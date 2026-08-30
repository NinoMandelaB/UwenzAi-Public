# capp/urls/push_urls.py
from django.urls import path
from capp.views.push_views import (
    api_get_vapid_public_key,
    api_subscribe_push,
    api_unsubscribe_push,
)

urlpatterns = [
    path(
        "vapid-public-key/",
        api_get_vapid_public_key,
        name="api_get_vapid_public_key",
    ),
    path(
        "subscribe/",
        api_subscribe_push,
        name="api_subscribe_push",
    ),
    path(
        "unsubscribe/",
        api_unsubscribe_push,
        name="api_unsubscribe_push",
    ),
]
