from django.urls import path
from capp.views.token_views import api_token_balance

urlpatterns = [
    path("balance/", api_token_balance, name="token-balance"),
]
