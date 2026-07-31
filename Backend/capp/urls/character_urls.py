from django.urls import path
from capp.views.character_views import (
    api_create_character,
    api_create_personality,
    api_flag_character,
    api_generate_avatar,
    api_generate_gallery_image,
    api_list_flagged_characters,
    api_list_personalities,
    api_list_user_characters,
    api_update_character,
    api_list_characters,
    api_list_pending_characters,
    api_get_character_by_id,
    api_get_character_by_slug,
    api_set_character_active_status,
    api_set_character_featured_status,
    api_set_character_nsfw_status,
    api_approve_character,
    api_reject_character,
    api_delete_character,
    api_list_occupations,
    api_create_occupation,
    api_list_hobbies,
    api_create_hobby,
    api_upload_gallery_image,
    api_list_gallery_images,
    api_delete_gallery_image,
    api_complete_character_setup,
    api_clear_character_flags,  # new import
)

urlpatterns = [
    path("", api_list_characters),
    path("my-characters/", api_list_user_characters),
    path("pending/", api_list_pending_characters),
    path("create/", api_create_character),
    # ============================================================
    # OCCUPATION ROUTES
    # ============================================================
    path("occupations/", api_list_occupations),
    path("occupations/create/", api_create_occupation),
    # ============================================================
    # HOBBY ROUTES
    # ============================================================
    path("hobbies/", api_list_hobbies),
    path("hobbies/create/", api_create_hobby),
    # ============================================================
    # PERSONALITY ROUTES
    # ============================================================
    path("personalities/", api_list_personalities),
    path("personalities/create/", api_create_personality),
    # ============================================================
    # GALLERY ROUTES (static paths must come BEFORE dynamic UUID)
    # ============================================================
    path("gallery/<int:image_id>/delete/", api_delete_gallery_image),
    # ============================================================
    # CHARACTER-SPECIFIC ROUTES (dynamic UUID patterns)
    # ============================================================
    path("<uuid:character_id>/", api_get_character_by_id),
    path("slug/<slug:slug>/", api_get_character_by_slug),
    path("<uuid:character_id>/update/", api_update_character),
    path("<uuid:character_id>/set-active/", api_set_character_active_status),
    path("<uuid:character_id>/set-featured/", api_set_character_featured_status),
    path("<uuid:character_id>/set-nsfw/", api_set_character_nsfw_status),
    path("<uuid:character_id>/approve/", api_approve_character),
    path("<uuid:character_id>/reject/", api_reject_character),
    path("<uuid:character_id>/delete/", api_delete_character),
    path("<uuid:character_id>/generate-avatar/", api_generate_avatar),
    path("<uuid:character_id>/complete-setup/", api_complete_character_setup),
    # Gallery sub-routes for a specific character
    path("<uuid:character_id>/gallery/", api_list_gallery_images),
    path("<uuid:character_id>/gallery/upload/", api_upload_gallery_image),
    path("<uuid:character_id>/gallery/generate/", api_generate_gallery_image),
    # Flagging routes
    path("<uuid:character_id>/flag/", api_flag_character),
    path("flagged/", api_list_flagged_characters),
    path("<uuid:character_id>/clear-flags/", api_clear_character_flags),  # new URL
]
