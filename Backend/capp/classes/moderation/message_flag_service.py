# capp/classes/message_flag_service.py
import logging
from typing import Dict, Any, Optional
from django.utils import timezone
from capp.models import MessageFlag, Message, SupportTicket

logger = logging.getLogger(__name__)


class MessageFlagService:

    @classmethod
    def submit_flag(
        cls,
        message_id: str,
        reason: str,
        user=None,
        guest_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not reason or not reason.strip():
            return {"status": "fail", "data": "reason_required"}

        try:
            message = Message.objects.select_related("conversation__character").get(message_id=message_id)
        except Message.DoesNotExist:
            return {"status": "fail", "data": "message_not_found"}

        # Prevent duplicate flagging
        if user and user.is_authenticated:
            if MessageFlag.objects.filter(message=message, reported_by=user).exists():
                return {"status": "fail", "data": "already_flagged"}
        elif guest_session_id:
            if MessageFlag.objects.filter(message=message, guest_session_id=guest_session_id).exists():
                return {"status": "fail", "data": "already_flagged"}
        else:
            return {"status": "fail", "data": "authentication_required"}

        # Create the flag
        flag = MessageFlag.objects.create(
            message=message,
            reported_by=user if (user and user.is_authenticated) else None,
            guest_session_id=(guest_session_id if not user or not user.is_authenticated else None),
            reason=reason.strip(),
        )

        logger.info(
            "Message flagged message_id=%s by=%s",
            message_id,
            user.username if user else guest_session_id,
        )

        # ── Auto‑create ticket for authenticated users ──
        if user and user.is_authenticated:
            cls._maybe_create_ticket_for_flag(flag)

        return {"status": "success", "data": "message_flagged"}

    @classmethod
    def _maybe_create_ticket_for_flag(cls, flag: MessageFlag) -> None:
        """Create a support ticket for an authenticated user's flag, with the flag details as the initial message."""
        if not flag.reported_by:
            return

        # Check for existing open ticket linked to this flag
        existing = SupportTicket.objects.filter(
            metadata__flag_id=str(flag.flag_id),
            status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS],
        ).exists()
        if existing:
            logger.info("Ticket already exists for flag %s, skipping", flag.flag_id)
            return

        character_name = (
            flag.message.conversation.character.name if flag.message.conversation.character else "unknown character"
        )
        title = f"Reported message in conversation with {character_name}"
        # ── Formatted description with markdown ──
        description = (  # noqa: E231
            f"Flagged Message:\n"  # noqa: E231
            f"{flag.message.content}\n\n"  # noqa: E231
            f"Reason: {flag.reason}\n\n"  # noqa: E231
            f"Flagged by: {flag.reported_by.get_display_name()} "  # noqa: E231
            f"({flag.reported_by.username})\n"  # noqa: E231
            f"Message ID: {flag.message.message_id}\n"  # noqa: E231
            f"Conversation: {flag.message.conversation.conversation_id}"  # noqa: E231
        )

        try:
            from capp.classes.support.support_service import SupportService

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
                logger.info("Auto-created ticket %s from flag %s", ticket_id, flag.flag_id)
        except Exception:
            logger.exception("Failed to create ticket for flag %s", flag.flag_id)

    # ── List flags (admin) ──
    @classmethod
    def list_flags(
        cls,
        actor,
        page: int = 1,
        page_size: int = 20,
        reviewed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not cls._is_admin(actor):
            return {"status": "fail", "data": "permission_denied"}

        if page < 1:
            page = 1
        if page_size < 1 or page_size > 100:
            page_size = 20

        queryset = MessageFlag.objects.select_related(
            "message__conversation__character",
            "reported_by",
            "reviewed_by",
        ).order_by("-created_at")

        if reviewed is not None:
            queryset = queryset.filter(reviewed=reviewed)

        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        flags = queryset[start:end]

        data = []
        for f in flags:
            reported_by_display = (
                f.reported_by.get_display_name()
                if f.reported_by
                else f"guest:{f.guest_session_id[:8] if f.guest_session_id else 'unknown'}"  # noqa: E231
            )  # noqa: E231
            data.append(
                {
                    "flag_id": str(f.flag_id),
                    "message_id": str(f.message.message_id),
                    "message_snippet": (f.message.content[:200] if f.message.content else ""),
                    "conversation_id": str(f.message.conversation.conversation_id),
                    "character_name": (
                        f.message.conversation.character.name if f.message.conversation.character else None
                    ),
                    "reported_by": reported_by_display,
                    "reason": f.reason,
                    "reviewed": f.reviewed,
                    "reviewed_by": (f.reviewed_by.get_display_name() if f.reviewed_by else None),
                    "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
                    "resolution_note": f.resolution_note,
                    "created_at": f.created_at.isoformat(),
                }
            )

        return {
            "status": "success",
            "data": {
                "flags": data,
                "has_next": end < total,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }

    @classmethod
    def review_flag(
        cls,
        flag_id: str,
        actor,
        resolution_note: Optional[str] = None,
        dismiss: bool = False,
    ) -> Dict[str, Any]:
        if not cls._is_admin(actor):
            return {"status": "fail", "data": "permission_denied"}

        try:
            flag = MessageFlag.objects.get(flag_id=flag_id)
        except MessageFlag.DoesNotExist:
            return {"status": "fail", "data": "flag_not_found"}

        flag.reviewed = True
        flag.reviewed_by = actor
        flag.reviewed_at = timezone.now()
        if resolution_note:
            flag.resolution_note = resolution_note.strip()
        flag.save(update_fields=["reviewed", "reviewed_by", "reviewed_at", "resolution_note"])
        logger.info("Message flag reviewed flag_id=%s by=%s", flag_id, actor.username)
        return {"status": "success", "data": "flag_reviewed"}

    @staticmethod
    def _is_admin(actor) -> bool:
        return bool(
            actor
            and getattr(actor, "is_authenticated", False)
            and (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))
        )
