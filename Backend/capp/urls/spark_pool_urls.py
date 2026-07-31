# capp/urls/spark_pool_urls.py

from django.urls import path
from capp.views.spark_pool_views import spark_pool_balance

urlpatterns = [
    path("balance/", spark_pool_balance, name="spark_pool-balance"),
]
