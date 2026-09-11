# capp/views/memory_views.py

import logging
from functools import wraps
from django.core.exceptions import ValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.exceptions import Throttled

from capp.models import Character
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.memory.memory_service import MemoryService
from capp.classes.memory.memory_editor_service import (
    MemoryEditorError,
    MemoryEditorService,
)

logger = logging.getLogger(__name__)


class MemoryAdditionThrottle(UserRateThrottle):
    scope = "memory_addition"
    rate = "20/min"


def require_memory_editor_feature(view_func):
    """403 unless the user enabled the memory-editor feature in Settings."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not FeaturePreferences.is_enabled(request.user, "memory_editor"):
            return Response({"status": "fail", "data": "feature_disabled"}, status=403)
        if request.method in ("POST", "PATCH", "PUT", "DELETE") and not isinstance(request.data, dict):
            return Response({"status": "fail", "data": "invalid_payload"}, status=400)
        if request.method == "POST":
            throttle = MemoryAdditionThrottle()
            if not throttle.allow_request(request, None):
                raise Throttled(wait=throttle.wait())
        return view_func(request, *args, **kwargs)

    return wrapper


def _get_character_or_none(character_id):
    try:
        return Character.objects.get(character_id=character_id)
    except (Character.DoesNotExist, ValueError, ValidationError):
        return None


def _editor_error_response(e: MemoryEditorError) -> Response:
    return Response({"status": "fail", "data": e.code}, status=e.status)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_clear_memory_for_character(request, character_id):
    """
    DELETE /api/v1/memory/clear/<uuid:character_id>/
    Clears all memory facts and summaries for the authenticated user and character.
    """
    try:
        character = Character.objects.get(character_id=character_id)
    except Character.DoesNotExist:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)

    user = request.user

    result = MemoryService.clear_memory_for_character(user, character)

    return Response(
        {
            "status": "success",
            "data": {
                "message": "Memory cleared successfully.",
                "facts_deleted": result["facts_deleted"],
                "relations_deleted": result.get("relations_deleted", 0),
                "summaries_deleted": result["summaries_deleted"],
                "relationship_reset": result.get("relationship_reset", 0),
                "entities_deleted": result.get("entities_deleted", 0),
            },
        },
        status=200,
    )


# ----------------------------------------------------------------------
# Memory editor (brain icon popup) — logged-in users only.
#
# Every handler re-derives ownership from request.user; the service layer
# filters every queryset by user, so rows of other users or characters can
# never be listed or mutated through these endpoints.
# ----------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_list_memory(request, character_id):
    """GET /api/v1/memory/editor/<uuid:character_id>/ — facts, relations, and current summary."""
    character = _get_character_or_none(character_id)
    if character is None:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    try:
        data = MemoryEditorService.list_memory(
            request.user,
            character,
            page=request.query_params.get("page", 1),
            page_size=request.query_params.get("page_size", 50),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_create_memory_fact(request, character_id):
    """POST /api/v1/memory/editor/<uuid:character_id>/facts/"""
    character = _get_character_or_none(character_id)
    if character is None:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    try:
        fact = MemoryEditorService.create_fact(
            request.user,
            character,
            fact_text=request.data.get("fact_text", ""),
            fact_type=request.data.get("fact_type", "other"),
            importance_score=request.data.get("importance_score", 7),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": fact}, status=201)


@api_view(["PATCH", "PUT"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_update_memory_fact(request, fact_id):
    """PATCH /api/v1/memory/editor/facts/<uuid:fact_id>/"""
    try:
        fact = MemoryEditorService.update_fact(
            request.user,
            fact_id,
            fact_text=request.data.get("fact_text", ""),
            expected_updated_at=request.data.get("expected_updated_at"),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": fact})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_delete_memory_fact(request, fact_id):
    """DELETE /api/v1/memory/editor/facts/<uuid:fact_id>/"""
    try:
        result = MemoryEditorService.delete_fact(
            request.user, fact_id, expected_updated_at=request.data.get("expected_updated_at")
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": result})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_create_memory_relation(request, character_id):
    """POST /api/v1/memory/editor/<uuid:character_id>/relations/"""
    character = _get_character_or_none(character_id)
    if character is None:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    try:
        relation = MemoryEditorService.create_relation(
            request.user,
            character,
            subject=request.data.get("subject", ""),
            predicate=request.data.get("predicate", ""),
            object_text=request.data.get("object", ""),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": relation}, status=201)


@api_view(["PATCH", "PUT"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_update_memory_relation(request, relation_id):
    """PATCH /api/v1/memory/editor/relations/<uuid:relation_id>/"""
    try:
        relation = MemoryEditorService.update_relation(
            request.user,
            relation_id,
            subject=request.data.get("subject", ""),
            predicate=request.data.get("predicate", ""),
            object_text=request.data.get("object", ""),
            expected_updated_at=request.data.get("expected_updated_at"),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": relation})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_delete_memory_relation(request, relation_id):
    """DELETE /api/v1/memory/editor/relations/<uuid:relation_id>/"""
    try:
        result = MemoryEditorService.delete_relation(
            request.user, relation_id, expected_updated_at=request.data.get("expected_updated_at")
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": result})


@api_view(["PATCH", "PUT"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_update_memory_summary(request, character_id):
    """PATCH /api/v1/memory/editor/summary/<uuid:character_id>/"""
    character = _get_character_or_none(character_id)
    if character is None:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    try:
        summary = MemoryEditorService.update_summary(
            request.user,
            character,
            summary_text=request.data.get("summary_text", ""),
            expected_updated_at=request.data.get("expected_updated_at"),
            expected_summary_id=request.data.get("expected_summary_id"),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": summary})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@require_memory_editor_feature
def api_delete_memory_summary(request, character_id):
    """DELETE /api/v1/memory/editor/summary/<uuid:character_id>/"""
    character = _get_character_or_none(character_id)
    if character is None:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    try:
        result = MemoryEditorService.delete_summary(
            request.user,
            character,
            expected_updated_at=request.data.get("expected_updated_at"),
            expected_summary_id=request.data.get("expected_summary_id"),
        )
    except MemoryEditorError as e:
        return _editor_error_response(e)
    return Response({"status": "success", "data": result})
