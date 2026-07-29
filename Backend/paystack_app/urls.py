# paystack_app/urls.py
from django.urls import path
from .views import initiate_payment, paystack_webhook, verify_payment

urlpatterns = [
    path("pay/", initiate_payment, name="paystack_initiate"),
    path("webhook/", paystack_webhook, name="paystack_webhook"),
    path("verify/", verify_payment, name="paystack_verify"),
]
