# Retain accounting records while deleting account, balance and private usage logs.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0088_auth_code_hmac"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymenttransaction",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="sparktransaction",
            name="spark_pool",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to="capp.sparkpool",
            ),
        ),
        migrations.AlterField(
            model_name="sparktransaction",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spark_transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="usagecharge",
            name="token_usage_log",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="usage_charge",
                to="capp.tokenusagelog",
            ),
        ),
        migrations.AlterField(
            model_name="usagecharge",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="usage_charges",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
