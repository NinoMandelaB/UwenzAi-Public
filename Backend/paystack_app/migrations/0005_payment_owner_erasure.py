# Permit explicitly erased payment owners without weakening new-intent ownership.

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("paystack_app", "0004_transaction_intent_and_fulfillment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="paystacktransaction",
            name="paystack_txn_intent_has_owner",
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="owner_deleted_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("package_slug__isnull", True),
                    ("user__isnull", False),
                    ("guest_session_id__isnull", False),
                    ("origin_guest_session_id__isnull", False),
                    ("owner_deleted_at__isnull", False),
                    _connector="OR",
                ),
                name="paystack_txn_intent_has_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("owner_deleted_at__isnull", True),
                    models.Q(("user__isnull", True), ("guest_session_id__isnull", True)),
                    _connector="OR",
                ),
                name="paystack_txn_erased_has_no_owner",
            ),
        ),
    ]
