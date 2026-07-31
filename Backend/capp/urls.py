from django.urls import path

from capp.views.system_views import ping_view

urlpatterns = [
    path("api/ping/", ping_view, name="ping"),
]
