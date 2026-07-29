import logging
from typing import Dict, Any

from capp.models import Occupation

logger = logging.getLogger(__name__)


class OccupationService:
    """
    Service for managing occupations.
    Admins can create; anyone authenticated can list.
    """

    @classmethod
    def list_occupations(cls, request=None) -> Dict[str, Any]:
        """
        Return a list of all occupations.
        Public read access for authenticated users.
        """
        try:
            occupations = Occupation.objects.all().order_by("name")
            data = [
                {
                    "id": (str(occ.occupation_id) if hasattr(occ, "occupation_id") else str(occ.id)),
                    "name": occ.name,
                    "description": occ.description,
                }
                for occ in occupations
            ]
            return {
                "status": "success",
                "data": {
                    "occupations": data,
                    "count": len(data),
                },
            }
        except Exception:
            logger.exception("Unexpected error listing occupations")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def create_occupation(cls, payload: Dict[str, Any], actor=None, request=None) -> Dict[str, Any]:
        """
        Create a new occupation. Admin only.
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
            if Occupation.objects.filter(name__iexact=name).exists():
                return {"status": "fail", "data": "occupation_already_exists"}

            description = (payload.get("description") or "").strip()

            occupation = Occupation.objects.create(
                name=name,
                description=description,
            )

            logger.info(
                "Occupation created successfully occupation_id=%s name=%s created_by=%s",
                getattr(occupation, "occupation_id", occupation.pk),
                name,
                getattr(actor, "user_id", None),
            )

            return {
                "status": "success",
                "data": {
                    "message": "occupation_created_successfully",
                    "occupation": {
                        "id": (
                            str(occupation.occupation_id)
                            if hasattr(occupation, "occupation_id")
                            else str(occupation.id)
                        ),
                        "name": occupation.name,
                        "description": occupation.description,
                    },
                },
            }

        except Exception:
            logger.exception("Unexpected error creating occupation")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _is_admin_user(cls, actor) -> bool:
        """Check if the actor is an admin."""
        return bool(
            actor
            and getattr(actor, "is_authenticated", False)
            and (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))
        )
