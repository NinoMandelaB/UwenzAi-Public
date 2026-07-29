import logging
from typing import Dict, Any, Optional

from django.db.models import Q
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from capp.models import Character, CharacterGalleryImage

logger = logging.getLogger(__name__)


class CharacterGalleryService:
    """
    Service for managing character gallery images.
    Admins can upload and delete; anyone can view.
    """

    ALLOWED_CONTENT_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
    MIN_WIDTH = 400
    MIN_HEIGHT = 400

    @classmethod
    def upload_gallery_image(
        cls,
        character_id: str,
        image_file,
        caption: Optional[str] = None,
        order: int = 0,
        actor=None,
        request=None,
    ) -> Dict[str, Any]:
        """
        Upload a new gallery image for a character. Admin only.
        """
        try:
            # Permission check
            if not cls._is_admin_user(actor):
                return {"status": "fail", "data": "permission_denied"}

            # Validate character exists
            try:
                character = Character.objects.get(character_id=character_id)
            except Character.DoesNotExist:
                return {"status": "fail", "data": "character_not_found"}

            # Validate image file
            validation_error = cls._validate_gallery_image_file(image_file)
            if validation_error:
                return {"status": "fail", "data": validation_error}

            caption = (caption or "").strip()
            if len(caption) > 200:
                return {"status": "fail", "data": "caption_too_long"}

            # Determine image source (admin upload)
            source = Character.AvatarSource.UPLOADED

            with transaction.atomic():
                # Inside upload_gallery_image, after creating the gallery_image:
                gallery_image = CharacterGalleryImage.objects.create(
                    character=character,
                    image=image_file,
                    caption=caption,
                    order=order,
                    source=source,
                    created_by=actor,
                    visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
                )

            logger.info(
                "Gallery image uploaded successfully image_id=%s character_id=%s uploaded_by=%s",
                gallery_image.id,
                character_id,
                getattr(actor, "user_id", None),
            )

            return {
                "status": "success",
                "data": {
                    "message": "gallery_image_uploaded_successfully",
                    "image": cls._serialize_gallery_image(gallery_image, request=request, actor=actor),
                },
            }

        except Exception:
            logger.exception("Unexpected error uploading gallery image")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def list_gallery_images(
        cls,
        character_id: str,
        actor=None,
        request=None,
    ) -> Dict[str, Any]:
        try:
            try:
                character = Character.objects.get(character_id=character_id)
            except Character.DoesNotExist:
                return {"status": "fail", "data": "character_not_found"}

            queryset = CharacterGalleryImage.objects.filter(character=character).order_by("order", "created_at")

            # Ownership alone decides what this list contains: PUBLIC images for
            # everyone, PRIVATE images only for the account that created them.
            # ``is_staff`` / ``is_superuser`` deliberately do not widen it — a
            # moderator browsing the ordinary gallery must not be handed another
            # user's private chat-generated pictures. Any need-to-know access
            # belongs in separate, audited moderation tooling, not here.
            if actor and getattr(actor, "is_authenticated", False):
                queryset = queryset.filter(
                    Q(visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC) | Q(created_by=actor)
                )
            else:
                # Anonymous and guest callers: public only. A guest's own private
                # images have no ``created_by`` to match on, and matching NULL
                # here would hand every guest's private images to every caller.
                queryset = queryset.filter(visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC)

            data = []
            for position, img in enumerate(queryset):
                entry = cls._serialize_gallery_image(img, request=request, actor=actor)
                # Position within the visible list rather than the stored index,
                # so a private image the caller cannot see leaves no numbering
                # gap that would disclose that it exists.
                entry["order"] = position
                data.append(entry)

            return {
                "status": "success",
                "data": {
                    "images": data,
                    "count": len(data),
                },
            }

        except Exception:
            logger.exception("Unexpected error listing gallery images")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def delete_gallery_image(
        cls,
        image_id: str,
        actor=None,
        request=None,
    ) -> Dict[str, Any]:
        """
        Delete a gallery image. Admin or image owner only.
        """
        try:
            if not (actor and getattr(actor, "is_authenticated", False)):
                return {"status": "fail", "data": "permission_denied"}

            try:
                image = CharacterGalleryImage.objects.get(id=image_id)
            except CharacterGalleryImage.DoesNotExist:
                return {"status": "fail", "data": "gallery_image_not_found"}

            # Admins can always delete; otherwise only the image creator
            if not cls._is_admin_user(actor) and image.created_by != actor:
                return {"status": "fail", "data": "permission_denied"}

            character_id = image.character.character_id
            image.delete()

            logger.info(
                "Gallery image deleted image_id=%s character_id=%s deleted_by=%s",
                image_id,
                character_id,
                getattr(actor, "user_id", None),
            )

            return {
                "status": "success",
                "data": {
                    "message": "gallery_image_deleted_successfully",
                    "image_id": image_id,
                },
            }

        except Exception:
            logger.exception("Unexpected error deleting gallery image")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _validate_gallery_image_file(cls, image_file) -> Optional[str]:
        """
        Validate image file: content type, size, dimensions.
        Returns error string or None.
        """
        if not image_file:
            return "image_required"

        content_type = getattr(image_file, "content_type", None)
        if content_type not in cls.ALLOWED_CONTENT_TYPES:
            return "image_invalid_type"

        size = getattr(image_file, "size", None)
        if size is not None and size > cls.MAX_FILE_SIZE_BYTES:
            return "image_file_too_large"

        try:
            width, height = cls._get_image_dimensions(image_file)
        except UnidentifiedImageError:
            return "image_invalid_image"
        except Exception:
            logger.exception("Unexpected error while validating image dimensions")
            return "image_invalid_image"

        if width < cls.MIN_WIDTH or height < cls.MIN_HEIGHT:
            return "image_too_small"

        return None

    @classmethod
    def _get_image_dimensions(cls, image_file) -> tuple:
        """
        Get image dimensions while preserving file pointer position.
        """
        current_position = None
        if hasattr(image_file, "tell"):
            try:
                current_position = image_file.tell()
            except Exception:
                current_position = None

        try:
            if hasattr(image_file, "seek"):
                image_file.seek(0)

            with Image.open(image_file) as img:
                img.verify()

            if hasattr(image_file, "seek"):
                image_file.seek(0)

            with Image.open(image_file) as img:
                width, height = img.size

            return width, height

        finally:
            if current_position is not None and hasattr(image_file, "seek"):
                try:
                    image_file.seek(current_position)
                except Exception:
                    pass

    @classmethod
    def _serialize_gallery_image(cls, image: CharacterGalleryImage, request=None, actor=None) -> Dict[str, Any]:
        image_url = None
        if image.image and hasattr(image.image, "url"):
            try:
                raw_url = image.image.url
                if request:
                    image_url = request.build_absolute_uri(raw_url)
                else:
                    image_url = raw_url
            except Exception:
                image_url = None

        resp = {
            "id": str(image.id),
            "character_id": str(image.character.character_id),
            "image_url": image_url,
            "caption": image.caption,
            "order": image.order,
            "source": image.source,
            "created_at": image.created_at.isoformat() if image.created_at else None,
            "updated_at": image.updated_at.isoformat() if image.updated_at else None,
            # Always include these fields – needed for UI permissions
            "created_by": str(image.created_by.user_id) if image.created_by else None,
            "visibility": image.visibility,
        }
        return resp

    @classmethod
    def _is_admin_user(cls, actor) -> bool:
        """Check if the actor is an admin."""
        return bool(
            actor
            and getattr(actor, "is_authenticated", False)
            and (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))
        )
