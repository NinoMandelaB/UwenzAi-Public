from django.urls import path
from capp.views.character_views import (
    api_delete_personality,
    api_list_personalities,
    api_create_personality,
)

urlpatterns = [
    path("", api_list_personalities),
    path("create/", api_create_personality),
    path("<int:personality_id>/delete/", api_delete_personality),
]
