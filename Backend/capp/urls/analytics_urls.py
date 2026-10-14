# capp/urls/analytics_urls.py
# Public analytics ping for the audience feature. Mounted at /api/v1/analytics/.

from django.urls import path

from capp.views.analytics_views import api_record_visit

urlpatterns = [
    path("visit/", api_record_visit, name="record-visit"),
]
