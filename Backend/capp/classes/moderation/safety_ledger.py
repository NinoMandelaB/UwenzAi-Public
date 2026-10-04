# capp/classes/moderation/safety_ledger.py
"""
Cross-session safety ledger: records classified messages, scores them, and
escalates.

Design (see PR for the full rationale):

* The deterministic classifier (``text_safety_policy.py``) decides WHAT
  happened; this module decides what it MEANS for the account.
* Events are per-user, cross-conversation: a violation in chat A followed by
  another in chat B still accumulates.
* Scoring is weighted by severity, not a flat count: ten low-weight signals
  are not ten critical attempts. Weights: critical=10, high=3, medium=2,
  low=1; the lock threshold is configurable (default 10). Locking always
  requires CORROBORATION — an unreversed prior violation of any severity
  inside the decay window, or a prior concern signal that aggravated this
  violation (the cross-session case) — and then happens either on a
  CRITICAL severity (CSAM, violence planning, trafficking) or when the
  accumulated score reaches the threshold. A first, uncorroborated
  violation of any severity — including a single lexical critical — blocks
  with a warning and leaves the account active: one false positive must
  never lock an account on its own (audit 2, recommendation 4).
* Self-harm is a CARE category: events are recorded for support context but
  are excluded from every punitive computation, and care messages are
  never blocked (the companion answers them empathetically under the
  safety-sandwich rules).
* Weapons-interest concerns are recorded but never blocked; they aggravate a
  later HIGH-severity violation to CRITICAL — the "asked about a weapon in
  one chat, planned violence in another" case, without ever treating a bare
  weapons question as a violation on its own.
* Events decay: only unreversed events inside the decay window (default
  180 days) count toward the score. Rows are kept for audit beyond that.
* Guests have no account to lock: outright violations are simply blocked,
  nothing is recorded.
* Enforcement boundary: the screen runs in ``api_send_message`` /
  ``api_generate_chat_image`` BEFORE storage, billing, prompt building or
  the LLM call, and a safety-locked account is refused there even if the
  WebSocket from before the lock is still open.

Concurrency: event creation, score recomputation and the account lock run
inside one transaction holding the user row (``select_for_update``), so two
parallel chats cannot both pass the threshold check.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Max, Min, Q, Sum, Value, When
from django.utils import timezone

from capp.classes.moderation.image_prompt_policy import _canonical_source, _separator_elided
from capp.classes.moderation.model_refusal import (
    CATEGORY_MODEL_REFUSAL,
    MODEL_REFUSAL_DETECTOR_VERSION,
    REFUSAL_MARKER,
)
from capp.classes.moderation.text_safety_policy import (
    CATEGORY_SELF_HARM,
    CATEGORY_WEAPONS_CONTEXT,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SafetyDecision,
    TextSafetyPolicy,
)
from capp.models import AdminActionLog, CustomUser, SafetyEvent

logger = logging.getLogger(__name__)

# Weight of one event toward the account score, by severity.
SEVERITY_WEIGHTS = {
    "critical": 10,
    "high": 3,
    "medium": 2,
    "low": 1,
}

# Concern categories whose events never add to the score themselves.
_NON_SCORING_CATEGORIES = (CATEGORY_WEAPONS_CONTEXT,)

# Advisory categories: recorded signals the MODEL produced itself (it refused
# a request, see capp/classes/moderation/model_refusal.py). A model is not a
# classifier — it over- and under-refuses — so these rows are shown to admins
# and can drive a SUGGESTION to block, but they never score, never count as a
# prior violation and never aggravate a later violation. Deliberately separate
# from _NON_SCORING_CATEGORIES, which doubles as the aggravation set.
_ADVISORY_CATEGORIES = (CATEGORY_MODEL_REFUSAL,)

# Photo-moderation blocks: the screening layer refused a child-safety image
# (see capp/classes/moderation/photo_moderation.py). Recorded through
# record_photo_block so repeat uploaders are visible in the ledger without
# importing the text policy's instant-lock semantics.
CATEGORY_PHOTO_CHILD_SAFETY = "photo_child_safety"

# Deployment modes for the ledger (SAFETY_LEDGER_MODE):
#
# * "off"      — no evaluation at all: nothing is blocked, nothing recorded.
#                Emergency stop.
# * "shadow"    — evaluate and record, never act: violations and concerns are
#                written with action=SHADOWED and the message is allowed
#                through to the model-level backstop. Shadow events NEVER
#                count toward a score, never aggravate, never block a lock
#                reversal and are not deduplicated against, so flipping the
#                mode to "enforce" later does not retroactively punish
#                anything measured in shadow. Use this to measure the live
#                false-positive rate before enforcing.
# * "enforce"  — the design behaviour: block, warn, lock.
#
# The default is "shadow": a new punitive system ships measuring, not
# punishing, and promotion to enforcement is a deliberate env change.
# Admin Users list: total (non-advisory) events at which an account is flagged
# for review even when none of its events is high priority.
ATTENTION_EVENT_THRESHOLD = 10
ATTENTION_LEVEL_NONE = "none"
ATTENTION_LEVEL_EVENTS = "events"
ATTENTION_LEVEL_REVIEW = "review"
ATTENTION_LEVEL_PRIORITY = "priority"

LEDGER_MODES = ("off", "shadow", "enforce")
DEFAULT_LEDGER_MODE = "shadow"


def _ledger_mode() -> str:
    mode = str(getattr(settings, "SAFETY_LEDGER_MODE", DEFAULT_LEDGER_MODE) or DEFAULT_LEDGER_MODE).strip().lower()
    return mode if mode in LEDGER_MODES else DEFAULT_LEDGER_MODE


def ledger_mode() -> str:
    """Public read of the current ledger mode for views and templates."""
    return _ledger_mode()


BLOCKED_CODE = "message_safety_blocked"
LOCKED_CODE = "account_safety_locked"

_COPY = {
    "en": {
        "warned": (
            "We blocked this message because it appears to request or describe content that is "
            "not allowed on UwenzAi. This is a warning: further violations will lock your account "
            "until our team reviews it. If you believe this was a mistake, please contact support."
        ),
        "blocked": (
            "We blocked this message because it appears to request or describe content that is "
            "not allowed on UwenzAi. Further violations will lock your account until our team "
            "reviews it."
        ),
        "locked": (
            "Your account has been locked for repeated or serious safety violations. You can "
            "still sign in and contact support to appeal; an administrator will review your "
            "account."
        ),
    },
    "sw": {
        "warned": (
            "Tulizuia ujumbe huu kwa sababu unaonekana kuomba au kuelezea maudhui yasiyoruhusiwa "
            "kwenye UwenzAi. Hili ni onyo: ukiendelea, akaunti yako itafungwa hadi timu yetu "
            "iipitie. Kama unadhani hili ni kosa, tafadhali wasiliana na usaidizi."
        ),
        "blocked": (
            "Tulizuia ujumbe huu kwa sababu unaonekana kuomba au kuelezea maudhui yasiyoruhusiwa "
            "kwenye UwenzAi. Ukiendelea, akaunti yako itafungwa hadi timu yetu iipitie."
        ),
        "locked": (
            "Akaunti yako imefungwa kwa sababu ya kuvunja sheria za usalama mara kwa mara au kwa "
            "njia kali. Bado unaweza kuingia na kuwasiliana na usaidizi kwa rufaa; msimamizi "
            "ataikagua akaunti yako."
        ),
    },
}


def _copy_for(action_key: str, language: Optional[str]) -> str:
    lang = (language or "en")[:2].lower()
    table = _COPY["sw"] if lang in ("sw", "sh") else _COPY["en"]
    return table.get(action_key, table["blocked"])


def blocked_copy_for(language: Optional[str]) -> str:
    """Localised copy for a blocked message (guest path / fallback)."""
    return _copy_for("blocked", language)


def locked_copy_for(language: Optional[str]) -> str:
    """Localised copy shown to a safety-locked account."""
    return _copy_for("locked", language)


def fingerprint_text(text: str) -> str:
    """Irreversible keyed fingerprint of a message, for deduplication only.

    The leet-mapped, separator-elided normal form is hashed so trivial
    re-spellings of the same violation deduplicate. This is NOT message
    content and cannot be reversed to the text.
    """
    canonical = _separator_elided(_canonical_source(text or "", map_leet=True))
    key = (settings.SECRET_KEY or "").encode("utf-8")
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SafetyScreenResult:
    """Outcome of screening one message. Contains no message text."""

    decision: SafetyDecision
    blocked: bool
    action: str
    code: Optional[str] = None
    message: Optional[str] = None
    event_id: Optional[str] = None


class SafetyLedgerService:
    @classmethod
    def screen_message(
        cls,
        *,
        user: CustomUser,
        conversation_id: Optional[str],
        text: str,
        language: Optional[str] = None,
    ) -> Optional[SafetyScreenResult]:
        """Screen one authenticated-user message before it is stored or billed.

        Returns ``None`` when nothing applies (the common case — pass
        through untouched), a non-blocking result for care/concern messages
        (event recorded, message allowed), and a blocking result for
        violations. The caller must refuse the send when ``blocked``.
        """
        if _ledger_mode() == "off":
            return None

        decision = TextSafetyPolicy.evaluate(text)
        if decision is None:
            return None

        if decision.is_violation and decision.also_care:
            # A violation that ALSO carries self-harm crisis language
            # ("I will kill everyone at school and then myself"): the
            # violation is enforced below, and a care event is recorded
            # next to it so support context exists (audit 2, M1). The
            # fingerprint dedup keeps a re-sent message from stacking care
            # rows alongside the violation dedup.
            fingerprint = fingerprint_text(text)
            dedup_start = timezone.now() - timedelta(minutes=settings.SAFETY_DEDUP_WINDOW_MINUTES)
            already_recorded = SafetyEvent.objects.filter(
                user_id=user.pk,
                fingerprint=fingerprint,
                is_reversed=False,
                created_at__gte=dedup_start,
            ).exists()
            if not already_recorded:
                SafetyEvent.objects.create(
                    user_id=user.pk,
                    conversation_id=conversation_id,
                    category=CATEGORY_SELF_HARM,
                    severity=SEVERITY_HIGH,
                    action=SafetyEvent.Action.LOGGED,
                    is_care=True,
                    confidence=decision.confidence,
                    classifier_version=decision.classifier_version,
                    fingerprint=fingerprint,
                )
                logger.info(
                    "Safety ledger: care context recorded alongside violation %s for user=%s",
                    decision.category,
                    user.pk,
                )

        if decision.is_care:
            # Care: record for support context; the message is answered by
            # the companion under the safety-sandwich rules, never blocked.
            # Care events are support telemetry, not enforcement, so they
            # are recorded in every mode except "off".
            event = SafetyEvent.objects.create(
                user_id=user.pk,
                conversation_id=conversation_id,
                category=decision.category,
                severity=decision.severity,
                action=SafetyEvent.Action.LOGGED,
                is_care=True,
                confidence=decision.confidence,
                classifier_version=decision.classifier_version,
                fingerprint=fingerprint_text(text),
            )
            logger.info(
                "Safety ledger: care event %s recorded for user=%s",
                event.category,
                user.pk,
            )
            return SafetyScreenResult(
                decision=decision,
                blocked=False,
                action=SafetyEvent.Action.LOGGED,
                event_id=str(event.event_id),
            )

        if decision.is_concern:
            fingerprint = fingerprint_text(text)
            dedup_start = timezone.now() - timedelta(minutes=settings.SAFETY_DEDUP_WINDOW_MINUTES)
            already_recorded = SafetyEvent.objects.filter(
                user_id=user.pk,
                fingerprint=fingerprint,
                is_reversed=False,
                created_at__gte=dedup_start,
            ).exists()
            if not already_recorded:
                # In shadow mode a concern is recorded as SHADOWED so it can
                # never aggravate a later violation once enforcement begins.
                action = SafetyEvent.Action.SHADOWED if _ledger_mode() == "shadow" else SafetyEvent.Action.LOGGED
                event = SafetyEvent.objects.create(
                    user_id=user.pk,
                    conversation_id=conversation_id,
                    category=decision.category,
                    severity=decision.severity,
                    action=action,
                    confidence=decision.confidence,
                    classifier_version=decision.classifier_version,
                    fingerprint=fingerprint,
                )
                logger.info(
                    "Safety ledger: concern event %s recorded for user=%s (action=%s)",
                    event.category,
                    user.pk,
                    action,
                )
            return SafetyScreenResult(
                decision=decision,
                blocked=False,
                action=SafetyEvent.Action.LOGGED,
            )

        return cls._record_violation(
            user=user,
            conversation_id=conversation_id,
            text=text,
            decision=decision,
            language=language,
        )

    # ------------------------------------------------------------------
    # Violations
    # ------------------------------------------------------------------

    @classmethod
    def _record_violation(
        cls,
        *,
        user: CustomUser,
        conversation_id: Optional[str],
        text: str,
        decision: SafetyDecision,
        language: Optional[str],
    ) -> SafetyScreenResult:
        if _ledger_mode() == "shadow":
            # Shadow: record what the classifier saw, never act on it. The
            # message passes through to the model-level backstop, and the
            # event can never count later (see the mode notes above).
            # Fingerprint-deduplicated like concerns, so one user re-sending
            # the same flagged message cannot flood the shadow rows
            # (audit 2, L3).
            fingerprint = fingerprint_text(text)
            dedup_start = timezone.now() - timedelta(minutes=settings.SAFETY_DEDUP_WINDOW_MINUTES)
            already_recorded = SafetyEvent.objects.filter(
                user_id=user.pk,
                fingerprint=fingerprint,
                is_reversed=False,
                created_at__gte=dedup_start,
            ).exists()
            if not already_recorded:
                event = SafetyEvent.objects.create(
                    user_id=user.pk,
                    conversation_id=conversation_id,
                    category=decision.category,
                    severity=decision.severity,
                    action=SafetyEvent.Action.SHADOWED,
                    confidence=decision.confidence,
                    classifier_version=decision.classifier_version,
                    fingerprint=fingerprint,
                )
                logger.info(
                    "Safety ledger: shadow event %s recorded for user=%s (not enforced)",
                    event.category,
                    user.pk,
                )
            return SafetyScreenResult(
                decision=decision,
                blocked=False,
                action=SafetyEvent.Action.SHADOWED,
            )

        lock_threshold = settings.SAFETY_LOCK_SCORE_THRESHOLD
        decay_days = settings.SAFETY_EVENT_DECAY_DAYS
        dedup_minutes = settings.SAFETY_DEDUP_WINDOW_MINUTES

        with transaction.atomic():
            # Hold the user row so parallel sends cannot both pass the
            # threshold check, and re-check the status inside the lock —
            # another request may have locked the account a moment ago.
            locked_user = CustomUser.objects.select_for_update().get(pk=user.pk)
            if locked_user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED:
                return SafetyScreenResult(
                    decision=decision,
                    blocked=True,
                    action=SafetyEvent.Action.LOCKED,
                    code=LOCKED_CODE,
                    message=_copy_for("locked", language),
                )

            now = timezone.now()
            window_start = now - timedelta(days=decay_days)
            fingerprint = fingerprint_text(text)

            recent_duplicate = (
                SafetyEvent.objects.filter(
                    user_id=locked_user.pk,
                    fingerprint=fingerprint,
                    is_reversed=False,
                    created_at__gte=now - timedelta(minutes=dedup_minutes),
                )
                .exclude(is_care=True)
                .exclude(action=SafetyEvent.Action.SHADOWED)
                .exists()
            )
            if recent_duplicate:
                # Same message re-sent within the window: block again, but
                # do not stack another event — spam must not manufacture a
                # lock out of one message.
                return SafetyScreenResult(
                    decision=decision,
                    blocked=True,
                    action=SafetyEvent.Action.BLOCKED,
                    code=BLOCKED_CODE,
                    message=_copy_for("blocked", language),
                )

            prior_events = SafetyEvent.objects.filter(
                user_id=locked_user.pk,
                is_reversed=False,
                created_at__gte=window_start,
            ).exclude(action=SafetyEvent.Action.SHADOWED)

            # Aggravation: prior unreversed concern signals (e.g. weapons
            # interest in another chat) escalate a HIGH violation to CRITICAL.
            aggravated = False
            severity = decision.severity
            if severity == SEVERITY_HIGH and prior_events.filter(category__in=_NON_SCORING_CATEGORIES).exists():
                aggravated = True
                severity = SEVERITY_CRITICAL

            prior_violations = (
                prior_events.exclude(is_care=True)
                .exclude(category__in=_NON_SCORING_CATEGORIES)
                .exclude(category__in=_ADVISORY_CATEGORIES)
                .count()
            )

            action = SafetyEvent.Action.WARNED if prior_violations == 0 else SafetyEvent.Action.BLOCKED
            # Corroboration: an unreversed prior violation of ANY severity
            # inside the decay window, or a prior concern signal that
            # aggravated this violation (the cross-session case: weapons
            # interest in one chat, violence in another). Without it nothing
            # ever locks — a first, standalone violation of ANY severity
            # (including a single lexical critical, whose weight alone meets
            # the threshold) only blocks and warns (audit 2, recommendation 4).
            corroborated = prior_violations > 0 or aggravated

            event = SafetyEvent.objects.create(
                user_id=locked_user.pk,
                conversation_id=conversation_id,
                category=decision.category,
                severity=severity,
                action=action,
                confidence=decision.confidence,
                classifier_version=decision.classifier_version,
                fingerprint=fingerprint,
                was_aggravated=aggravated,
            )

            score = cls.active_score(locked_user.pk, now=now)
            should_lock = corroborated and (severity == SEVERITY_CRITICAL or score >= lock_threshold)
            if should_lock:
                action = SafetyEvent.Action.LOCKED
                event.action = action
                event.save(update_fields=["action", "updated_at"])
                cls._lock_account(
                    locked_user,
                    reason=f"Automatic safety lock: {decision.category} (score {score})",
                )
                return SafetyScreenResult(
                    decision=decision,
                    blocked=True,
                    action=action,
                    code=LOCKED_CODE,
                    message=_copy_for("locked", language),
                    event_id=str(event.event_id),
                )

            return SafetyScreenResult(
                decision=decision,
                blocked=True,
                action=action,
                code=BLOCKED_CODE,
                message=_copy_for("warned" if action == SafetyEvent.Action.WARNED else "blocked", language),
                event_id=str(event.event_id),
            )

    @classmethod
    def _lock_account(cls, user: CustomUser, *, reason: str) -> None:
        user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        # is_active stays True on purpose: a safety-locked user can still
        # sign in, read their status and appeal through support. Only
        # generation actions are refused.
        user.ban_reason = reason
        user.save(update_fields=["account_status", "ban_reason", "updated_at"])
        logger.warning("Safety ledger: account %s safety-locked (%s)", user.pk, reason)

    # ------------------------------------------------------------------
    # Scoring / admin surface
    # ------------------------------------------------------------------

    @classmethod
    def active_score(cls, user_id, *, now=None) -> int:
        """Weighted score of unreversed, non-care, non-concern events in window."""
        now = now or timezone.now()
        window_start = now - timedelta(days=settings.SAFETY_EVENT_DECAY_DAYS)
        events = (
            SafetyEvent.objects.filter(
                user_id=user_id,
                is_reversed=False,
                is_care=False,
                created_at__gte=window_start,
            )
            .exclude(category__in=_NON_SCORING_CATEGORIES)
            .exclude(category__in=_ADVISORY_CATEGORIES)
            .exclude(action=SafetyEvent.Action.SHADOWED)
        )
        return sum(SEVERITY_WEIGHTS.get(event.severity, 1) for event in events)

    @classmethod
    def has_critical_event(cls, user_id, *, now=None) -> bool:
        now = now or timezone.now()
        window_start = now - timedelta(days=settings.SAFETY_EVENT_DECAY_DAYS)
        return (
            SafetyEvent.objects.filter(
                user_id=user_id,
                is_reversed=False,
                is_care=False,
                severity=SEVERITY_CRITICAL,
                created_at__gte=window_start,
            )
            .exclude(action=SafetyEvent.Action.SHADOWED)
            .exists()
        )

    # ------------------------------------------------------------------
    # Advisory model refusals (the prompt-level backstop, never punitive)
    # ------------------------------------------------------------------

    @classmethod
    def record_model_refusal(
        cls,
        *,
        user: CustomUser,
        conversation_id: Optional[str],
        response_text: str,
        user_text: Optional[str] = None,
    ) -> Optional[SafetyEvent]:
        """Record that the companion model itself refused this user's request.

        Called by capp/tasks.py AFTER the refusal reply is durably stored and
        delivered — an advisory flag must never break or refund a chat turn,
        so the caller wraps this in its own error guard. The event is
        metadata-only like every ledger row: category, severity, detector
        version and an irreversible fingerprint of the reply (used solely so
        a redelivered task does not double-count one refusal).

        ``user_text`` is the user's own message for this turn. When it already
        contains the refusal marker the flag is skipped: the model merely
        echoed platform syntax the user typed, and counting that would let a
        user inflate their own advisory count on purpose (audit 2, L2).

        Advisory by construction: the row is LOGGED, never scored, never
        aggravating, never blocking. Recorded in every mode except "off"
        (same treatment as care events: it is support telemetry, and flipping
        shadow -> enforce does not change what refusals mean).
        """
        if _ledger_mode() == "off" or user is None or not response_text:
            return None

        if user_text and REFUSAL_MARKER.lower() in str(user_text).lower():
            return None

        fingerprint = fingerprint_text(response_text)
        dedup_start = timezone.now() - timedelta(minutes=settings.SAFETY_DEDUP_WINDOW_MINUTES)
        already_recorded = SafetyEvent.objects.filter(
            user_id=user.pk,
            category=CATEGORY_MODEL_REFUSAL,
            fingerprint=fingerprint,
            is_reversed=False,
            created_at__gte=dedup_start,
        ).exists()
        if already_recorded:
            return None

        event = SafetyEvent.objects.create(
            user_id=user.pk,
            conversation_id=conversation_id,
            category=CATEGORY_MODEL_REFUSAL,
            severity="low",
            action=SafetyEvent.Action.LOGGED,
            confidence=1.0,
            classifier_version=MODEL_REFUSAL_DETECTOR_VERSION,
            fingerprint=fingerprint,
        )
        logger.info(
            "Safety ledger: advisory model-refusal flag recorded for user=%s (never punitive)",
            user.pk,
        )
        return event

    @classmethod
    def model_refusal_count(cls, user_id, *, now=None) -> int:
        """Unreversed advisory refusals inside the decay window.

        Windowed on purpose: old advisory flags must not suggest blocking
        forever. Mirrors the window used by active_score so the admin panel
        presents one consistent notion of "current" signals.
        """
        now = now or timezone.now()
        window_start = now - timedelta(days=settings.SAFETY_EVENT_DECAY_DAYS)
        return SafetyEvent.objects.filter(
            user_id=user_id,
            category=CATEGORY_MODEL_REFUSAL,
            is_reversed=False,
            created_at__gte=window_start,
        ).count()

    # ------------------------------------------------------------------
    # Shadow-mode overview (admin review surface)
    # ------------------------------------------------------------------

    @classmethod
    def shadow_overview(
        cls,
        *,
        days: int = 30,
        category: Optional[str] = None,
        min_severity: Optional[str] = None,
        account_status: Optional[str] = None,
        sort: str = "score",
        offset: int = 0,
        limit: int = 20,
        now=None,
    ) -> dict:
        """Per-user roll-up of what the classifier recorded but did not enforce.

        Shadow mode records violations and concerns as ``action=SHADOWED``
        rows that never score, never lock and never block a message. This
        gives the administrator who decides what happens to an account one
        list to work from instead of opening every user.

        Counted: unreversed, non-care, non-advisory shadow rows inside the
        window, for accounts that still exist. Self-harm care events are
        excluded on purpose (support context, not a violation), and so are
        model refusals (advisory, shown separately).

        ``shadow_score`` is the severity-weighted sum that enforcement would
        use for scoring and exists to order the list. It is NOT a prediction
        that enforcement would lock the account: real locking also needs
        corroboration, which a shadow roll-up cannot reproduce.

        Metadata only: no message text, fingerprint or conversation id is
        read into the result.
        """
        now = now or timezone.now()
        window_start = now - timedelta(days=days)

        base = SafetyEvent.objects.filter(
            action=SafetyEvent.Action.SHADOWED,
            is_care=False,
            is_reversed=False,
            created_at__gte=window_start,
        ).exclude(category__in=_ADVISORY_CATEGORIES)

        users = CustomUser.objects.all()
        if account_status:
            users = users.filter(account_status=account_status)
        base = base.filter(user_id__in=users.values("user_id"))

        if category:
            base = base.filter(category=category)
        if min_severity:
            order = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL)
            if min_severity in order:
                floor = order.index(min_severity)
                base = base.filter(severity__in=order[floor:])

        weighted = Case(
            *[When(severity=severity, then=Value(weight)) for severity, weight in SEVERITY_WEIGHTS.items()],
            default=Value(1),
            output_field=IntegerField(),
        )
        scoring = Case(
            When(category__in=_NON_SCORING_CATEGORIES, then=Value(0)),
            default=weighted,
            output_field=IntegerField(),
        )

        per_user = (
            base.order_by()
            .values("user_id")
            .annotate(
                event_count=Count("event_id"),
                shadow_score=Sum(scoring),
                critical_count=Count("event_id", filter=Q(severity=SEVERITY_CRITICAL)),
                high_count=Count("event_id", filter=Q(severity=SEVERITY_HIGH)),
                first_event_at=Min("created_at"),
                last_event_at=Max("created_at"),
            )
        )

        orderings = {
            "score": ("-shadow_score", "-event_count", "-last_event_at", "user_id"),
            "count": ("-event_count", "-shadow_score", "-last_event_at", "user_id"),
            "recent": ("-last_event_at", "-shadow_score", "user_id"),
        }
        end = offset + limit
        ordered = per_user.order_by(*orderings.get(sort, orderings["score"]))
        rows = list(ordered[offset:end])
        user_ids = [row["user_id"] for row in rows]

        categories_by_user: dict = {}
        for item in (
            base.filter(user_id__in=user_ids).order_by().values("user_id", "category").annotate(n=Count("event_id"))
        ):
            categories_by_user.setdefault(item["user_id"], {})[item["category"]] = item["n"]

        decay_start = now - timedelta(days=settings.SAFETY_EVENT_DECAY_DAYS)
        refusals_by_user = {
            item["user_id"]: item["n"]
            for item in SafetyEvent.objects.filter(
                category=CATEGORY_MODEL_REFUSAL,
                is_reversed=False,
                created_at__gte=decay_start,
                user_id__in=user_ids,
            )
            .order_by()
            .values("user_id")
            .annotate(n=Count("event_id"))
        }

        threshold = settings.SAFETY_LOCK_SCORE_THRESHOLD
        summary = {
            "window_days": days,
            "lock_threshold": threshold,
            "total_users": per_user.count(),
            "total_events": base.count(),
            "users_over_threshold": per_user.filter(shadow_score__gte=threshold).count(),
            "users_with_critical": per_user.filter(critical_count__gt=0).count(),
            "by_category": {
                item["category"]: item["n"] for item in base.order_by().values("category").annotate(n=Count("event_id"))
            },
            "by_severity": {
                item["severity"]: item["n"] for item in base.order_by().values("severity").annotate(n=Count("event_id"))
            },
        }

        for row in rows:
            row["categories"] = categories_by_user.get(row["user_id"], {})
            row["model_refusal_count"] = refusals_by_user.get(row["user_id"], 0)
            row["shadow_score"] = row["shadow_score"] or 0
            row["exceeds_threshold"] = row["shadow_score"] >= threshold
        return {"summary": summary, "rows": rows, "total": summary["total_users"]}

    @classmethod
    def record_photo_block(
        cls,
        *,
        user: CustomUser,
        conversation_id=None,
        sha256: str,
        provider: str,
    ) -> Optional[SafetyEvent]:
        """Record a blocked child-safety photo as a safety event.

        The photo moderation layer blocks the upload on its own; this writes
        the account-level signal so a REPEATED uploader is visible next to the
        text violations and accumulates toward the lock threshold instead of
        disappearing into the photo-screening audit table.

        Deliberately narrower than the text path (audit F4): the screening
        model can false-positive, so one photo block never locks an account
        and never escalates severity. A HIGH event is worth 3 points; the lock
        threshold and corroboration rules are unchanged, and the lock itself
        can only ever come from a text violation or an administrator.

        Shadow mode records SHADOWED (measured, never scoring); enforce mode
        records BLOCKED, which scores. "off" records nothing, exactly like
        the text path. The image sha256 doubles as the dedup fingerprint: the
        same photo re-uploaded within SAFETY_DEDUP_WINDOW_MINUTES does not
        stack events.
        """
        if user is None or _ledger_mode() == "off":
            return None

        fingerprint = (sha256 or "")[:64]
        dedup_start = timezone.now() - timedelta(minutes=settings.SAFETY_DEDUP_WINDOW_MINUTES)
        already_recorded = SafetyEvent.objects.filter(
            user_id=user.pk,
            fingerprint=fingerprint,
            is_reversed=False,
            created_at__gte=dedup_start,
        ).exists()
        if already_recorded:
            return None

        action = SafetyEvent.Action.SHADOWED if _ledger_mode() == "shadow" else SafetyEvent.Action.BLOCKED
        event = SafetyEvent.objects.create(
            user_id=user.pk,
            conversation_id=conversation_id,
            category=CATEGORY_PHOTO_CHILD_SAFETY,
            severity=SEVERITY_HIGH,
            action=action,
            is_care=False,
            confidence=1.0,
            classifier_version=f"photo:{provider}"[:40],
            fingerprint=fingerprint,
        )
        logger.info(
            "Safety ledger: blocked photo recorded for user=%s (action=%s, provider=%s)",
            user.pk,
            action,
            provider,
        )
        return event

    @classmethod
    def attention_summaries(cls, user_ids) -> dict:
        """At-a-glance review flag per user, for the admin Users list.

        Lets an administrator see which accounts need a look without opening
        every profile. Counts unreversed adverse AND care events of every
        severity; advisory model refusals are left out (shown separately on
        the profile). Reversed events are resolved false positives and do not
        count.

        Per user: ``event_count`` (all counted events), ``priority_count``
        (care events plus critical/high severity events) and a ``level``:

        * ``priority`` — at least one care event or critical/high event;
        * ``review``   — no priority event, but ``ATTENTION_EVENT_THRESHOLD``
                         or more events in total;
        * ``events``   — some lower-severity events below the threshold;
        * ``none``     — nothing recorded.

        Metadata only: counts and a level, never categories, text,
        fingerprints or conversation ids. One grouped query for the whole
        page of users. Every requested id gets an entry.
        """
        ids = list(user_ids)
        summaries = {user_id: {"event_count": 0, "priority_count": 0, "level": ATTENTION_LEVEL_NONE} for user_id in ids}
        if not ids:
            return summaries

        priority = Q(is_care=True) | Q(severity__in=(SEVERITY_CRITICAL, SEVERITY_HIGH))
        rows = (
            SafetyEvent.objects.filter(user_id__in=ids, is_reversed=False)
            .exclude(category__in=_ADVISORY_CATEGORIES)
            .order_by()
            .values("user_id")
            .annotate(event_count=Count("event_id"), priority_count=Count("event_id", filter=priority))
        )
        for row in rows:
            event_count = row["event_count"]
            priority_count = row["priority_count"]
            if priority_count > 0:
                level = ATTENTION_LEVEL_PRIORITY
            elif event_count >= ATTENTION_EVENT_THRESHOLD:
                level = ATTENTION_LEVEL_REVIEW
            else:
                level = ATTENTION_LEVEL_EVENTS
            summaries[row["user_id"]] = {
                "event_count": event_count,
                "priority_count": priority_count,
                "level": level,
            }
        return summaries

    @classmethod
    def has_priority_events(cls, user_id) -> bool:
        """Whether ONE account currently warrants priority attention.

        The single-user form of attention_summaries' "priority" level (the
        admin Users list flag): at least one unreversed, non-advisory event
        that is either a care event or of critical/high severity. Used by the
        admin User detail page to offer direct outreach to the user.

        Metadata only: a boolean, nothing else.
        """
        return (
            SafetyEvent.objects.filter(
                user_id=user_id,
                is_reversed=False,
            )
            .exclude(category__in=_ADVISORY_CATEGORIES)
            .filter(Q(is_care=True) | Q(severity__in=(SEVERITY_CRITICAL, SEVERITY_HIGH)))
            .exists()
        )

    @classmethod
    def reset_model_refusals(cls, *, user: CustomUser, admin_user, reason: str) -> dict:
        """Soft-reverse ALL unreversed advisory refusal flags for one account.

        Product rule: refusal flags are never deleted by anything a user can
        do — not memory clears, message/conversation deletion, or account
        erasure — and the effective count only ever changes through this
        admin action (or the per-event reversal endpoint for selective
        review). Reversal is an annotation, never a deletion: the audit
        trail of what was recorded stays queryable, and the reset itself is
        logged to AdminActionLog with the affected count and the admin's
        reason. Idempotent in effect: an account with no unreversed
        refusals simply reverses zero rows and still logs the attempt.
        """
        now = timezone.now()
        with transaction.atomic():
            locked_user = CustomUser.objects.select_for_update().get(pk=user.pk)
            events = list(
                SafetyEvent.objects.select_for_update().filter(
                    user_id=locked_user.pk,
                    category=CATEGORY_MODEL_REFUSAL,
                    is_reversed=False,
                )
            )
            reversed_at = now
            for event in events:
                event.is_reversed = True
                event.reversed_at = reversed_at
                event.reversed_by = admin_user.pk if admin_user else None
                event.reversal_reason = reason
                event.updated_at = now
            SafetyEvent.objects.bulk_update(
                events,
                ["is_reversed", "reversed_at", "reversed_by", "reversal_reason", "updated_at"],
            )

            AdminActionLog.objects.create(
                admin_user=admin_user,
                action_type=AdminActionLog.ActionType.SAFETY_MODEL_REFUSALS_RESET,
                target_user=locked_user,
                reason=reason,
                metadata={
                    "reversed_count": len(events),
                    "category": CATEGORY_MODEL_REFUSAL,
                },
            )
            return {
                "user": locked_user,
                "reversed_count": len(events),
                "remaining_count": cls.model_refusal_count(locked_user.pk, now=now),
            }

    @classmethod
    def reverse_event(cls, *, event: SafetyEvent, admin_user, reason: str) -> dict:
        """Soft-reverse one event and recompute the account state.

        If the account is safety-locked and the reversal drops the score
        below the lock threshold with no unreversed critical events left,
        the account is unlocked automatically — reversing the event that
        caused the lock should also undo the lock it caused. Locks held by
        a remaining critical event always require the explicit unlock.
        """
        if event.is_reversed:
            return {"event": event, "unlocked": False, "already_reversed": True}

        with transaction.atomic():
            locked_user = CustomUser.objects.select_for_update().filter(pk=event.user_id).first()
            event.is_reversed = True
            event.reversed_at = timezone.now()
            event.reversed_by = admin_user.pk if admin_user else None
            event.reversal_reason = reason
            event.save(
                update_fields=[
                    "is_reversed",
                    "reversed_at",
                    "reversed_by",
                    "reversal_reason",
                    "updated_at",
                ]
            )

            unlocked = False
            if (
                locked_user is not None
                and locked_user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED
                and cls.active_score(locked_user.pk) < settings.SAFETY_LOCK_SCORE_THRESHOLD
                and not cls.has_critical_event(locked_user.pk)
            ):
                cls._unlock(locked_user, admin_user=admin_user, reason=reason, automatic=True)
                unlocked = True

            AdminActionLog.objects.create(
                admin_user=admin_user,
                action_type=AdminActionLog.ActionType.SAFETY_EVENT_REVERSE,
                target_user=locked_user,
                reason=reason,
                metadata={
                    "event_id": str(event.event_id),
                    "category": event.category,
                    "severity": event.severity,
                    "unlocked_account": unlocked,
                },
            )
            return {"event": event, "unlocked": unlocked, "already_reversed": False}

    @classmethod
    def unlock_account(cls, *, user: CustomUser, admin_user, reason: str) -> CustomUser:
        """Explicit admin unlock of a safety-locked account."""
        if user.account_status != CustomUser.AccountStatus.SAFETY_LOCKED:
            return user
        with transaction.atomic():
            locked_user = CustomUser.objects.select_for_update().get(pk=user.pk)
            if locked_user.account_status != CustomUser.AccountStatus.SAFETY_LOCKED:
                return locked_user
            cls._unlock(locked_user, admin_user=admin_user, reason=reason, automatic=False)
            return locked_user

    @classmethod
    def _unlock(cls, user: CustomUser, *, admin_user, reason: str, automatic: bool) -> None:
        user.account_status = CustomUser.AccountStatus.ACTIVE
        user.ban_reason = ""
        user.save(update_fields=["account_status", "ban_reason", "updated_at"])
        AdminActionLog.objects.create(
            admin_user=admin_user,
            action_type=AdminActionLog.ActionType.SAFETY_ACCOUNT_UNLOCK,
            target_user=user,
            reason=reason,
            metadata={"automatic": automatic},
        )
        logger.info(
            "Safety ledger: account %s unlocked by %s (automatic=%s)",
            user.pk,
            getattr(admin_user, "pk", None),
            automatic,
        )
