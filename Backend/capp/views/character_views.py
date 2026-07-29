import logging

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.billing import idempotency
from capp.classes.billing.idempotency import InvalidIdempotencyKey
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.csrf import csrf_protect_for_guests
from capp.models import Character, Personality
from capp.classes.characters.character_service import CharacterService
from capp.classes.characters.character_flag_service import CharacterFlagService

logger = logging.getLogger(__name__)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_clear_character_flags(request, character_id):
    """
    DELETE /api/v1/characters/<uuid:character_id>/clear-flags/
    Removes all flag records for the given character.
    Admin only.
    """
    # Allow both staff and superuser
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({"status": "fail", "data": "permission_denied"}, status=403)

    character = get_object_or_404(Character, character_id=character_id)
    result = CharacterFlagService.clear_character_flags(character, admin_user=request.user)

    if result["status"] == "fail":
        return Response(result, status=400)

    return Response(result, status=200)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def api_flag_character(request, character_id):
    """
    Flag a character. Anyone can flag (authenticated or guest).
    Body: { reason: "..." }
    """
    character = get_object_or_404(Character, character_id=character_id)

    reason = request.data.get("reason", "").strip()
    if not reason:
        return Response({"status": "fail", "data": "reason_required"}, status=400)

    if request.user.is_authenticated:
        user = request.user
        session_id = None
    else:
        user = None
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        session_id = identity.owner_key

    result = CharacterFlagService.flag_character(
        character=character,
        reason=reason,
        user=user,
        guest_session_id=session_id,
    )

    if result["status"] == "fail":
        return Response(result, status=400)

    return Response(result, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_flagged_characters(request):
    """
    Admin-only: list characters that have been flagged.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({"status": "fail", "data": "permission_denied"}, status=403)

    characters = CharacterFlagService.list_flagged_characters()
    data = []
    for char in characters:
        data.append(
            {
                "character_id": str(char.character_id),
                "name": char.name,
                "flag_count": char.flag_count,
                "latest_flag_reason": char.latest_flag_reason or "",
                "is_active": char.is_active,
                "status": char.status,
            }
        )

    return Response({"status": "success", "data": {"characters": data}})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_personality(request, personality_id):
    if not request.user.is_staff and not request.user.is_superuser:
        return Response({"status": "fail", "data": "permission_denied"}, status=403)
    try:
        personality = Personality.objects.get(pk=personality_id)
    except Personality.DoesNotExist:
        return Response({"status": "fail", "data": "personality_not_found"}, status=404)
    personality.delete()
    return Response({"status": "success", "data": "personality_deleted"})


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_personalities(request):
    personalities = Personality.objects.all().order_by("name")
    data = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description or "",
        }
        for p in personalities
    ]
    return Response({"status": "success", "data": {"personalities": data}})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_personality(request):
    if not request.user.is_staff and not request.user.is_superuser:
        return Response({"status": "fail", "data": "permission_denied"}, status=403)

    name = request.data.get("name", "").strip()
    if not name:
        return Response({"status": "fail", "data": "name_required"}, status=400)

    description = request.data.get("description", "").strip()

    personality, created = Personality.objects.get_or_create(name=name, defaults={"description": description})

    # If the personality already existed, we may still want to update the description
    if not created and description:
        personality.description = description
        personality.save(update_fields=["description", "updated_at"])

    return Response(
        {
            "status": "success",
            "data": {
                "id": personality.id,
                "name": personality.name,
                "description": personality.description or "",
                "created": created,
            },
        },
        status=201,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_complete_character_setup(request, character_id):
    """
    POST /api/v1/characters/<character_id>/complete-setup/
    Completes setup with avatar (and optional gallery images).
    Transitions character from DRAFT to PENDING (regular) or APPROVED (admin).
    """
    try:
        character = Character.objects.filter(character_id=character_id).first()
        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        result = CharacterService.complete_setup(
            character=character,
            payload=request.data,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            if result["data"] == "character_not_in_draft":
                return Response(result, status=400)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_complete_character_setup")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_generate_gallery_image(request, character_id):
    """
    POST /api/v1/characters/<character_id>/gallery/generate/
    Generate an AI image and add to character's gallery.
    Body: { prompt, caption, order, negative_prompt, aspect_ratio, style_preset, seed, extra_prompt }
    """
    try:
        character = Character.objects.filter(character_id=character_id).first()
        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        prompt = request.data.get("prompt", "").strip()
        if not prompt:
            prompt = "portrait, high quality, detailed"

        caption = request.data.get("caption", "")
        extra_prompt = request.data.get("extra_prompt", "").strip()

        order = request.data.get("order")
        if order is not None and order != "":
            try:
                order = int(order)
            except (ValueError, TypeError):
                order = None
        else:
            order = None

        try:
            operation_id, _ = idempotency.resolve(request)
        except InvalidIdempotencyKey:
            return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

        result = CharacterService.generate_gallery_image(
            character=character,
            prompt=prompt,
            caption=caption,
            order=order,
            negative_prompt=request.data.get("negative_prompt", ""),
            aspect_ratio=request.data.get("aspect_ratio", "2:3"),
            style_preset=request.data.get("style_preset", "photographic"),
            seed=int(request.data.get("seed", 0)),
            extra_prompt=extra_prompt,  # <-- passed here
            actor=request.user,
            request=request,
            operation_id=operation_id,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return idempotency.echo(Response(result, status=200), operation_id)

    except Exception:
        logger.exception("Unexpected error in api_generate_gallery_image")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_generate_avatar(request, character_id):
    """
    POST /api/v1/characters/<character_id>/generate-avatar/
    Generate an AI avatar for the character.
    Body: { prompt, negative_prompt, aspect_ratio, style_preset, seed, extra_prompt }
    """
    try:
        character = Character.objects.filter(character_id=character_id).first()
        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        prompt = request.data.get("prompt", "").strip()
        if not prompt:
            prompt = "portrait, high quality, detailed"

        extra_prompt = request.data.get("extra_prompt", "").strip()

        try:
            operation_id, _ = idempotency.resolve(request)
        except InvalidIdempotencyKey:
            return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

        result = CharacterService.generate_avatar(
            character=character,
            prompt=prompt,
            negative_prompt=request.data.get("negative_prompt", ""),
            aspect_ratio=request.data.get("aspect_ratio", "1:1"),
            style_preset=request.data.get("style_preset", "photographic"),
            seed=int(request.data.get("seed", 0)),
            extra_prompt=extra_prompt,  # <-- passed here
            actor=request.user,
            request=request,
            operation_id=operation_id,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_generate_avatar")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ============================================================
# OCCUPATION ENDPOINTS
# ============================================================


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_occupations(request):
    """
    GET /api/occupations/
    Returns a list of all occupations.
    """
    try:
        from capp.classes.occupations.occupation_service import OccupationService

        result = OccupationService.list_occupations(request=request)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Unexpected error in api_list_occupations")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_occupation(request):
    """
    POST /api/occupations/create/
    Creates a new occupation. Admin only.
    """
    try:
        from capp.classes.occupations.occupation_service import OccupationService

        result = OccupationService.create_occupation(request.data, actor=request.user, request=request)
        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)
        return Response(result, status=201)
    except Exception:
        logger.exception("Unexpected error in api_create_occupation")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ============================================================
# HOBBY ENDPOINTS
# ============================================================


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_hobbies(request):
    """
    GET /api/hobbies/
    Returns a list of all hobbies.
    """
    try:
        from capp.classes.hobbies.hobby_service import HobbyService

        result = HobbyService.list_hobbies(request=request)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Unexpected error in api_list_hobbies")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_hobby(request):
    """
    POST /api/hobbies/create/
    Creates a new hobby. Admin only.
    """
    try:
        from capp.classes.hobbies.hobby_service import HobbyService

        result = HobbyService.create_hobby(request.data, actor=request.user, request=request)
        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)
        return Response(result, status=201)
    except Exception:
        logger.exception("Unexpected error in api_create_hobby")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ============================================================
# GALLERY ENDPOINTS
# ============================================================


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def api_upload_gallery_image(request, character_id):
    """
    POST /api/characters/<character_id>/gallery/upload/
    Uploads a new gallery image. Admin only.
    Expects multipart form with: image, caption (optional), order (optional).
    """
    try:
        from capp.classes.characters.character_gallery_service import (
            CharacterGalleryService,
        )

        image_file = request.FILES.get("image")
        caption = request.data.get("caption") or request.POST.get("caption")
        order = request.data.get("order") or request.POST.get("order", 0)

        try:
            order = int(order)
        except (ValueError, TypeError):
            order = 0

        result = CharacterGalleryService.upload_gallery_image(
            character_id=character_id,
            image_file=image_file,
            caption=caption,
            order=order,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            if result["data"] == "character_not_found":
                return Response(result, status=404)
            return Response(result, status=400)

        return Response(result, status=201)

    except Exception:
        logger.exception("Unexpected error in api_upload_gallery_image")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_gallery_images(request, character_id):
    """
    GET /api/characters/<character_id>/gallery/
    Returns all gallery images for a character.
    """
    try:
        from capp.classes.characters.character_gallery_service import (
            CharacterGalleryService,
        )

        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        result = CharacterGalleryService.list_gallery_images(
            character_id=character_id,
            actor=actor,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "character_not_found":
                return Response(result, status=404)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_list_gallery_images")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_gallery_image(request, image_id):
    """
    DELETE /api/gallery/<image_id>/
    Deletes a gallery image. Admin only.
    """
    try:
        from capp.classes.characters.character_gallery_service import (
            CharacterGalleryService,
        )

        result = CharacterGalleryService.delete_gallery_image(
            image_id=image_id,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            if result["data"] == "gallery_image_not_found":
                return Response(result, status=404)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_delete_gallery_image")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_pending_characters(request):
    """
    API view to list all characters with a 'pending' status.
    Access is restricted to administrative users within the service layer.
    """
    try:
        # The service layer handles the admin check and filtering logic
        result = CharacterService.list_pending_characters(actor=request.user, request=request)

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_list_pending_characters")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_delete_character(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        result = CharacterService.delete_character(
            character=character,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_delete_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def api_create_character(request):
    try:
        logger.info("Received data for character creation:", request.data)
        try:
            operation_id, _ = idempotency.resolve(request)
        except InvalidIdempotencyKey:
            return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

        result = CharacterService.create_character(request.data, request=request, operation_id=operation_id)

        if result["status"] != "success":
            return Response(result, status=400)

        return idempotency.echo(Response(result, status=201), operation_id)

    except Exception:
        logger.exception("Unexpected error in api_create_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def api_update_character(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        try:
            operation_id, _ = idempotency.resolve(request)
        except InvalidIdempotencyKey:
            return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

        result = CharacterService.update_character(character, request.data, request=request, operation_id=operation_id)

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return idempotency.echo(Response(result, status=200), operation_id)

    except Exception:
        logger.exception("Unexpected error in api_update_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_user_characters(request):
    """
    GET /api/v1/users/me/characters/
    List all characters created by the currently authenticated user,
    regardless of approval status.
    """
    try:
        result = CharacterService.list_user_characters(
            user=request.user,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_list_user_characters")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_characters(request):
    try:
        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        include_inactive = request.query_params.get("include_inactive")
        featured_only = request.query_params.get("featured_only")
        popular = request.query_params.get("popular")
        category = request.query_params.get("category")
        language = request.query_params.get("language")
        status = request.query_params.get("status")
        search = request.query_params.get("search")
        mine = request.query_params.get("mine")

        created_by = None
        if _to_bool(mine, default=False):
            if not actor:
                return Response(
                    {"status": "fail", "data": "authentication_required"},
                    status=401,
                )
            created_by = actor

        result = CharacterService.list_characters(
            include_inactive=_to_bool(include_inactive, default=False),
            featured_only=_to_bool(featured_only, default=False),
            popular=_to_bool(popular, default=False),
            category=category,
            language=language,
            status=status,
            search=search,
            created_by=created_by,
            actor=actor,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_list_characters")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_get_character_by_id(request, character_id):
    try:
        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        result = CharacterService.get_character_by_id(
            character_id,
            actor=actor,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "character_not_found":
                return Response(result, status=404)
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_get_character_by_id")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_get_character_by_slug(request, slug):
    try:
        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        result = CharacterService.get_character_by_slug(
            slug,
            actor=actor,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "character_not_found":
                return Response(result, status=404)
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_get_character_by_slug")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_set_character_active_status(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        is_active = request.data.get("is_active")
        result = CharacterService.set_active_status(
            character,
            is_active,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_set_character_active_status")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_set_character_featured_status(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        is_featured = request.data.get("is_featured")
        result = CharacterService.set_featured_status(
            character,
            is_featured,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_set_character_featured_status")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_set_character_nsfw_status(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        nsfw_enabled = request.data.get("nsfw_enabled")
        result = CharacterService.set_nsfw_status(
            character,
            nsfw_enabled,
            actor=request.user,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_set_character_nsfw_status")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_approve_character(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        is_active = request.data.get("is_active", True)
        result = CharacterService.approve_character(
            character=character,
            actor=request.user,
            is_active=is_active,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_approve_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_reject_character(request, character_id):
    try:
        character = Character.objects.filter(character_id=character_id).first()

        if not character:
            return Response(
                {"status": "fail", "data": "character_not_found"},
                status=404,
            )

        rejection_reason = request.data.get("rejection_reason")
        result = CharacterService.reject_character(
            character=character,
            actor=request.user,
            rejection_reason=rejection_reason,
            request=request,
        )

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_reject_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


def _to_bool(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False

    if isinstance(value, int):
        return bool(value)

    return default
