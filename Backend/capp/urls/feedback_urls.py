from django.urls import path
from capp.views.feedback_views import (
    api_submit_feedback,
    api_list_feedback,
    api_mark_feedback_read,
    api_delete_feedback,  # new import
)

urlpatterns = [
    path("submit/", api_submit_feedback, name="api_submit_feedback"),
    path("admin/", api_list_feedback, name="api_list_feedback"),
    path(
        "admin/<uuid:feedback_id>/read/",
        api_mark_feedback_read,
        name="api_mark_feedback_read",
    ),
    path(
        "admin/<uuid:feedback_id>/delete/",
        api_delete_feedback,
        name="api_delete_feedback",
    ),  # new
]
