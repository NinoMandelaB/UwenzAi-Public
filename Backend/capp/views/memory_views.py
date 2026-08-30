# capp/views/memory_views.py

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.models import Character
from capp.classes.memory.memory_service import MemoryService

logger = logging.getLogger(__name__)


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
