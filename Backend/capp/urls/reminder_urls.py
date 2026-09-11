# capp/urls/reminder_urls.py
from django.urls import path
from capp.views.reminder_views import (
    api_list_reminders,
    api_create_reminder,
    api_update_reminder,
    api_delete_reminder,
)

urlpatterns = [
    path("", api_list_reminders, name="api_list_reminders"),
    path("create/", api_create_reminder, name="api_create_reminder"),
    path("<uuid:reminder_id>/update/", api_update_reminder, name="api_update_reminder"),
    path("<uuid:reminder_id>/delete/", api_delete_reminder, name="api_delete_reminder"),
]
