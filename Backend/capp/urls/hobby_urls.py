from django.urls import path
from capp.views.character_views import api_list_hobbies, api_create_hobby

urlpatterns = [
    path("", api_list_hobbies),
    path("create/", api_create_hobby),
]
