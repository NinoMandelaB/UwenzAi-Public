"""
Agent Gateway Key Service

Handles creation, retrieval, and management of Agent API keys.
"""

import logging
from datetime import date
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db import models
from django.utils import timezone

from capp.models import AgentAPIKey, AgentUsageLog, CustomUser, BillingReservation
from capp.classes.agent_gateway.auth import generate_api_key, hash_api_key, get_key_hint

logger = logging.getLogger(__name__)


User = get_user_model()


class AgentKeyService:
    """
    Service for managing Agent API keys.

    Provides methods for:
    - Creating new keys
    - Listing user's keys
    - Revoking keys
    - Getting key details
    - Checking key budgets
    """

    @staticmethod
    def parse_monthly_budget(raw: Any) -> tuple[int | None, str | None]:
        """Validate a monthly_spark_budget value (shared by create and budget update).

        Returns ``(budget, error)``: ``budget`` is None for no limit (either
        the value was omitted or null was passed explicitly), ``error`` is a
        human-readable message when the value is invalid.
        """
        if raw is None:
            return None, None
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return None, "monthly_spark_budget must be an integer."
        try:
            budget = int(raw)
        except (ValueError, TypeError):
            return None, "monthly_spark_budget must be an integer."
        max_budget = getattr(settings, "AGENT_KEY_MAX_MONTHLY_BUDGET", 1_000_000)
        if budget < 1 or budget > max_budget:
            return None, f"monthly_spark_budget must be between 1 and {max_budget} sparks."
        return budget, None

    @staticmethod
    def create_key(
        user: CustomUser,
        label: str = "",
        expires_at: date | None = None,
        monthly_spark_budget: int | None = None,
    ) -> tuple[str, AgentAPIKey]:
        """
        Create a new Agent API key for a user.

        The full key is returned exactly once and should never be stored.
        Only the hash and hint are persisted.

        Args:
            user: The user to create the key for
            label: Optional label for the key
            expires_at: Optional expiration date
            monthly_spark_budget: Optional per-key monthly spending cap in
                sparks (None = unlimited). Must be a positive integer; the API
                layer additionally bounds it by AGENT_KEY_MAX_MONTHLY_BUDGET.

        Returns:
            Tuple of (full_key, key_record)
        """
        if monthly_spark_budget is not None:
            if isinstance(monthly_spark_budget, bool) or not isinstance(monthly_spark_budget, int):
                raise ValueError("monthly_spark_budget must be an integer")
            if monthly_spark_budget < 1:
                raise ValueError("monthly_spark_budget must be positive")
        # Determine prefix based on environment
        # In production, use "live", otherwise "dev"
        import os

        is_production = os.getenv("IS_PRODUCTION", "false").lower() == "true"
        prefix = "live" if is_production else "dev"

        # Generate the full key
        full_key = generate_api_key(prefix)

        # Compute hash and hint
        key_hash = hash_api_key(full_key)
        key_hint = get_key_hint(full_key)

        # Create the key record
        key_record = AgentAPIKey.objects.create(
            user=user,
            label=label.strip()[:100] if label else "",
            key_hash=key_hash,
            key_hint=key_hint,
            is_active=True,
            expires_at=expires_at,
            monthly_spark_budget=monthly_spark_budget,
        )

        logger.info(
            "Agent Gateway: created new key for user %s, key_id=%s, hint=%s",
            user.user_id,
            key_record.agent_api_key_id,
            key_hint,
        )

        return (full_key, key_record)

    @staticmethod
    def list_keys(user: CustomUser) -> list[dict[str, Any]]:
        """
        List all Agent API keys for a user.

        Args:
            user: The user to list keys for

        Returns:
            List of key info dicts (without full keys)
        """
        keys = AgentAPIKey.objects.filter(user=user).order_by("-created_at")

        result = []
        for key in keys:
            result.append(
                {
                    "id": str(key.agent_api_key_id),
                    "label": key.label,
                    "hint": key.display_hint,
                    "is_active": key.is_active,
                    "created_at": key.created_at.isoformat() if key.created_at else None,
                    "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                    "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                    "monthly_spark_budget": key.monthly_spark_budget,
                    "is_expired": key.is_expired(),
                    "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                }
            )

        return result

    @staticmethod
    def get_key(user: CustomUser, key_id: str) -> AgentAPIKey | None:
        """
        Get a single Agent API key by ID.

        Args:
            user: The user who owns the key
            key_id: The key ID

        Returns:
            AgentAPIKey record or None if not found or not owned by user
        """
        try:
            key = AgentAPIKey.objects.get(agent_api_key_id=key_id)
            if key.user_id != user.user_id:
                return None
            return key
        except AgentAPIKey.DoesNotExist:
            return None

    @staticmethod
    def revoke_key(user: CustomUser, key_id: str) -> bool:
        """
        Revoke an Agent API key.

        Args:
            user: The user who owns the key
            key_id: The key ID to revoke

        Returns:
            True if key was revoked, False if not found or not owned
        """
        key = AgentKeyService.get_key(user, key_id)
        if key is None:
            return False

        key.is_active = False
        # Keep the first revocation timestamp: the retention task measures
        # from it, and repeated revoke calls must not extend a key's life.
        if key.revoked_at is None:
            key.revoked_at = timezone.now()
        key.save(update_fields=["is_active", "revoked_at", "updated_at"])

        logger.info(
            "Agent Gateway: revoked key %s for user %s",
            key_id,
            user.user_id,
        )

        return True

    @staticmethod
    def check_budget(key: AgentAPIKey, sparks_needed: int) -> bool:
        """
        Check if a key has enough budget for a request.

        Args:
            key: The AgentAPIKey to check
            sparks_needed: Number of sparks needed for the request

        Returns:
            True if budget allows the request, False otherwise
        """
        if key.monthly_spark_budget is None:
            return True  # No budget limit

        # Calculate sparks used this month (timezone-aware month start;
        # naive datetimes trigger RuntimeWarnings against the aware column)
        from capp.classes.agent_gateway.usage_service import month_start

        # Get usage for this key this month
        usage = (
            AgentUsageLog.objects.filter(
                key=key,
                created_at__gte=month_start(),
            ).aggregate(
                total=models.Sum("sparks_charged")
            )["total"]
            or 0
        )

        # Include still-reserved holds (e.g. a worker crashed). Never spend that budget twice.
        pending = (
            BillingReservation.objects.filter(
                user=key.user,
                feature="agent_gateway",
                status=BillingReservation.Status.RESERVED,
            )
            .filter(
                models.Q(idempotency_key__startswith=f"agent:{key.pk}:")
                | ~models.Q(idempotency_key__startswith="agent:")
                | models.Q(operation_reference__startswith="agent_gateway:")
            )
            .aggregate(total=models.Sum("amount_sparks"))["total"]
            or 0
        )
        return (usage + pending + sparks_needed) <= key.monthly_spark_budget

    @staticmethod
    def get_budget_status(key: AgentAPIKey) -> dict[str, Any]:
        """
        Get budget usage status for a key.

        Args:
            key: The AgentAPIKey

        Returns:
            Dict with budget info
        """
        if key.monthly_spark_budget is None:
            return {
                "has_budget": False,
                "monthly_budget": None,
                "used_this_month": 0,
                "remaining": None,
            }

        today = timezone.now().date()
        first_of_month = date(today.year, today.month, 1)

        usage = (
            AgentUsageLog.objects.filter(
                key=key,
                created_at__gte=first_of_month,
            ).aggregate(
                total=models.Sum("sparks_charged")
            )["total"]
            or 0
        )

        return {
            "has_budget": True,
            "monthly_budget": key.monthly_spark_budget,
            "used_this_month": usage,
            "remaining": max(0, key.monthly_spark_budget - usage),
        }

    @staticmethod
    def rotate_key(user: CustomUser, old_key_id: str, new_label: str = "") -> tuple[str, AgentAPIKey] | None:
        """
        Create a new key and revoke an old one (atomic operation).

        Args:
            user: The user
            old_key_id: The ID of the key to revoke
            new_label: Label for the new key

        Returns:
            Tuple of (full_new_key, new_key_record) or None if old key not found
        """
        old_key = AgentKeyService.get_key(user, old_key_id)
        if old_key is None:
            return None

        with transaction.atomic():
            # Create new key
            full_new_key, new_key = AgentKeyService.create_key(user, new_label)

            # Revoke old key
            old_key.is_active = False
            old_key.save(update_fields=["is_active", "updated_at"])

            return (full_new_key, new_key)
