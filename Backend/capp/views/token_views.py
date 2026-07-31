# capp/views/token_views.py

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import GuestTokenQuota, SparkPool


@api_view(["GET"])
@permission_classes([AllowAny])
def api_token_balance(request):
    """
    Return token balance / quota for the current user or guest.
    - Authenticated: { token_balance: int, daily_quota: int, daily_used: int }
    - Guest: identified by the guest credential cookie; returns daily and
      lifetime remaining.
    """
    if request.user.is_authenticated:
        spark_pool = SparkPool.objects.filter(user=request.user, is_active=True).first()
        token_balance = spark_pool.token_balance if spark_pool else 0
        daily_quota = request.user.daily_token_quota
        daily_used = request.user.daily_tokens_used
        return Response(
            {
                "status": "success",
                "data": {
                    "type": "authenticated",
                    "token_balance": token_balance,
                    "daily_quota": daily_quota,
                    "daily_used": daily_used,
                },
            }
        )
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)

        quota = GuestTokenQuota.objects.filter(session_id=identity.owner_key).first()
        if not quota:
            # No quota record yet – return full limits
            daily_remaining = settings.GUEST_DAILY_TOKEN_LIMIT
            lifetime_remaining = settings.GUEST_LIFETIME_TOKEN_LIMIT
        else:
            GuestTokenQuota.reset_daily_if_needed(quota)
            daily_remaining = max(0, settings.GUEST_DAILY_TOKEN_LIMIT - quota.daily_tokens_used)
            lifetime_remaining = max(0, settings.GUEST_LIFETIME_TOKEN_LIMIT - quota.lifetime_tokens_used)

        return Response(
            {
                "status": "success",
                "data": {
                    "type": "guest",
                    "daily_remaining": daily_remaining,
                    "lifetime_remaining": lifetime_remaining,
                    "daily_limit": settings.GUEST_DAILY_TOKEN_LIMIT,
                    "lifetime_limit": settings.GUEST_LIFETIME_TOKEN_LIMIT,
                },
            }
        )
