# play_billing_app/urls.py
from django.urls import path

from .views import verify_play_purchase

urlpatterns = [
    path("verify/", verify_play_purchase, name="play_billing_verify"),
]
