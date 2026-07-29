# capp/urls/memory_urls.py

from django.urls import path
from capp.views.memory_views import api_clear_memory_for_character

urlpatterns = [
    path(
        "clear/<uuid:character_id>/",
        api_clear_memory_for_character,
        name="api_clear_memory_for_character",
    ),
]
