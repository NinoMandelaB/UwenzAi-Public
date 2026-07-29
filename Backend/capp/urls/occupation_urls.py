from django.urls import path
from capp.views.character_views import api_list_occupations, api_create_occupation

urlpatterns = [
    path("", api_list_occupations),
    path("create/", api_create_occupation),
]
