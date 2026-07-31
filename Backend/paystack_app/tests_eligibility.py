# paystack_app/tests_eligibility.py
"""
Eligibility before capture, and honest compensation state after it.

Two properties are under test. First, everything that could make a purchase
undeliverable is checked *before* the customer reaches Paystack, so the platform
does not take money it cannot honour. Second, when something becomes
undeliverable between initiation and capture — the only window we cannot close —
the capture is recorded and queued for manual compensation rather than being
silently kept or silently rolled back.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from capp.classes.billing.charge_service import ChargeService
from capp.models import (
    SparkPackage,
    SparkPool,
    SubscriptionPlan,
    UserSubscription,
)
from paystack_app.models import PaystackFulfillment, PaystackTransaction
from paystack_app.services import SUBSCRIPTION_UPGRADE_THRESHOLD, PaystackService
from paystack_app.tests import make_user

callback_settings = override_settings(
    PAYMENT_CALLBACK_ALLOWED_ORIGINS=["https://app.uwenzai.com"],
    PAYMENT_CALLBACK_ALLOWED_PATHS=["/payment/verify"],
    PAYMENT_CALLBACK_DEFAULT_PATH="/payment/verify",
    PAYSTACK_SUPPORTED_CURRENCIES=["KES"],
)


def init_response(reference="ref_new_001"):
    response = mock.Mock(status_code=200)
    response.json.return_value = {
        "status": True,
        "data": {
            "authorization_url": "https://checkout.paystack.com/abc",
            "access_code": "abc",
            "reference": reference,
        },
    }
    return response


def make_spark_package(slug="starter", sparks=1000, price="100.00", active=True):
    return SparkPackage.objects.create(
        slug=slug,
        name=slug.title(),
        base_spark_amount=sparks,
        base_price_kes=Decimal(price),
        discount_percent=Decimal("0.00"),
        final_price_kes=Decimal(price),
        is_active=active,
    )


def make_plan(slug="pro", price="500.00", currency="KES", sparks=5000, active=True):
    return SubscriptionPlan.objects.create(
        name=slug.title(),
        slug=slug,
        price=Decimal(price),
        currency=currency,
        billing_cycle=SubscriptionPlan.BillingCycle.MONTHLY,
        included_sparks=sparks,
        is_active=active,
    )


@callback_settings
class PreCaptureEligibilityTests(TestCase):
    """Nothing reaches Paystack unless it can be delivered when it comes back."""

    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)

    def _initiate(self, slug="starter", package_type="spark", **kwargs):
        with mock.patch("paystack_app.services.requests.post", return_value=init_response()) as post:
            result = ChargeService.initiate_payment_from_package(
                user=self.user,
                package_slug=slug,
                package_type=package_type,
                callback_path="/payment/verify",
                **kwargs,
            )
        return result, post

    def test_unknown_package_is_refused_before_any_provider_call(self):
        result, post = self._initiate(slug="does-not-exist")
        self.assertEqual(result["data"], "package_not_found")
        post.assert_not_called()
        self.assertFalse(PaystackTransaction.objects.exists())

    def test_inactive_package_is_refused(self):
        make_spark_package(active=False)
        result, post = self._initiate()
        self.assertEqual(result["data"], "package_not_found")
        post.assert_not_called()

    def test_unknown_package_type_is_refused(self):
        result, post = self._initiate(package_type="lifetime")
        self.assertEqual(result["data"], "invalid_package_type")
        post.assert_not_called()

    def test_zero_priced_package_is_refused_rather_than_charged(self):
        make_spark_package(price="0.00")
        result, post = self._initiate()
        self.assertEqual(result["data"], "package_price_invalid")
        post.assert_not_called()

    def test_plan_in_an_unsupported_currency_is_refused_not_substituted(self):
        """
        Substituting KES for a USD price would capture roughly a hundredth of the
        real amount, so the plan is refused until it is priced properly.
        """
        make_plan(currency="USD")
        result, post = self._initiate(slug="pro", package_type="subscription")
        self.assertEqual(result["data"], "currency_not_supported")
        post.assert_not_called()

    def test_active_plan_with_sparks_left_cannot_start_another(self):
        """
        The gate that used to run only *after* capture: a customer with a full
        plan would pay and then be told the upgrade was refused.
        """
        plan = make_plan()
        UserSubscription.objects.create(
            user=self.user,
            subscription_plan=plan,
            status=UserSubscription.StatusChoices.ACTIVE,
            sparks_granted=5000,
            sparks_remaining=SUBSCRIPTION_UPGRADE_THRESHOLD + 1,
            ends_at=timezone.now() + timedelta(days=20),
        )
        result, post = self._initiate(slug="pro", package_type="subscription")
        self.assertEqual(result["data"], "too_many_sparks_remaining")
        post.assert_not_called()
        self.assertFalse(PaystackTransaction.objects.exists())

    def test_nearly_exhausted_plan_may_upgrade(self):
        plan = make_plan()
        UserSubscription.objects.create(
            user=self.user,
            subscription_plan=plan,
            status=UserSubscription.StatusChoices.ACTIVE,
            sparks_granted=5000,
            sparks_remaining=SUBSCRIPTION_UPGRADE_THRESHOLD - 1,
            ends_at=timezone.now() + timedelta(days=20),
        )
        result, post = self._initiate(slug="pro", package_type="subscription")
        self.assertEqual(result["status"], "success")
        post.assert_called_once()

    def test_expired_plan_does_not_block_a_new_one(self):
        plan = make_plan()
        UserSubscription.objects.create(
            user=self.user,
            subscription_plan=plan,
            status=UserSubscription.StatusChoices.EXPIRED,
            sparks_granted=5000,
            sparks_remaining=5000,
            ends_at=timezone.now() - timedelta(days=1),
        )
        result, _ = self._initiate(slug="pro", package_type="subscription")
        self.assertEqual(result["status"], "success")

    def test_guest_cannot_subscribe(self):
        make_plan()
        with mock.patch("paystack_app.services.requests.post", return_value=init_response()) as post:
            result = ChargeService.initiate_payment_from_package(
                user=None,
                guest_session_id="some-guest",
                package_slug="pro",
                package_type="subscription",
                callback_path="/payment/verify",
            )
        self.assertEqual(result["data"], "guests_cannot_subscribe")
        post.assert_not_called()


@callback_settings
class RecordedIntentTests(TestCase):
    """What the server writes down before the customer leaves."""

    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)
        make_spark_package(price="149.50")

    def test_intent_records_the_server_side_price_in_minor_units(self):
        with mock.patch("paystack_app.services.requests.post", return_value=init_response()):
            result = ChargeService.initiate_payment_from_package(
                user=self.user,
                package_slug="starter",
                package_type="spark",
                callback_path="/payment/verify",
            )
        self.assertEqual(result["status"], "success")
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.package_slug, "starter")
        self.assertEqual(txn.package_type, PaystackTransaction.PackageType.SPARK)
        self.assertEqual(txn.expected_amount_minor, 14950)
        self.assertEqual(txn.currency, "KES")
        self.assertEqual(txn.user_id, self.user.user_id)
        self.assertEqual(txn.callback_path, "/payment/verify")
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.UNFULFILLED)

    def test_only_an_allowlisted_absolute_url_is_sent_to_paystack(self):
        with mock.patch("paystack_app.services.requests.post", return_value=init_response()) as post:
            ChargeService.initiate_payment_from_package(
                user=self.user,
                package_slug="starter",
                package_type="spark",
                callback_path="/payment/verify",
            )
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["callback_url"], "https://app.uwenzai.com/payment/verify")

    def test_guest_identity_is_not_sent_to_paystack(self):
        """Neither the metadata nor the placeholder email may carry the ownership ID."""
        owner_key = "11111111-2222-3333-4444-555555555555"
        with mock.patch("paystack_app.services.requests.post", return_value=init_response()) as post:
            ChargeService.initiate_payment_from_package(
                user=None,
                guest_session_id=owner_key,
                package_slug="starter",
                package_type="spark",
                callback_path="/payment/verify",
            )
        sent = post.call_args.kwargs["json"]
        self.assertNotIn(owner_key, str(sent))
        self.assertNotIn(owner_key, sent["email"])
        # Ownership still lives in our own row, which is where it belongs.
        self.assertEqual(PaystackTransaction.objects.get().guest_session_id, owner_key)

    def test_provider_failure_leaves_the_intent_pending_and_retryable(self):
        import requests

        with mock.patch(
            "paystack_app.services.requests.post",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            result = ChargeService.initiate_payment_from_package(
                user=self.user,
                package_slug="starter",
                package_type="spark",
                callback_path="/payment/verify",
            )
        self.assertEqual(result["status"], "fail")
        txn = PaystackTransaction.objects.get()
        # Not FAILED: we do not know that Paystack did nothing, so the row stays
        # in a state a later webhook or verification can still resolve.
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.PENDING)


class PostCaptureCompensationTests(TestCase):
    """
    When capture succeeds but delivery cannot, the money is visibly owed.

    The alternative — rolling the whole thing back — would leave the customer
    charged with no record at all, which is the worse failure.
    """

    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)

    def _txn(self, package_type, slug, minor=50000):
        return PaystackTransaction.objects.create(
            user=self.user,
            reference="ref_capture_001",
            amount=Decimal(minor) / 100,
            currency="KES",
            status=PaystackTransaction.StatusChoices.PENDING,
            package_slug=slug,
            package_type=package_type,
            expected_amount_minor=minor,
        )

    def _fulfill(self, minor=50000):
        return PaystackService.fulfill_reference(
            "ref_capture_001",
            {
                "id": 1,
                "reference": "ref_capture_001",
                "status": "success",
                "amount": minor,
                "currency": "KES",
            },
            source=PaystackFulfillment.Source.WEBHOOK,
        )

    def _sparks(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def test_package_deactivated_while_the_customer_was_paying(self):
        make_spark_package(active=False, price="500.00")
        self._txn(PaystackTransaction.PackageType.SPARK, "starter")

        result = self._fulfill()
        self.assertEqual(result["data"], "spark_package_unavailable")
        self.assertEqual(self._sparks(), 0)

        txn = PaystackTransaction.objects.get()
        # SUCCESS + manual_review is the "we have their money and owe them value"
        # state. It must not read as a plain success, and must not read as failed.
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.SUCCESS)
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)
        self.assertFalse(PaystackFulfillment.objects.exists())

    def test_package_deleted_while_the_customer_was_paying(self):
        self._txn(PaystackTransaction.PackageType.SPARK, "vanished")
        result = self._fulfill()
        self.assertEqual(result["data"], "spark_package_unavailable")
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)

    def test_subscription_conflict_acquired_while_on_the_payment_page(self):
        """
        The window we cannot close: the customer gained sparks after we cleared
        them to pay. The capture is recorded and owed, not thrown away.
        """
        plan = make_plan()
        UserSubscription.objects.create(
            user=self.user,
            subscription_plan=plan,
            status=UserSubscription.StatusChoices.ACTIVE,
            sparks_granted=5000,
            sparks_remaining=SUBSCRIPTION_UPGRADE_THRESHOLD + 10,
            ends_at=timezone.now() + timedelta(days=20),
        )
        self._txn(PaystackTransaction.PackageType.SUBSCRIPTION, "pro")

        result = self._fulfill()
        self.assertEqual(result["data"], "too_many_sparks_remaining")
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.SUCCESS)
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)
        self.assertFalse(PaystackFulfillment.objects.exists())

    def test_plan_deactivated_while_the_customer_was_paying(self):
        make_plan(active=False)
        self._txn(PaystackTransaction.PackageType.SUBSCRIPTION, "pro")
        result = self._fulfill()
        self.assertEqual(result["data"], "subscription_plan_unavailable")
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)

    def test_a_held_capture_is_not_retried_into_a_double_credit(self):
        """
        Once held for review the reference is closed to automatic fulfillment, so
        a redelivered webhook cannot credit it behind an operator's back.
        """
        make_spark_package(active=False, price="500.00")
        self._txn(PaystackTransaction.PackageType.SPARK, "starter")
        self._fulfill()

        SparkPackage.objects.filter(slug="starter").update(is_active=True)
        retry = self._fulfill()
        self.assertEqual(retry["data"], "already_fulfilled")
        self.assertEqual(self._sparks(), 0)

    def test_validation_failures_are_also_queued_for_review(self):
        """A refused payment may still be a real one, so it stays visible."""
        make_spark_package(price="500.00")
        self._txn(PaystackTransaction.PackageType.SPARK, "starter")

        result = self._fulfill(minor=1)
        self.assertIn("amount_mismatch", result["data"])
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.FAILED)
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)
        self.assertEqual(self._sparks(), 0)

    def test_a_successful_capture_records_a_fulfillment_and_credits_once(self):
        make_spark_package(price="500.00", sparks=5000)
        self._txn(PaystackTransaction.PackageType.SPARK, "starter")

        result = self._fulfill()
        self.assertEqual(result["status"], "success")
        self.assertEqual(self._sparks(), 5000)
        txn = PaystackTransaction.objects.get()
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.SUCCESS)
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.FULFILLED)
        self.assertEqual(PaystackFulfillment.objects.get().sparks_granted, 5000)
