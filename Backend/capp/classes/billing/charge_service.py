# capp/classes/billing/charge_service.py
"""
Paystack purchases and the balance pushes that follow them.

Spending Sparks no longer happens here. The ``validate_usage`` /
``charge_usage`` pair this module used to expose charged *after* the provider
call had already succeeded, so it could not express "this operation was paid
for and then failed" — a provider error left the user charged with no refund
path, and several call sites discarded the returned dict, making a failed
charge invisible. Every paid feature now goes through
:mod:`capp.classes.billing.paid_features`, which wraps
:class:`~capp.classes.billing.reservation_service.BillingReservationService`
(``reserve`` → provider call → ``settle``/``refund``).

What remains here is the other direction: turning money into Sparks.
"""

import hashlib
import logging
from decimal import Decimal
from typing import Dict, Any, Optional

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from capp.classes.billing.fx_service import convert_kes
from capp.classes.billing.balances import (
    credit_locked,
    lock_guest_quota,
    lock_spark_pool,
)
from capp.models import (
    CustomUser,
    UserSubscription,
    SubscriptionUsage,
    SubscriptionPlan,
    SparkPackage,
    SparkPool,
    SparkTransaction,
    GuestTokenQuota,
)

logger = logging.getLogger(__name__)

SUBSCRIPTION_UPGRADE_THRESHOLD = 500  # sparks


def _push_guest_spark_pool_balance(guest_session_id: str):
    """Send the current spark balance for a guest to its WebSocket group."""
    try:
        quota = GuestTokenQuota.objects.get(session_id=guest_session_id)
        balance = quota.sparks
    except GuestTokenQuota.DoesNotExist:
        balance = 0

    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"guest_spark_pool_{guest_session_id}",
            {
                "type": "spark_pool_balance",  # reuse same event type
                "sparks": balance,
            },
        )
        logger.info("Guest spark_pool balance pushed to group guest_spark_pool_%s", guest_session_id)


def _push_spark_pool_balance(user):
    """Send the current spark_pool balance to the user's WebSocket group."""
    spark_pool = SparkPool.objects.filter(user=user).first()
    balance = spark_pool.sparks if spark_pool else 0
    logger.info("Pushing spark_pool balance: user=%s balance=%s", user.user_id, balance)
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"spark_pool_{user.user_id}",
            {
                "type": "spark_pool_balance",
                "sparks": balance,
            },
        )
        logger.info("SparkPool balance pushed to group spark_pool_%s", user.user_id)


