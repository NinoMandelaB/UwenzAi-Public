"""
Recorded site visits for the audience analytics view.

The public frontend pings ``POST /api/v1/analytics/visit/`` once per app open
(and when a background tab becomes visible again). That endpoint is the ONLY
writer of ``first_visit_at`` / ``last_visit_at`` / ``visit_country_code`` on
``CustomUser`` and ``GuestIdentity``, so those columns keep one meaning: the
public app was opened by this person. Logins, token refreshes, chat activity
and the separate admin panel never touch them.

Semantics pinned here:

* ``first_visit_at`` is set once (first come wins) and never rewritten.
* ``last_visit_at`` is coalesced: a ping inside VISIT_LAST_SEEN_REFRESH_SECONDS
  of the stored value does not write, so a chatty client costs nothing.
* ``visit_country_code`` is the country of the LATEST recorded visit, taken
  from the trusted edge's country header (Cloudflare CF-IPCountry). Country
  level only; no IP is stored for accounts, and guests keep their existing
  GuestTokenQuota IP for abuse control — this field is independent of it.

History is not invented: people who only ever used the site before this
endpoint existed keep ``first_visit_at`` null, and the admin audience view
reports that honestly as "unknown" rather than guessing from account dates.
"""

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from capp.models import CustomUser, GuestIdentity

logger = logging.getLogger(__name__)

# Strict ISO-3166 alpha-2. Cloudflare's own "unknown" markers (XX for no
# geo match, T1 for Tor) are rejected too — they are not countries.
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_EDGE_UNKNOWN_COUNTRIES = {"XX", "T1"}


class VisitService:
    @staticmethod
    def _country_from_request(request) -> str | None:
        """The visitor's country from the configured edge header, if trustworthy.

        The header name comes from settings so it can be disabled per
        deployment (empty string) — e.g. if the origin is ever reachable
        without the edge, clients could forge it. Values are validated as
        strict two-letter country codes; anything else is treated as unknown
        rather than stored.
        """
        header = getattr(settings, "VISIT_COUNTRY_HEADER", None)
        if not header:
            return None
        raw = (request.META.get(header) or "").strip().upper()
        if not _COUNTRY_RE.match(raw) or raw in _EDGE_UNKNOWN_COUNTRIES:
            return None
        return raw

    @classmethod
    def _updates_for(cls, *, first_visit_at, last_visit_at, stored_country, request) -> dict:
        """New field values for a principal's row, honouring the coalesce rules.

        Returns only the fields that need writing (empty dict = no write).
        """
        now = timezone.now()
        staleness = timedelta(seconds=settings.VISIT_LAST_SEEN_REFRESH_SECONDS)
        updates = {}

        if first_visit_at is None:
            updates["first_visit_at"] = now

        if last_visit_at is None or now - last_visit_at >= staleness:
            updates["last_visit_at"] = now

        country = cls._country_from_request(request)
        # Keep the latest visit's country, but only when the edge actually
        # reported one — a header-less request never erases a known country.
        if country and country != stored_country:
            updates["visit_country_code"] = country

        return updates

    @classmethod
    def record_for_user(cls, user: CustomUser, request) -> bool:
        updates = cls._updates_for(
            first_visit_at=user.first_visit_at,
            last_visit_at=user.last_visit_at,
            stored_country=user.visit_country_code,
            request=request,
        )
        if not updates:
            return False
        CustomUser.objects.filter(pk=user.pk).update(**updates)
        return True

    @classmethod
    def record_for_guest(cls, identity: GuestIdentity, request) -> bool:
        updates = cls._updates_for(
            first_visit_at=identity.first_visit_at,
            last_visit_at=identity.last_visit_at,
            stored_country=identity.visit_country_code,
            request=request,
        )
        if not updates:
            return False
        GuestIdentity.objects.filter(pk=identity.pk).update(**updates)
        return True

    @classmethod
    def record(cls, request) -> bool:
        """Record one visit for whoever the request resolves to. Never raises.

        Returns whether a row was written (used only for debug logging —
        callers must treat a silent no-op as success so the frontend ping is
        fire-and-forget).
        """
        try:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                # Staff signed into the public app are still people visiting
                # www.uwenzai.com — record them like anyone else. The separate
                # admin panel never calls this endpoint, so its traffic is
                # excluded by construction.
                return cls.record_for_user(user, request)

            from capp.classes.guest.guest_identity_service import GuestIdentityService

            identity = GuestIdentityService.resolve(request)
            if identity is None:
                # No cookie, or expired/revoked/claimed. Nothing to attribute
                # the visit to — the frontend mints a session before pinging,
                # so this is not the public app's normal path.
                return False
            return cls.record_for_guest(identity, request)
        except Exception:
            logger.exception("Visit recording failed — visit dropped, caller unaffected")
            return False
