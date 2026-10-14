# capp/classes/support/support_service.py
import logging
from typing import Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from django.db import models

from capp.models import SupportTicket, TicketMessage, CustomUser, TicketView

logger = logging.getLogger(__name__)


class SupportService:
    # ─────────────────────────────────────────────────────────────
    # Permission helpers
    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _is_admin(user: CustomUser) -> bool:
        return user.is_authenticated and (user.is_staff or user.is_superuser)

    @staticmethod
    def _can_view_ticket(user: CustomUser, ticket: SupportTicket) -> bool:
        if SupportService._is_admin(user):
            return True
        return ticket.created_by == user

    @classmethod
    def _get_unread_count_for_ticket(cls, ticket: SupportTicket, user: CustomUser) -> int:
        if not user or not user.is_authenticated:
            return 0

        last_view = TicketView.objects.filter(user=user, ticket=ticket).first()
        qs = TicketMessage.objects.filter(ticket=ticket)

        if not cls._is_admin(user):
            qs = qs.exclude(sender=user).filter(is_internal=False)

        if last_view:
            qs = qs.filter(created_at__gt=last_view.last_viewed_at)

        return qs.count()

    # ─────────────────────────────────────────────────────────────
    # Ticket CRUD
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def create_ticket(cls, user: CustomUser, title: str, description: str, priority: str = "medium") -> Dict[str, Any]:
        try:
            if not user.is_authenticated:
                return {"status": "fail", "data": "authentication_required"}

            title = (title or "").strip()
            description = (description or "").strip()
            if not title or not description:
                return {"status": "fail", "data": "title_and_description_required"}

            valid_priorities = [p[0] for p in SupportTicket.Priority.choices]
            if priority not in valid_priorities:
                priority = SupportTicket.Priority.MEDIUM

            with transaction.atomic():
                ticket = SupportTicket.objects.create(
                    title=title,
                    description=description,
                    priority=priority,
                    created_by=user,
                )
                TicketMessage.objects.create(
                    ticket=ticket,
                    sender=user,
                    content=description,
                    parent=None,
                )

            logger.info("Support ticket created: %s by %s", ticket.ticket_id, user.username)
            return {
                "status": "success",
                "data": {"ticket": cls._serialize_ticket(ticket, include_messages=False)},
            }
        except Exception:
            logger.exception("Error creating support ticket")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def get_ticket(cls, user: CustomUser, ticket_id: str) -> Dict[str, Any]:
        try:
            ticket = SupportTicket.objects.filter(ticket_id=ticket_id).first()
            if not ticket:
                return {"status": "fail", "data": "ticket_not_found"}
            if not cls._can_view_ticket(user, ticket):
                return {"status": "fail", "data": "permission_denied"}
            return {
                "status": "success",
                "data": {"ticket": cls._serialize_ticket(ticket, include_messages=True, user=user)},
            }
        except Exception:
            logger.exception("Error retrieving ticket")
            return {"status": "fail", "data": "server_error"}

    # ─────────────────────────────────────────────────────────────
    # Admin-initiated outreach (see capp/views/admin_views.py::admin_message_user)
    # ─────────────────────────────────────────────────────────────
    DEFAULT_OUTREACH_TITLE = "Message from the UwenzAi team"

    @classmethod
    def create_admin_outreach_ticket(
        cls,
        admin_user: CustomUser,
        target_user: CustomUser,
        message: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Open a support ticket ON BEHALF OF a user, written by an admin.

        For high-priority safety situations (care events, critical/high
        severity) an administrator needs to reach the user directly instead
        of waiting for them to open a ticket. The ticket is created_by the
        TARGET user, so it lands in their existing Support inbox (unread
        badge included) and they can reply through the normal flow; the first
        message's sender is the admin, rendered as the support team.

        Status starts IN_PROGRESS (an admin has already acted, mirroring
        reply_to_ticket's admin-reply transition) and the ticket is assigned
        to the sending admin. The durable audit trail is the ticket itself:
        metadata records who initiated it, and the first message carries the
        admin as its sender.
        """
        try:
            if not cls._is_admin(admin_user):
                return {"status": "fail", "data": "permission_denied"}
            if target_user is None or target_user.is_deleted:
                return {"status": "fail", "data": "user_not_found"}

            message = (message or "").strip()
            if not message:
                return {"status": "fail", "data": "message_required"}
            title = (title or "").strip() or cls.DEFAULT_OUTREACH_TITLE

            with transaction.atomic():
                ticket = SupportTicket.objects.create(
                    title=title,
                    description=message,
                    status=SupportTicket.Status.IN_PROGRESS,
                    priority=SupportTicket.Priority.HIGH,
                    created_by=target_user,
                    assigned_to=admin_user,
                    metadata={
                        "initiated_by_admin": True,
                        "initiated_by_admin_id": str(admin_user.user_id),
                    },
                )
                TicketMessage.objects.create(
                    ticket=ticket,
                    sender=admin_user,
                    content=message,
                    parent=None,
                )
                # Mark the ticket read for the sending admin so their own
                # outreach message does not show up as unread for them.
                TicketView.objects.update_or_create(
                    user=admin_user,
                    ticket=ticket,
                    defaults={"last_viewed_at": timezone.now()},
                )

            # Web Push for the TARGET user (privacy-safe, content-free): they
            # would otherwise only learn about the message from the header
            # badge's 30-second poll or by opening their Support inbox. Runs
            # as a Celery task on commit so the admin's request never pays
            # for external push-endpoint latency. Both the registration and
            # the callback itself are guarded: a broker outage raises inside
            # the commit, and the ticket that was just created must never be
            # lost to a 500.
            def _schedule_support_push():
                try:
                    send_support_push_task.delay(str(target_user.user_id))
                except Exception:
                    logger.exception(
                        "Could not enqueue support push for user %s (ticket was created)",
                        target_user.user_id,
                    )

            try:
                from capp.tasks import send_support_message_push as send_support_push_task

                transaction.on_commit(_schedule_support_push)
            except Exception:
                logger.exception(
                    "Could not register support push for user %s (ticket was created)",
                    target_user.user_id,
                )

            logger.info(
                "Admin outreach ticket %s created by %s for %s",
                ticket.ticket_id,
                admin_user.username,
                target_user.username,
            )
            return {
                "status": "success",
                "data": {"ticket": cls._serialize_ticket(ticket, include_messages=False, user=admin_user)},
            }
        except Exception:
            logger.exception("Error creating admin outreach ticket")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def list_tickets(cls, user: CustomUser, filters: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            queryset = SupportTicket.objects.all()
            if not cls._is_admin(user):
                queryset = queryset.filter(created_by=user)
            if filters:
                if filters.get("status"):
                    queryset = queryset.filter(status=filters["status"])
                if filters.get("priority"):
                    queryset = queryset.filter(priority=filters["priority"])
                if filters.get("assigned_to"):
                    queryset = queryset.filter(assigned_to_id=filters["assigned_to"])
            queryset = queryset.order_by("-created_at")
            data = [cls._serialize_ticket(t, include_messages=False, user=user) for t in queryset]
            return {"status": "success", "data": {"tickets": data, "count": len(data)}}
        except Exception:
            logger.exception("Error listing tickets")
            return {"status": "fail", "data": "server_error"}

    # ─────────────────────────────────────────────────────────────
    # Reply / Threading
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def reply_to_ticket(
        cls,
        user: CustomUser,
        ticket_id: str,
        content: str,
        parent_message_id: Optional[str] = None,
        is_internal: bool = False,
    ) -> Dict[str, Any]:
        try:
            ticket = SupportTicket.objects.filter(ticket_id=ticket_id).first()
            if not ticket:
                return {"status": "fail", "data": "ticket_not_found"}

            if not cls._can_view_ticket(user, ticket):
                return {"status": "fail", "data": "permission_denied"}

            if ticket.status in (
                SupportTicket.Status.RESOLVED,
                SupportTicket.Status.CLOSED,
            ):
                return {"status": "fail", "data": "ticket_closed"}

            content = (content or "").strip()
            if not content:
                return {"status": "fail", "data": "content_required"}

            parent = None
            if parent_message_id:
                parent = TicketMessage.objects.filter(message_id=parent_message_id, ticket=ticket).first()
                if not parent:
                    return {"status": "fail", "data": "parent_message_not_found"}

            if is_internal and not cls._is_admin(user):
                is_internal = False

            with transaction.atomic():
                message = TicketMessage.objects.create(
                    ticket=ticket,
                    sender=user,
                    content=content,
                    parent=parent,
                    is_internal=is_internal,
                )
                if cls._is_admin(user) and ticket.status == SupportTicket.Status.OPEN:
                    ticket.status = SupportTicket.Status.IN_PROGRESS
                    ticket.save(update_fields=["status", "updated_at"])

            logger.info("Reply added to ticket %s by %s", ticket_id, user.username)
            return {
                "status": "success",
                "data": {"message": cls._serialize_message(message)},
            }
        except Exception:
            logger.exception("Error adding reply")
            return {"status": "fail", "data": "server_error"}

    # ─────────────────────────────────────────────────────────────
    # Admin actions
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def update_ticket_status(
        cls,
        admin_user: CustomUser,
        ticket_id: str,
        status: Optional[str] = None,
        assigned_to_id: Optional[str] = None,  # None = skip, "" = clear, otherwise UUID
    ) -> Dict[str, Any]:
        logger.info(
            "update_ticket_status called: status=%s, assigned_to_id=%s",
            status,
            assigned_to_id,
        )

        if not cls._is_admin(admin_user):
            return {"status": "fail", "data": "permission_denied"}

        ticket = SupportTicket.objects.filter(ticket_id=ticket_id).first()
        if not ticket:
            return {"status": "fail", "data": "ticket_not_found"}

        update_fields = ["updated_at"]

        # ── Update status if provided ──
        if status is not None:
            status_val = status.strip().lower() if status else ""
            valid_statuses = [s[0] for s in SupportTicket.Status.choices]
            if status_val not in valid_statuses:
                logger.warning("Invalid status: '%s'", status_val)
                return {"status": "fail", "data": "invalid_status"}
            ticket.status = status_val
            update_fields.append("status")
            if status_val in (
                SupportTicket.Status.RESOLVED,
                SupportTicket.Status.CLOSED,
            ):
                ticket.closed_at = timezone.now()
                update_fields.append("closed_at")
            else:
                ticket.closed_at = None
                update_fields.append("closed_at")
        else:
            logger.info("status is None, skipping status update")

        # ── Update assignee if provided ──
        if assigned_to_id is not None:
            if assigned_to_id == "":
                ticket.assigned_to = None
                update_fields.append("assigned_to")
                logger.info("Cleared assignee")
            else:
                try:
                    assignee = CustomUser.objects.get(user_id=assigned_to_id)
                    ticket.assigned_to = assignee
                    update_fields.append("assigned_to")
                    logger.info("Assigned to %s", assignee.username)
                except CustomUser.DoesNotExist:
                    logger.warning("Assignee not found: %s", assigned_to_id)
                    return {"status": "fail", "data": "assignee_not_found"}
        else:
            logger.info("assigned_to_id is None, skipping assignee update")

        ticket.save(update_fields=update_fields)
        logger.info("Ticket %s updated by admin %s", ticket_id, admin_user.username)

        return {
            "status": "success",
            "data": {"ticket": cls._serialize_ticket(ticket, include_messages=False)},
        }

    # ─────────────────────────────────────────────────────────────
    # Read / Unread
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def get_unread_count(cls, user: CustomUser) -> Dict[str, Any]:
        try:
            if not user.is_authenticated:
                return {"status": "fail", "data": "authentication_required"}

            is_admin = cls._is_admin(user)
            if is_admin:
                ticket_ids = SupportTicket.objects.all().values_list("ticket_id", flat=True)
            else:
                ticket_ids = SupportTicket.objects.filter(
                    models.Q(created_by=user) | models.Q(assigned_to=user)
                ).values_list("ticket_id", flat=True)

            if not ticket_ids:
                return {"status": "success", "data": {"count": 0}}

            total_unread = 0
            for ticket_id in ticket_ids:
                try:
                    ticket = SupportTicket.objects.get(ticket_id=ticket_id)
                except SupportTicket.DoesNotExist:
                    continue
                total_unread += cls._get_unread_count_for_ticket(ticket, user)

            return {"status": "success", "data": {"count": total_unread}}
        except Exception:
            logger.exception("Error counting unread messages")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def mark_ticket_read(cls, user: CustomUser, ticket_id: str) -> Dict[str, Any]:
        try:
            ticket = SupportTicket.objects.filter(ticket_id=ticket_id).first()
            if not ticket:
                return {"status": "fail", "data": "ticket_not_found"}

            if not cls._can_view_ticket(user, ticket):
                return {"status": "fail", "data": "permission_denied"}

            TicketView.objects.update_or_create(user=user, ticket=ticket, defaults={"last_viewed_at": timezone.now()})
            return {"status": "success", "data": {"message": "ticket_marked_read"}}
        except Exception:
            logger.exception("Error marking ticket as read")
            return {"status": "fail", "data": "server_error"}

    # ─────────────────────────────────────────────────────────────
    # Serializers
    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _serialize_message(msg: TicketMessage) -> Dict[str, Any]:
        return {
            "message_id": str(msg.message_id),
            "sender": {
                "user_id": str(msg.sender.user_id),
                "username": msg.sender.username,
                "display_name": msg.sender.get_display_name(),
                "is_admin": msg.sender.is_staff or msg.sender.is_superuser,
            },
            "content": msg.content,
            "parent_id": str(msg.parent.message_id) if msg.parent else None,
            "is_internal": msg.is_internal,
            "created_at": msg.created_at.isoformat(),
        }

    @classmethod
    def _serialize_ticket(cls, ticket: SupportTicket, include_messages: bool = False, user=None) -> Dict[str, Any]:
        data = {
            "ticket_id": str(ticket.ticket_id),
            "title": ticket.title,
            "description": ticket.description,
            "status": ticket.status,
            "priority": ticket.priority,
            "created_by": {
                "user_id": str(ticket.created_by.user_id),
                "username": ticket.created_by.username,
                "display_name": ticket.created_by.get_display_name(),
            },
            "assigned_to": None,
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat(),
            "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
            "message_count": ticket.messages.count(),
            "unread_count": (cls._get_unread_count_for_ticket(ticket, user) if user and user.is_authenticated else 0),
        }

        if ticket.assigned_to:
            data["assigned_to"] = {
                "user_id": str(ticket.assigned_to.user_id),
                "username": ticket.assigned_to.username,
                "display_name": ticket.assigned_to.get_display_name(),
            }

        if include_messages:
            messages_qs = ticket.messages.all().order_by("created_at")
            is_admin = cls._is_admin(user) if user else False
            if not is_admin:
                messages_qs = messages_qs.filter(is_internal=False)

            messages_by_id = {}
            root_messages = []

            for msg in messages_qs:
                ser_msg = cls._serialize_message(msg)
                messages_by_id[msg.message_id] = ser_msg
                if msg.parent is None:
                    root_messages.append(ser_msg)

            for msg in messages_qs:
                if msg.parent:
                    parent = messages_by_id.get(msg.parent.message_id)
                    if parent:
                        parent.setdefault("replies", []).append(messages_by_id[msg.message_id])

            data["messages"] = root_messages

        return data
