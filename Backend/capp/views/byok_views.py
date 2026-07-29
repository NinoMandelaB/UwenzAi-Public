# capp/views/byok_views.py

import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.classes.byok.byok_service import BYOKService

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def api_byok_config(request):
    """
    GET    /api/v1/auth/byok/   → Return current config (masked key)
    POST   /api/v1/auth/byok/   → Save / update config (validates key first)
    DELETE /api/v1/auth/byok/   → Remove config entirely
    """
    user = request.user

    # ── GET ───────────────────────────────────────────────────────────────────
    if request.method == "GET":
        try:
            result = BYOKService.get_config(user)
            if result["status"] == "not_found":
                return Response({"status": "success", "data": None}, status=200)
            return Response(result, status=200)
        except Exception:
            logger.exception("BYOK GET failed for user=%s", user.user_id)
            return Response({"status": "fail", "data": "server_error"}, status=500)

    # ── POST ──────────────────────────────────────────────────────────────────
    if request.method == "POST":
        api_url = request.data.get("api_url", "").strip()
        api_key = request.data.get("api_key", "").strip()
        model_name = request.data.get("model_name", "").strip()
        system_prompt = request.data.get("system_prompt", "").strip()
        store = bool(request.data.get("store_on_platform", False))

        # Check if user already has a saved config (api_key can be blank to keep existing)
        from capp.models import UserBYOKConfig

        has_existing = UserBYOKConfig.objects.filter(user=user).exists()

        # api_key required only when no config exists yet
        if not api_url or not model_name or (not api_key and not has_existing):
            return Response(
                {"status": "fail", "data": "api_url_api_key_and_model_name_required"},
                status=400,
            )

        try:
            result = BYOKService.save_config(
                user=user,
                api_url=api_url,
                api_key=api_key,
                model_name=model_name,
                system_prompt=system_prompt,
                store_on_platform=store,
            )
        except Exception:
            logger.exception(
                "BYOK POST unhandled exception for user=%s api_url=%s model=%s",
                user.user_id,
                api_url,
                model_name,
            )
            return Response({"status": "fail", "data": "server_error"}, status=500)

        if result["status"] != "success":
            error_map = {
                "invalid_api_key": 401,
                "model_not_found": 404,
                "connection_error": 502,
                "access_forbidden": 403,
            }
            http_status = error_map.get(result.get("data"), 400)
            return Response(result, status=http_status)

        return Response(result, status=200)

    # ── DELETE ────────────────────────────────────────────────────────────────
    if request.method == "DELETE":
        try:
            result = BYOKService.delete_config(user)
            if result["status"] == "not_found":
                return Response({"status": "fail", "data": "no_byok_config_found"}, status=404)
            return Response({"status": "success"}, status=200)
        except Exception:
            logger.exception("BYOK DELETE failed for user=%s", user.user_id)
            return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_exempt
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def api_byok_toggle(request):
    """
    PATCH /api/v1/auth/byok/toggle/
    Body: { "is_active": true|false }

    Let users quickly enable/disable BYOK without deleting the config.
    """
    is_active = request.data.get("is_active")
    if is_active is None:
        return Response({"status": "fail", "data": "is_active_required"}, status=400)

    try:
        result = BYOKService.set_active(request.user, bool(is_active))
        if result["status"] == "not_found":
            return Response({"status": "fail", "data": "no_byok_config_found"}, status=404)
        return Response({"status": "success"}, status=200)
    except Exception:
        logger.exception("BYOK PATCH toggle failed for user=%s", request.user.user_id)
        return Response({"status": "fail", "data": "server_error"}, status=500)
