import logging
from typing import Dict, Any

from capp.models import Hobby

logger = logging.getLogger(__name__)


class HobbyService:
    """
    Service for managing hobbies.
    Admins can create; anyone authenticated can list.
    """

    @classmethod
    def list_hobbies(cls, request=None) -> Dict[str, Any]:
        """
        Return a list of all hobbies.
        Public read access for authenticated users.
        """
        try:
            hobbies = Hobby.objects.all().order_by("name")
            data = [
                {
                    "id": (str(hobby.hobby_id) if hasattr(hobby, "hobby_id") else str(hobby.id)),
                    "name": hobby.name,
                    "description": hobby.description,
                }
                for hobby in hobbies
            ]
            return {
                "status": "success",
                "data": {
                    "hobbies": data,
                    "count": len(data),
                },
            }
        except Exception:
            logger.exception("Unexpected error listing hobbies")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def create_hobby(cls, payload: Dict[str, Any], actor=None, request=None) -> Dict[str, Any]:
        """
        Create a new hobby. Admin only.
        """
        try:
            if not cls._is_admin_user(actor):
                return {"status": "fail", "data": "permission_denied"}

            name = (payload.get("name") or "").strip()
            if not name:
                return {"status": "fail", "data": "name_required"}

            if len(name) > 100:
                return {"status": "fail", "data": "name_too_long"}

            # Check for duplicate (case-insensitive)
            if Hobby.objects.filter(name__iexact=name).exists():
                return {"status": "fail", "data": "hobby_already_exists"}

            description = (payload.get("description") or "").strip()

            hobby = Hobby.objects.create(
                name=name,
                description=description,
            )

            logger.info(
                "Hobby created successfully hobby_id=%s name=%s created_by=%s",
                getattr(hobby, "hobby_id", hobby.pk),
                name,
                getattr(actor, "user_id", None),
            )

            return {
                "status": "success",
                "data": {
                    "message": "hobby_created_successfully",
                    "hobby": {
                        "id": (str(hobby.hobby_id) if hasattr(hobby, "hobby_id") else str(hobby.id)),
                        "name": hobby.name,
                        "description": hobby.description,
                    },
                },
            }

        except Exception:
            logger.exception("Unexpected error creating hobby")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _is_admin_user(cls, actor) -> bool:
        """Check if the actor is an admin."""
        return bool(
            actor
            and getattr(actor, "is_authenticated", False)
            and (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))
        )
