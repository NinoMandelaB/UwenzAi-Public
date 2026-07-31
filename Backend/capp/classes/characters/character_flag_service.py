# capp/classes/characters/character_flag_service.py
import logging
from django.db import IntegrityError
from django.db.models import Count, OuterRef, Subquery
from capp.models import CharacterFlag, CustomUser, Character, SupportTicket
from capp.classes.support.support_service import SupportService

logger = logging.getLogger(__name__)


class CharacterFlagService:

    @classmethod
    def flag_character(
        cls,
        character: Character,
        reason: str,
        user: CustomUser = None,
        guest_session_id: str = None,
    ) -> dict:
        """
        Create a flag for a character. Returns success/fail status.
        Enforces one flag per user or guest session per character.
        """
        if not reason or not reason.strip():
            return {"status": "fail", "data": "reason_required"}

        try:
            flag = CharacterFlag.objects.create(
                character=character,
                reported_by=user,
                guest_session_id=guest_session_id,
                reason=reason.strip(),
            )
            user_display = user.username if user else f"guest:{guest_session_id[:8]}"  # noqa: E231
            logger.info(
                "Character flagged: character_id=%s, reported_by=%s",
                character.character_id,
                user_display,
            )

            # ── Auto‑create ticket for authenticated users ──
            if user and user.is_authenticated:
                cls._maybe_create_ticket_for_character_flag(flag)

            return {"status": "success", "data": {"flag_id": str(flag.flag_id)}}
        except IntegrityError:
            return {"status": "fail", "data": "already_flagged"}

    @classmethod
    def _maybe_create_ticket_for_character_flag(cls, flag: CharacterFlag) -> None:
        """Create a support ticket for an authenticated user's character flag."""
        if not flag.reported_by:
            return

        # Check for existing open ticket linked to this flag
        existing = SupportTicket.objects.filter(
            metadata__flag_id=str(flag.flag_id),
            status__in=[
                SupportTicket.Status.OPEN,
                SupportTicket.Status.IN_PROGRESS,
            ],
        ).exists()

        if existing:
            logger.info(
                "Ticket already exists for character flag %s, skipping",
                flag.flag_id,
            )
            return

        character = flag.character

        title = f"Reported character: {character.name}"

        description = (
            f"Flagged Character:\n"  # noqa: E231
            f"{character.name}\n\n"  # noqa: E231
            f"Reason: {flag.reason}\n\n"  # noqa: E231
            f"Flagged by: {flag.reported_by.get_display_name()} "  # noqa: E231
            f"({flag.reported_by.username})\n"  # noqa: E231
            f"Character ID: {character.character_id}\n"  # noqa: E231
            f"Character Slug: {character.slug}\n"  # noqa: E231
            f"Created by: "  # noqa: E231
            f"{character.created_by.get_display_name() if character.created_by else 'Unknown'}"
        )

        try:

            result = SupportService.create_ticket(
                user=flag.reported_by,
                title=title,
                description=description,
                priority="medium",
            )

            if result["status"] == "success":
                ticket_data = result["data"]["ticket"]
                ticket_id = ticket_data["ticket_id"]

                ticket = SupportTicket.objects.get(ticket_id=ticket_id)
                ticket.metadata["flag_id"] = str(flag.flag_id)
                ticket.save(update_fields=["metadata"])

                logger.info(
                    "Auto-created ticket %s from character flag %s",
                    ticket_id,
                    flag.flag_id,
                )

        except Exception:
            logger.exception(
                "Failed to create ticket for character flag %s",
                flag.flag_id,
            )

    @classmethod
    def list_flagged_characters(cls) -> list:
        """
        Return characters that have been flagged, annotated with flag_count
        and the latest flag reason. For admin use.
        """
        latest_reason = (
            CharacterFlag.objects.filter(character=OuterRef("pk")).order_by("-created_at")[:1].values("reason")
        )

        flagged = (
            Character.objects.annotate(
                flag_count=Count("character_flags"),
                latest_flag_reason=Subquery(latest_reason),
            )
            .filter(flag_count__gt=0)
            .order_by("-flag_count")
        )

        return flagged

    @classmethod
    def clear_character_flags(cls, character: Character, admin_user: CustomUser = None) -> dict:
        """
        Delete all flag records for a given character.
        Returns success/fail status.
        Admin-only operation (permission should be checked by the caller).
        """
        try:
            flag_count = character.character_flags.count()
            if flag_count == 0:
                return {"status": "fail", "data": "no_flags_found"}

            deleted, _ = character.character_flags.all().delete()

            logger.info(
                "Cleared %d flags for character %s by admin %s",
                deleted,
                character.character_id,
                admin_user.user_id if admin_user else "unknown",
            )
            return {"status": "success", "data": f"cleared_{deleted}_flags"}
        except Exception:
            logger.exception("Error clearing flags for character %s", character.character_id)
            return {"status": "fail", "data": "server_error"}
