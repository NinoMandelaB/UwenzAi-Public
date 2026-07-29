import logging
from typing import Dict, Any, Optional
from django.core.cache import cache
from django.utils import timezone
from capp.models import Feedback

logger = logging.getLogger(__name__)


class FeedbackService:

    @classmethod
    def delete_feedback(cls, feedback_id: str, actor) -> Dict[str, Any]:
        """Delete a feedback entry (admin only)."""
        if not cls._is_admin_user(actor):
            return {"status": "fail", "data": "permission_denied"}

        try:
            feedback = Feedback.objects.get(feedback_id=feedback_id)
            feedback.delete()
            logger.info("Feedback deleted feedback_id=%s by=%s", feedback_id, actor.user_id)
            return {"status": "success", "data": "feedback_deleted"}
        except Feedback.DoesNotExist:
            return {"status": "fail", "data": "not_found"}
        except Exception:
            logger.exception("Error deleting feedback")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def submit_feedback(cls, message: str, rating: Optional[int] = None, user=None, request=None) -> Dict[str, Any]:
        try:
            # --- IP‑based throttling: max 5 submissions per hour per IP ---
            ip_address = cls._get_client_ip(request)
            cache_key = f"feedback_throttle_{ip_address}"
            submission_count = cache.get(cache_key, 0)

            if submission_count >= 5:
                logger.warning("Feedback rate limit exceeded for IP %s", ip_address)
                return {"status": "fail", "data": "rate_limit_exceeded"}

            # Increment counter (expires in 1 hour)
            cache.set(cache_key, submission_count + 1, timeout=3600)
            # ----------------------------------------------------------------

            if not message or not message.strip():
                return {"status": "fail", "data": "message_required"}

            if len(message) > 2000:
                return {"status": "fail", "data": "message_too_long"}

            feedback = Feedback.objects.create(user=user, message=message.strip(), rating=rating)

            logger.info("Feedback submitted feedback_id=%s", feedback.feedback_id)

            return {
                "status": "success",
                "data": {"feedback_id": str(feedback.feedback_id)},
            }

        except Exception:
            logger.exception("Error submitting feedback")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _get_client_ip(cls, request):
        """Extract client IP from request."""
        if not request:
            return "unknown"
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR", "unknown")
        return ip

    @classmethod
    def list_feedback(cls, actor) -> Dict[str, Any]:
        if not cls._is_admin_user(actor):
            return {"status": "fail", "data": "permission_denied"}

        try:
            feedbacks = Feedback.objects.all().order_by("-created_at")
            data = []
            for f in feedbacks:
                data.append(
                    {
                        "feedback_id": str(f.feedback_id),
                        "user": f.user.get_display_name() if f.user else "Guest",
                        "user_id": str(f.user.user_id) if f.user else None,
                        "message": f.message,
                        "rating": f.rating,
                        "is_read": f.is_read,
                        "created_at": f.created_at.isoformat(),
                        "read_at": f.read_at.isoformat() if f.read_at else None,
                        "read_by": f.read_by.get_display_name() if f.read_by else None,
                    }
                )

            return {"status": "success", "data": {"feedback": data, "count": len(data)}}

        except Exception:
            logger.exception("Error listing feedback")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def mark_as_read(cls, feedback_id: str, actor) -> Dict[str, Any]:
        if not cls._is_admin_user(actor):
            return {"status": "fail", "data": "permission_denied"}

        try:
            feedback = Feedback.objects.get(feedback_id=feedback_id)
            feedback.is_read = True
            feedback.read_by = actor
            feedback.read_at = timezone.now()
            feedback.save(update_fields=["is_read", "read_by", "read_at"])

            logger.info(
                "Feedback marked as read feedback_id=%s by=%s",
                feedback_id,
                actor.user_id,
            )

            return {"status": "success"}

        except Feedback.DoesNotExist:
            return {"status": "fail", "data": "not_found"}
        except Exception:
            logger.exception("Error marking feedback as read")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _is_admin_user(cls, actor) -> bool:
        return bool(
            actor
            and getattr(actor, "is_authenticated", False)
            and (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))
        )
