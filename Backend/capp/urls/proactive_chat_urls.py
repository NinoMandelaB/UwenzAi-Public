# capp/urls/proactive_chat_urls.py
from django.urls import path
from capp.views.proactive_chat_views import (
    api_get_proactive_preference,
    api_update_proactive_preference,
)

urlpatterns = [
    path(
        "preference/<uuid:character_id>/",
        api_get_proactive_preference,
        name="api_get_proactive_preference",
    ),
    path(
        "preference/<uuid:character_id>/update/",
        api_update_proactive_preference,
        name="api_update_proactive_preference",
    ),
]
