# capp/urls/memory_urls.py

from django.urls import path

from capp.views.memory_views import (
    api_clear_memory_for_character,
    api_create_memory_fact,
    api_create_memory_relation,
    api_delete_memory_fact,
    api_delete_memory_relation,
    api_delete_memory_summary,
    api_list_memory,
    api_update_memory_fact,
    api_update_memory_relation,
    api_update_memory_summary,
)

urlpatterns = [
    path(
        "clear/<uuid:character_id>/",
        api_clear_memory_for_character,
        name="api_clear_memory_for_character",
    ),
    # ── Memory editor (brain icon popup, logged-in users only) ──
    path(
        "editor/<uuid:character_id>/",
        api_list_memory,
        name="api_list_memory",
    ),
    path(
        "editor/<uuid:character_id>/facts/",
        api_create_memory_fact,
        name="api_create_memory_fact",
    ),
    path(
        "editor/facts/<uuid:fact_id>/",
        api_update_memory_fact,
        name="api_update_memory_fact",
    ),
    path(
        "editor/facts/<uuid:fact_id>/delete/",
        api_delete_memory_fact,
        name="api_delete_memory_fact",
    ),
    path(
        "editor/<uuid:character_id>/relations/",
        api_create_memory_relation,
        name="api_create_memory_relation",
    ),
    path(
        "editor/relations/<uuid:relation_id>/",
        api_update_memory_relation,
        name="api_update_memory_relation",
    ),
    path(
        "editor/relations/<uuid:relation_id>/delete/",
        api_delete_memory_relation,
        name="api_delete_memory_relation",
    ),
    path(
        "editor/<uuid:character_id>/summary/",
        api_update_memory_summary,
        name="api_update_memory_summary",
    ),
    path(
        "editor/<uuid:character_id>/summary/delete/",
        api_delete_memory_summary,
        name="api_delete_memory_summary",
    ),
]
