"""
Agent Gateway Usage Service

Handles usage tracking and reporting for the Agent Gateway.
"""

import logging

from typing import Any

from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

from capp.models import AgentAPIKey, AgentUsageLog

logger = logging.getLogger(__name__)


def month_start():
    """Start of the current calendar month in the server timezone (aware)."""
    return timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class AgentUsageService:
    """
    Service for tracking and reporting Agent Gateway usage.

    Provides methods for:
    - Getting usage summaries per user
    - Getting usage summaries per key
    - Getting usage statistics
    """

    @staticmethod
    def get_user_usage(user_id: str) -> dict[str, Any]:
        """
        Get usage summary for a user.

        Args:
            user_id: The user ID

        Returns:
            Dict with usage summary
        """
        # Get total usage
        total = AgentUsageLog.objects.filter(user_id=user_id).aggregate(
            total_sparks=models.Sum("sparks_charged"),
            total_input_tokens=models.Sum("input_tokens"),
            total_output_tokens=models.Sum("output_tokens"),
            total_calls=models.Count("usage_log_id"),
        )

        # Get usage by status
        by_status = (
            AgentUsageLog.objects.filter(user_id=user_id)
            .values("status")
            .annotate(
                count=models.Count("usage_log_id"),
                sparks=models.Sum("sparks_charged"),
            )
            .order_by("-count")
        )

        # Get recent usage
        recent = AgentUsageLog.objects.filter(user_id=user_id).order_by("-created_at")[:10]

        return {
            "total_sparks": total["total_sparks"] or 0,
            "total_input_tokens": total["total_input_tokens"] or 0,
            "total_output_tokens": total["total_output_tokens"] or 0,
            "total_calls": total["total_calls"] or 0,
            "by_status": list(by_status),
            "recent_calls": list(recent.values("created_at", "model", "sparks_charged", "status", "latency_ms")),
        }

    @staticmethod
    def get_key_usage(key_id: str) -> dict[str, Any]:
        """
        Get usage summary for a specific API key.

        Args:
            key_id: The AgentAPIKey ID

        Returns:
            Dict with usage summary
        """
        # Get total usage
        total = AgentUsageLog.objects.filter(key_id=key_id).aggregate(
            total_sparks=models.Sum("sparks_charged"),
            total_input_tokens=models.Sum("input_tokens"),
            total_output_tokens=models.Sum("output_tokens"),
            total_calls=models.Count("usage_log_id"),
        )

        # Get monthly usage (current month)
        monthly = AgentUsageLog.objects.filter(
            key_id=key_id,
            created_at__gte=month_start(),
        ).aggregate(
            sparks=models.Sum("sparks_charged"),
            calls=models.Count("usage_log_id"),
        )

        return {
            "total_sparks": total["total_sparks"] or 0,
            "total_input_tokens": total["total_input_tokens"] or 0,
            "total_output_tokens": total["total_output_tokens"] or 0,
            "total_calls": total["total_calls"] or 0,
            "monthly_sparks": monthly["sparks"] or 0,
            "monthly_calls": monthly["calls"] or 0,
        }

    @staticmethod
    def get_all_keys_usage(user_id: str) -> list[dict[str, Any]]:
        """
        Get usage summary for all keys of a user.

        Args:
            user_id: The user ID

        Returns:
            List of usage summaries per key
        """
        keys = AgentAPIKey.objects.filter(user_id=user_id)

        result = []
        for key in keys:
            usage = AgentUsageService.get_key_usage(str(key.agent_api_key_id))
            result.append(
                {
                    "key_id": str(key.agent_api_key_id),
                    "label": key.label,
                    "hint": key.display_hint,
                    "is_active": key.is_active,
                    **usage,
                }
            )

        return result

    @staticmethod
    def get_monthly_summary(user_id: str, year: int | None = None, month: int | None = None) -> list[dict[str, Any]]:
        """
        Get monthly usage summary for a user.

        Args:
            user_id: The user ID
            year: Year to filter by (optional)
            month: Month to filter by (optional)

        Returns:
            List of monthly summaries
        """
        from django.db.models.functions import TruncMonth

        qs = AgentUsageLog.objects.filter(user_id=user_id)

        if year and month:
            qs = qs.filter(created_at__year=year, created_at__month=month)
        elif year:
            qs = qs.filter(created_at__year=year)

        summary = (
            qs.annotate(
                month=TruncMonth("created_at"),
            )
            .values("month")
            .annotate(
                sparks=models.Sum("sparks_charged"),
                calls=models.Count("usage_log_id"),
            )
            .order_by("-month")
        )

        return list(summary)

    @staticmethod
    def get_daily_usage(user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """
        Get daily usage for the last N days.

        Args:
            user_id: The user ID
            limit: Number of days to include

        Returns:
            List of daily usage summaries
        """
        from django.db.models.functions import TruncDate

        qs = AgentUsageLog.objects.filter(user_id=user_id)

        daily = (
            qs.annotate(
                day=TruncDate("created_at"),
            )
            .values("day")
            .annotate(
                sparks=models.Sum("sparks_charged"),
                calls=models.Count("usage_log_id"),
            )
            .order_by("-day")[:limit]
        )

        return list(daily)

    @staticmethod
    def get_usage_by_key(user_id: str) -> list[dict[str, Any]]:
        """Usage grouped by key and source, aggregated from usage logs.

        Grouping happens on the logs (key id + denormalized key_hint + source)
        rather than on live AgentAPIKey rows, so usage keeps showing for keys
        that were removed by the revoked-key retention task. ``source`` is
        ``None`` for rows written before the field existed (shown as direct
        API usage) and distinguishes workspace playground calls from direct
        API calls otherwise.

        Args:
            user_id: The user ID

        Returns:
            List of per-(key, source) usage summaries ordered by total sparks
        """
        monthly_q = models.Q(created_at__gte=month_start())
        # Group NULL (pre-field) and "api" rows together so the deployed mix
        # of legacy and new rows shows one Direct API line per key, not two.
        rows = (
            AgentUsageLog.objects.filter(user_id=user_id)
            .annotate(
                source_key=Coalesce("source", models.Value(AgentUsageLog.Source.API)),
            )
            .values("key_id", "key_hint", "source_key")
            .annotate(
                total_sparks=models.Sum("sparks_charged", default=0),
                total_calls=models.Count("usage_log_id"),
                monthly_sparks=models.Sum("sparks_charged", filter=monthly_q, default=0),
                monthly_calls=models.Count("usage_log_id", filter=monthly_q),
            )
            .order_by("-total_sparks")
        )

        keys_by_id = {
            str(key.agent_api_key_id): key
            for key in AgentAPIKey.objects.filter(user_id=user_id).only(
                "agent_api_key_id", "label", "key_hint", "is_active", "revoked_at"
            )
        }

        result = []
        for row in rows:
            key = keys_by_id.get(str(row["key_id"])) if row["key_id"] else None
            result.append(
                {
                    "key_id": str(row["key_id"]) if row["key_id"] else None,
                    # Live label while the key exists; hint-only afterwards.
                    "label": key.label if key else "",
                    # Same display format as list_keys ("...abcd").
                    "hint": f"...{row['key_hint']}" if row["key_hint"] else (key.display_hint if key else ""),
                    "is_active": key.is_active if key else False,
                    "removed": key is None,
                    "source": row["source_key"],
                    **{k: row[k] for k in ("total_sparks", "total_calls", "monthly_sparks", "monthly_calls")},
                }
            )
        return result

    @staticmethod
    def get_usage_by_model(user_id: str) -> list[dict[str, Any]]:
        """All pairs, including revoked/deleted keys; calendar month in server timezone."""
        monthly = models.Q(created_at__gte=month_start())
        return list(
            AgentUsageLog.objects.filter(user_id=user_id)
            .values("model", "provider")
            .annotate(
                total_input_tokens=models.Sum("input_tokens", default=0),
                total_output_tokens=models.Sum("output_tokens", default=0),
                total_calls=models.Count("usage_log_id"),
                total_sparks=models.Sum("sparks_charged", default=0),
                monthly_input_tokens=models.Sum("input_tokens", filter=monthly, default=0),
                monthly_output_tokens=models.Sum("output_tokens", filter=monthly, default=0),
                monthly_calls=models.Count("usage_log_id", filter=monthly),
                monthly_sparks=models.Sum("sparks_charged", filter=monthly, default=0),
            )
            .order_by("provider", "model")
        )
