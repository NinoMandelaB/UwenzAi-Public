# capp/views/spark_pool_views.py

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import GuestTokenQuota, SparkPool
from capp.constants import GUEST_INITIAL_SPARKS


@api_view(["GET"])
@permission_classes([AllowAny])
def spark_pool_balance(request):
    """
    Return spark balance for the current user or guest.

    For authenticated users:
        - Returns the `sparks` from the user's SparkPool.
        - Requires a valid JWT token (handled by DRF authentication).

    For guests:
        - Identified by the guest credential cookie.
        - Returns the `sparks` from GuestTokenQuota.
        - If no quota row exists yet, it is created with `GUEST_INITIAL_SPARKS`.

    Example responses:
        Authenticated:
        {
            "status": "success",
            "data": {
                "type": "authenticated",
                "sparks": 1500
            }
        }

        Guest:
        {
            "status": "success",
            "data": {
                "type": "guest",
                "sparks": 600
            }
        }
    """
    if request.user.is_authenticated:
        # Authenticated user
        spark_pool = SparkPool.objects.filter(user=request.user, is_active=True).first()
        sparks = spark_pool.sparks if spark_pool else 0
        return Response(
            {
                "status": "success",
                "data": {
                    "type": "authenticated",
                    "sparks": sparks,
                },
            }
        )
    else:
        # Guest – ownership comes only from the credential cookie
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)

        # Get or create the guest quota (new guests get the initial sparks)
        quota, _ = GuestTokenQuota.objects.get_or_create(
            session_id=identity.owner_key, defaults={"sparks": GUEST_INITIAL_SPARKS}
        )
        return Response(
            {
                "status": "success",
                "data": {
                    "type": "guest",
                    "sparks": quota.sparks,
                },
            }
        )