class ChargeService:
    """
    Spark‑based billing: validates usage, deducts sparks, enforces subscription caps,
    and processes successful Paystack payments.
    """

    @classmethod
    def initiate_payment_from_package(
        cls,
        user,  # CustomUser or None for guests
        package_slug: str,
        package_type: str,
        callback_path: Optional[str] = None,
        guest_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve a package slug to a server-side price and initialize with Paystack.

        Every gate that could make the purchase unfulfillable is checked *here*,
        before the customer is sent to a payment page, so the platform does not
        capture money it cannot honour. Price and currency come from the package
        row and are recorded as the purchase intent; the client supplies only the
        slug, the type, and a choice among allowlisted return routes.
        """
        from paystack_app.services import PaystackService

        # 1. Resolve the package and its authoritative price/currency
        if package_type == "spark":
            try:
                pkg = SparkPackage.objects.get(slug=package_slug, is_active=True)
            except SparkPackage.DoesNotExist:
                return {"status": "fail", "data": "package_not_found"}
            amount = Decimal(str(pkg.final_price_kes))
            # SparkPackage prices are KES by construction (``final_price_kes``)
            # and already include VAT (see PackageService pricing formula).
            currency = "KES"
            metadata = {
                "package_slug": package_slug,
                "package_type": "spark",
                "spark_amount": pkg.base_spark_amount,
                "vat_percent": str(pkg.vat_percent),
            }
        elif package_type == "subscription":
            if not user:
                return {"status": "fail", "data": "guests_cannot_subscribe"}
            try:
                pkg = SubscriptionPlan.objects.get(slug=package_slug, is_active=True)
            except SubscriptionPlan.DoesNotExist:
                return {"status": "fail", "data": "package_not_found"}
            amount = Decimal(str(pkg.price))
            currency = (pkg.currency or "").strip().upper()

            eligibility = cls._check_subscription_eligibility(user)
            if eligibility is not None:
                return {"status": "fail", "data": eligibility}

            metadata = {
                "package_slug": package_slug,
                "package_type": "subscription",
                "plan_name": pkg.name,
            }
        else:
            return {"status": "fail", "data": "invalid_package_type"}

        if amount <= 0:
            logger.error("Refusing to initiate payment for non-positive price slug=%s", package_slug)
            return {"status": "fail", "data": "package_price_invalid"}

        supported = [code.upper() for code in getattr(settings, "PAYSTACK_SUPPORTED_CURRENCIES", ["KES"])]
        if currency not in supported:
            # Charging a plan priced in an unsupported currency would mean
            # capturing the wrong amount, so refuse rather than substitute one.
            logger.error(
                "Refusing to initiate payment: package slug=%s is priced in unsupported currency %s",
                package_slug,
                currency,
            )
            return {"status": "fail", "data": "currency_not_supported"}

        # 2. Resolve email — guests get a deterministic placeholder
        if user:
            email = user.get_decrypted_email()
            if not email:
                return {"status": "fail", "data": "email_unavailable"}
        else:
            # Paystack requires a valid email — use a placeholder for guests.
            # The ownership ID is not a credential, but it still does not belong
            # in an address Paystack will echo back, so it is hashed.
            guest_tag = hashlib.sha256(guest_session_id.encode("utf-8")).hexdigest()[:12]
            email = f"guest_{guest_tag}@guest.uwenzai.com"

        # 3. Delegate to PaystackService
        return PaystackService.initialize_payment(
            user=user,
            amount=amount,
            email=email,
            package_slug=package_slug,
            package_type=package_type,
            currency=currency,
            metadata=metadata,
            callback_path=callback_path,
            guest_session_id=guest_session_id,
        )

    @classmethod
    def _check_subscription_eligibility(cls, user) -> Optional[str]:
        """
        Return a failure reason if this account may not start a new plan yet.

        This is the same threshold ``process_successful_payment`` applies after
        capture. Applying it beforehand is the point: previously a customer with
        a full plan could pay and then be refused the upgrade, leaving money
        taken with nothing delivered.
        """
        existing = UserSubscription.objects.filter(user=user, status=UserSubscription.StatusChoices.ACTIVE).first()
        if existing is None:
            return None
        if existing.sparks_remaining >= SUBSCRIPTION_UPGRADE_THRESHOLD:
            return "too_many_sparks_remaining"
        return None

    # ------------------------------------------------------------------
    # Post-payment: credit guest GuestTokenQuota (called from webhook)
    # ------------------------------------------------------------------
    @classmethod
    def process_guest_spark_payment(
        cls,
        guest_session_id: str,
        package_slug: str,
        paystack_reference: str,
    ) -> Dict[str, Any]:
        """
        Award sparks to a guest's GuestTokenQuota after a confirmed payment.
        """
        try:
            spark_pkg = SparkPackage.objects.get(slug=package_slug, is_active=True)
            with db_transaction.atomic():
                # The previous get_or_create/select_for_update pair could lose
                # the creation race and credit a row it had not locked.
                quota = lock_guest_quota(guest_session_id, initial_sparks=0)
                credit_locked(quota, spark_pkg.base_spark_amount)
            logger.info(
                "Awarded %d sparks to guest %s (ref %s)",
                spark_pkg.base_spark_amount,
                guest_session_id,
                paystack_reference,
            )
            return {"status": "success", "sparks_granted": int(spark_pkg.base_spark_amount)}
        except SparkPackage.DoesNotExist:
            logger.warning(
                "process_guest_spark_payment: SparkPackage not found slug=%s",
                package_slug,
            )
            return {"status": "fail", "data": "package_not_found"}
        except Exception:
            logger.exception("process_guest_spark_payment error")
            return {"status": "fail", "data": "server_error"}

    # ------------------------------------------------------------------
    # Public package listing (for the frontend)
    # ------------------------------------------------------------------
    @classmethod
    def get_available_packages(cls) -> Dict[str, Any]:
        """
        Return all active spark packages and subscription plans.
        No authentication required.
        """
        try:
            # Imported lazily (like the Paystack service above): the billing
            # apps and capp cross-import, so resolution happens at call time.
            from play_billing_app.services import PlayBillingService

            spark_packages = SparkPackage.objects.filter(is_active=True).order_by("base_spark_amount")
            subscription_plans = SubscriptionPlan.objects.filter(is_active=True).order_by("price")

            packages = []
            # Informational USD/EUR conversions for the purchase UI. Fetched
            # from a fixed allowlisted provider and cached 24h; when rates
            # are unavailable the fields are simply omitted — the charged
            # amount is always the KES price below.
            for cp in spark_packages:
                entry = {
                    "type": "spark",
                    "slug": cp.slug,
                    "name": cp.name,
                    "description": cp.description,
                    "base_spark_amount": cp.base_spark_amount,
                    "base_price_kes": str(cp.base_price_kes),
                    "discount_percent": str(cp.discount_percent),
                    "vat_percent": str(cp.vat_percent),
                    "final_price_kes": str(cp.final_price_kes),
                    "currency": "KES",
                    "tokens": 0,  # legacy, kept for frontend compatibility
                }
                # Google Play product id when this package is also purchasable
                # inside the Android TWA. Only the Digital-Goods-capable
                # Android client reads this field; the website flow ignores it.
                play_product_id = PlayBillingService.product_id_for_slug(cp.slug)
                if play_product_id:
                    entry["play_product_id"] = play_product_id
                try:
                    price_usd = convert_kes(cp.final_price_kes, "usd")
                    price_eur = convert_kes(cp.final_price_kes, "eur")
                except Exception:
                    logger.warning("FX conversion failed for %s", cp.slug, exc_info=True)
                    price_usd = price_eur = None
                if price_usd is not None:
                    entry["final_price_usd"] = str(price_usd)
                if price_eur is not None:
                    entry["final_price_eur"] = str(price_eur)
                packages.append(entry)
            for sp in subscription_plans:
                packages.append(
                    {
                        "type": "subscription",
                        "slug": sp.slug,
                        "name": sp.name,
                        "description": sp.description,
                        "price": str(sp.price),
                        "currency": sp.currency,
                        "billing_cycle": sp.billing_cycle,
                        "sparks": sp.included_sparks,
                        "message_pairs": sp.included_message_pairs,
                        "fair_use_message_cap": sp.fair_use_message_cap,
                        "characters": sp.included_characters,
                        "fair_use_character_cap": sp.fair_use_character_cap,
                        "images": sp.included_images,
                        "fair_use_image_cap": sp.fair_use_image_cap,
                        "tokens": 0,  # legacy
                    }
                )
            return {"status": "success", "data": {"packages": packages}}
        except Exception:
            logger.exception("Error listing packages")
            return {"status": "fail", "data": "server_error"}

    # ------------------------------------------------------------------
    # Post‑payment processing (called from webhook)
    # ------------------------------------------------------------------
    @classmethod
    def process_successful_payment(
        cls,
        user: CustomUser,
        package_slug: str,
        package_type: str,
        paystack_reference: str,
    ) -> Dict[str, Any]:
        """
        Award sparks or activate subscription after a confirmed payment.
        For subscriptions, any active subscription with less than 500 remaining
        sparks is automatically upgraded – remaining sparks carry over to the new plan.

        Callers must have already validated the payment against its recorded
        purchase intent; this method grants value and does not re-verify money.
        Returns ``{"status": "success", "sparks_granted": N}`` or a failure dict.
        """
        logger.info(
            "process_successful_payment: user=%s (%s) slug=%s type=%s ref=%s",
            user.user_id,
            user.username,
            package_slug,
            package_type,
            paystack_reference,
        )

        granted_sparks = 0

        try:
            if package_type == "spark":
                logger.info("Looking up spark package slug=%s", package_slug)
                spark_pkg = SparkPackage.objects.get(slug=package_slug, is_active=True)
                logger.info(
                    "Found spark package: %s, sparks=%d",
                    spark_pkg.name,
                    spark_pkg.base_spark_amount,
                )

                with db_transaction.atomic():
                    spark_pool = lock_spark_pool(user)
                    logger.info("SparkPool balance before award: %d", spark_pool.sparks)

                    balance_before = int(spark_pool.sparks)
                    credit_locked(spark_pool, spark_pkg.base_spark_amount)
                    logger.info("SparkPool balance after award: %d", spark_pool.sparks)

                    SparkTransaction.objects.create(
                        spark_pool=spark_pool,
                        user=user,
                        entry_type=SparkTransaction.EntryType.CREDIT,
                        source=SparkTransaction.SourceChoices.TOKEN_PURCHASE,
                        spark_amount=spark_pkg.base_spark_amount,
                        sparks_before=balance_before,
                        sparks_after=spark_pool.sparks,
                        description=f"Purchased {spark_pkg.name} – {spark_pkg.base_spark_amount} sparks",
                        reference=paystack_reference,
                    )
                    logger.info("SparkTransaction created for spark purchase")

                granted_sparks = int(spark_pkg.base_spark_amount)
                _push_spark_pool_balance(user)
                logger.info(
                    "Awarded %d sparks to user %s",
                    spark_pkg.base_spark_amount,
                    user.user_id,
                )

            elif package_type == "subscription":
                logger.info("Looking up subscription plan slug=%s", package_slug)
                plan = SubscriptionPlan.objects.get(slug=package_slug, is_active=True)
                logger.info(
                    "Found subscription plan: %s, sparks=%d",
                    plan.name,
                    plan.included_sparks,
                )

                with db_transaction.atomic():
                    # Check for existing active subscription and handle upgrade
                    existing = UserSubscription.objects.filter(
                        user=user, status=UserSubscription.StatusChoices.ACTIVE
                    ).first()

                    extra_sparks = 0
                    if existing:
                        logger.info(
                            "Existing subscription found: %s (remaining=%d)",
                            existing.subscription_plan.name,
                            existing.sparks_remaining,
                        )
                        # Only allow upgrade if remaining sparks are below threshold
                        if existing.sparks_remaining >= SUBSCRIPTION_UPGRADE_THRESHOLD:
                            logger.warning(
                                "Upgrade blocked – user has %d remaining sparks (threshold=%d)",
                                existing.sparks_remaining,
                                SUBSCRIPTION_UPGRADE_THRESHOLD,
                            )
                            return {
                                "status": "fail",
                                "data": "too_many_sparks_remaining",
                            }
                        extra_sparks = existing.sparks_remaining
                        existing.status = UserSubscription.StatusChoices.EXPIRED
                        existing.sparks_remaining = 0
                        existing.save(update_fields=["status", "sparks_remaining", "updated_at"])
                        logger.info(
                            "Previous subscription expired, carrying over %d sparks",
                            extra_sparks,
                        )

                    now = timezone.now()
                    end_date = cls._calculate_end_date(plan.billing_cycle, now)
                    logger.info("Creating subscription: start=%s end=%s", now, end_date)

                    total_sparks = plan.included_sparks + extra_sparks
                    sub = UserSubscription.objects.create(
                        user=user,
                        subscription_plan=plan,
                        status=UserSubscription.StatusChoices.ACTIVE,
                        started_at=now,
                        ends_at=end_date,
                        daily_tokens_used=0,
                        tokens_used_in_cycle=0,
                        sparks_granted=total_sparks,
                        sparks_remaining=total_sparks,
                    )
                    logger.info("Subscription created: %s", sub.user_subscription_id)

                    # Award the total sparks
                    spark_pool = lock_spark_pool(user)
                    logger.info("SparkPool balance before award: %d", spark_pool.sparks)

                    balance_before = int(spark_pool.sparks)
                    credit_locked(spark_pool, total_sparks)
                    logger.info("SparkPool balance after award: %d", spark_pool.sparks)

                    SparkTransaction.objects.create(
                        spark_pool=spark_pool,
                        user=user,
                        entry_type=SparkTransaction.EntryType.CREDIT,
                        source=SparkTransaction.SourceChoices.SUBSCRIPTION,
                        spark_amount=total_sparks,
                        sparks_before=balance_before,
                        sparks_after=spark_pool.sparks,
                        description=f"Subscription {plan.name} – {total_sparks} sparks"
                        + (f" (includes {extra_sparks} from previous plan)" if extra_sparks else ""),
                        reference=paystack_reference,
                    )
                    logger.info("SparkTransaction created for subscription")

                    SubscriptionUsage.objects.create(
                        user_subscription=sub,
                        cycle_start=now,
                        cycle_end=end_date,
                    )
                    logger.info("SubscriptionUsage record created")

                granted_sparks = int(total_sparks)
                _push_spark_pool_balance(user)
                logger.info(
                    "Activated subscription %s for user %s (ends %s)",
                    plan.name,
                    user.user_id,
                    end_date,
                )

            else:
                logger.warning("Invalid package_type: %s", package_type)
                return {"status": "fail", "data": "invalid_package_type"}

            return {"status": "success", "sparks_granted": granted_sparks}

        except SparkPackage.DoesNotExist:
            logger.warning("SparkPackage not found: slug=%s", package_slug)
            return {"status": "fail", "data": "spark_package_not_found"}
        except SubscriptionPlan.DoesNotExist:
            logger.warning("SubscriptionPlan not found: slug=%s", package_slug)
            return {"status": "fail", "data": "subscription_plan_not_found"}
        except Exception:
            logger.exception("Error processing payment for user %s", user.user_id)
            return {"status": "fail", "data": "server_error"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_spark_pool(user: CustomUser) -> SparkPool:
        spark_pool, _ = SparkPool.objects.get_or_create(
            user=user,
            defaults={"sparks": 0, "token_balance": 0, "is_active": True},
        )
        return spark_pool

    @staticmethod
    def _get_active_subscription(user: CustomUser) -> Optional[UserSubscription]:
        now = timezone.now()
        return (
            UserSubscription.objects.filter(
                user=user,
                status=UserSubscription.StatusChoices.ACTIVE,
                started_at__lte=now,
                ends_at__gt=now,
            )
            .select_related("subscription_plan")
            .first()
        )

    @staticmethod
    def _get_or_create_usage_record(
        user_subscription: UserSubscription,
    ) -> SubscriptionUsage:
        now = timezone.now()
        usage = SubscriptionUsage.objects.filter(
            user_subscription=user_subscription,
            cycle_start__lte=now,
            cycle_end__gt=now,
        ).first()
        if usage is None:
            # Create a new cycle record
            usage = SubscriptionUsage.objects.create(
                user_subscription=user_subscription,
                cycle_start=now,
                cycle_end=user_subscription.ends_at,
            )
        return usage

    @staticmethod
    def _calculate_end_date(billing_cycle: str, start_date):
        from datetime import timedelta

        if billing_cycle == SubscriptionPlan.BillingCycle.DAILY:
            return start_date + timedelta(days=1)
        elif billing_cycle == SubscriptionPlan.BillingCycle.WEEKLY:
            return start_date + timedelta(weeks=1)
        elif billing_cycle == SubscriptionPlan.BillingCycle.MONTHLY:
            # Approximate month as 30 days
            return start_date + timedelta(days=30)
        elif billing_cycle == SubscriptionPlan.BillingCycle.YEARLY:
            return start_date + timedelta(days=365)
        else:
            raise ValueError(f"Unknown billing cycle: {billing_cycle}")
