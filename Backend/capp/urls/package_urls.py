# capp/urls/package_urls.py
from django.urls import path
from capp.views.package_views import (
    current_subscription,
    list_packages,
    admin_list_spark_packages,
    admin_create_spark_package,
    admin_update_spark_package,
    admin_delete_spark_package,
    admin_list_subscription_plans,
    admin_create_subscription_plan,
    admin_update_subscription_plan,
    admin_delete_subscription_plan,
    spark_pool_balance,
    spark_transactions,
)

urlpatterns = [
    # Public
    path("packages/", list_packages, name="list_packages"),
    # Admin – spark packages
    path(
        "admin/packages/spark-packages/",
        admin_list_spark_packages,
        name="admin_list_spark_packages",
    ),
    path(
        "admin/packages/spark-packages/create/",
        admin_create_spark_package,
        name="admin_create_spark_package",
    ),
    path(
        "admin/packages/spark-packages/<slug:slug>/update/",
        admin_update_spark_package,
        name="admin_update_spark_package",
    ),
    path(
        "admin/packages/spark-packages/<slug:slug>/delete/",
        admin_delete_spark_package,
        name="admin_delete_spark_package",
    ),
    # Admin – subscription plans
    path(
        "admin/packages/subscription-plans/",
        admin_list_subscription_plans,
        name="admin_list_subscription_plans",
    ),
    path(
        "admin/packages/subscription-plans/create/",
        admin_create_subscription_plan,
        name="admin_create_subscription_plan",
    ),
    path(
        "admin/packages/subscription-plans/<slug:slug>/update/",
        admin_update_subscription_plan,
        name="admin_update_subscription_plan",
    ),
    path(
        "admin/packages/subscription-plans/<slug:slug>/delete/",
        admin_delete_subscription_plan,
        name="admin_delete_subscription_plan",
    ),
    path("spark_pool/", spark_pool_balance, name="spark_pool_balance"),
    path("spark_pool/transactions/", spark_transactions, name="spark_transactions"),
    path("subscription/", current_subscription, name="current_subscription"),
]
