# capp/urls/billing_urls.py
from django.urls import path
from capp.views.billing_views import (
    api_pricing,
    messages_today_count,
    spark_transactions,
)

urlpatterns = [
    path(
        "spark_pool/transactions/paginated/",
        spark_transactions,
        name="spark_transactions_paginated",
    ),
    path("messages/today-count/", messages_today_count, name="messages_today_count"),
    path("pricing/", api_pricing, name="api_pricing"),
]
