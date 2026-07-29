"""
Persist authoritative purchase intent and make fulfillment idempotent in the DB.

Rows written before this migration have no intent to backfill: the price a
customer was actually quoted is not recoverable from them, and reconstructing it
from ``paystack_data`` would mean trusting provider metadata for pricing, which
is exactly what this change exists to stop. The intent columns are therefore
left NULL on those rows, and the new constraints that would be unsafe against
them are conditional on ``package_slug`` being set.

The one thing that *is* backfilled is ``fulfillment_state`` on historical
successes. Marking them ``fulfilled`` is the protective direction: it makes the
replay guard cover legacy references, so an old ``charge.success`` redelivery
cannot credit a second time. The cost is that a legacy success whose crediting
had actually failed will not self-heal — those need the manual reconciliation
query documented in ``Backend/docs/PAYSTACK_TRANSACTION_INTEGRITY.md``.

Legacy rows still ``pending`` stay unfulfillable, because a payment with no
recorded intent has nothing to validate against. That is deliberate: they are
listed by the same reconciliation query rather than credited on a guess.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def check_legacy_rows_are_constrainable(apps, schema_editor):
    """
    Refuse to proceed with a clear message rather than a raw constraint error.

    Neither of these can be repaired by guessing — a non-positive amount and a
    row owned by both an account and a guest are both states we must not
    invent a correction for — so the operator is told exactly what to reconcile.
    """
    PaystackTransaction = apps.get_model("paystack_app", "PaystackTransaction")

    non_positive = PaystackTransaction.objects.filter(amount__lte=0).count()
    if non_positive:
        raise RuntimeError(
            f"{non_positive} paystack_transactions row(s) have amount <= 0 and cannot satisfy "
            "the paystack_txn_amount_positive constraint. Reconcile these rows manually "
            "(see Backend/docs/PAYSTACK_TRANSACTION_INTEGRITY.md) before migrating."
        )

    dual_owner = PaystackTransaction.objects.filter(
        user__isnull=False,
        guest_session_id__isnull=False,
    ).count()
    if dual_owner:
        raise RuntimeError(
            f"{dual_owner} paystack_transactions row(s) have both a user and a guest_session_id and "
            "cannot satisfy the paystack_txn_single_owner constraint. Reconcile these rows manually "
            "(see Backend/docs/PAYSTACK_TRANSACTION_INTEGRITY.md) before migrating."
        )


def mark_historical_successes_fulfilled(apps, schema_editor):
    PaystackTransaction = apps.get_model("paystack_app", "PaystackTransaction")
    PaystackTransaction.objects.filter(status="success").update(fulfillment_state="fulfilled")


def unmark_historical_successes(apps, schema_editor):
    PaystackTransaction = apps.get_model("paystack_app", "PaystackTransaction")
    PaystackTransaction.objects.filter(fulfillment_state="fulfilled").update(fulfillment_state="unfulfilled")


class Migration(migrations.Migration):

    dependencies = [
        ("paystack_app", "0003_paystacktransaction_claimed_from_guest_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            check_legacy_rows_are_constrainable,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name="PaystackFulfillment",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "fulfillment_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("reference", models.CharField(max_length=100, unique=True)),
                (
                    "source",
                    models.CharField(
                        choices=[("webhook", "Webhook"), ("manual_verification", "Manual verification")], max_length=32
                    ),
                ),
                ("package_slug", models.CharField(max_length=120)),
                (
                    "package_type",
                    models.CharField(
                        choices=[("spark", "Spark package"), ("subscription", "Subscription plan")], max_length=20
                    ),
                ),
                ("amount_minor", models.BigIntegerField()),
                ("currency", models.CharField(max_length=3)),
                ("sparks_granted", models.BigIntegerField(default=0)),
            ],
            options={
                "db_table": "paystack_fulfillments",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="callback_path",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="expected_amount_minor",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="fulfillment_state",
            field=models.CharField(
                choices=[
                    ("unfulfilled", "Unfulfilled"),
                    ("fulfilled", "Fulfilled"),
                    ("manual_review", "Awaiting manual review"),
                ],
                default="unfulfilled",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="package_slug",
            field=models.CharField(blank=True, db_index=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="paystacktransaction",
            name="package_type",
            field=models.CharField(
                blank=True,
                choices=[("spark", "Spark package"), ("subscription", "Subscription plan")],
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(
            mark_historical_successes_fulfilled,
            unmark_historical_successes,
        ),
        migrations.AddIndex(
            model_name="paystacktransaction",
            index=models.Index(fields=["status", "fulfillment_state"], name="paystack_tr_status_151bda_idx"),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(("user__isnull", True), ("guest_session_id__isnull", True), _connector="OR"),
                name="paystack_txn_single_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("package_slug__isnull", True),
                    ("user__isnull", False),
                    ("guest_session_id__isnull", False),
                    ("origin_guest_session_id__isnull", False),
                    _connector="OR",
                ),
                name="paystack_txn_intent_has_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)), name="paystack_txn_amount_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("expected_amount_minor__isnull", True), ("expected_amount_minor__gt", 0), _connector="OR"
                ),
                name="paystack_txn_expected_amount_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="paystacktransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("package_slug__isnull", True),
                    models.Q(
                        ("package_type__isnull", False),
                        ("expected_amount_minor__isnull", False),
                        models.Q(("currency", ""), _negated=True),
                    ),
                    _connector="OR",
                ),
                name="paystack_txn_intent_is_complete",
            ),
        ),
        migrations.AddField(
            model_name="paystackfulfillment",
            name="transaction",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="fulfillment",
                to="paystack_app.paystacktransaction",
            ),
        ),
    ]
