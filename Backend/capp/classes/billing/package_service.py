# capp/classes/billing/package_service.py
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

from capp.models import SparkPackage, SubscriptionPlan

logger = logging.getLogger(__name__)


TWO_PLACES = Decimal("0.01")


def _to_decimal(value, places: Decimal = TWO_PLACES) -> Decimal:
    """Coerce a payload value into a quantized Decimal (no float artefacts)."""
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value))
    return dec.quantize(places, rounding=ROUND_HALF_UP)


class PackageService:
    # --------------------------------- Helpers ---------------------------------
    @staticmethod
    def _compute_vat_inclusive_price(
        base_price_kes: Decimal,
        discount_percent: Decimal,
        vat_percent: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Compute the discounted subtotal and the VAT-inclusive final price.

        VAT is applied on top of the *discounted* price, so::

            subtotal = base_price_kes * (1 - discount_percent / 100)
            final    = subtotal * (1 + vat_percent / 100)

        Both results are rounded half-up to 2 decimal places. The returned
        ``final`` is what the customer is charged, so this is the single
        authoritative pricing path for spark packages.
        """
        subtotal = (base_price_kes * (Decimal("100") - discount_percent) / Decimal("100")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        final = (subtotal * (Decimal("100") + vat_percent) / Decimal("100")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        return subtotal, final

    @staticmethod
    def _sanitize_slug(raw_slug: str) -> str:
        """Convert an arbitrary string into a valid Django slug.

        - Lowercases the string
        - Replaces whitespace with hyphens
        - Strips any character that isn't alphanumeric, a hyphen, or an underscore
        - Strips leading/trailing hyphens
        """
        if not raw_slug:
            return ""
        slug = raw_slug.strip().lower()
        slug = re.sub(r"\s+", "_", slug)
        slug = re.sub(r"[^a-z0-9_-]", "", slug)
        slug = slug.strip("-")
        return slug

    # --------------------------------- Spark Packages ---------------------------------
    @classmethod
    def list_spark_packages(cls, active_only: bool = False) -> Dict[str, Any]:
        try:
            qs = SparkPackage.objects.all()
            if active_only:
                qs = qs.filter(is_active=True)
            qs = qs.order_by("base_spark_amount")
            data = [cls._serialize_spark_package(pkg) for pkg in qs]
            return {"status": "success", "data": {"packages": data, "count": len(data)}}
        except Exception:
            logger.exception("Error listing spark packages")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def create_spark_package(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            slug = cls._sanitize_slug(payload.get("slug", ""))
            if not slug:
                return {"status": "fail", "data": "slug_required"}
            name = payload.get("name", "").strip()
            if not name:
                return {"status": "fail", "data": "name_required"}
            base_spark_amount = int(payload.get("base_spark_amount", 0))
            if base_spark_amount <= 0:
                return {"status": "fail", "data": "invalid_spark_amount"}
            base_price_kes = _to_decimal(payload.get("base_price_kes", 0))
            discount_percent = _to_decimal(payload.get("discount_percent", 0))
            vat_percent = _to_decimal(payload.get("vat_percent", 0))

            if base_price_kes < 0:
                return {"status": "fail", "data": "invalid_base_price"}
            if not (Decimal("0") <= discount_percent <= Decimal("100")):
                return {"status": "fail", "data": "invalid_discount_percent"}
            if not (Decimal("0") <= vat_percent <= Decimal("100")):
                return {"status": "fail", "data": "invalid_vat_percent"}

            # final_price_kes is always derived server-side from
            # base/discount/VAT; a client-supplied value is ignored so the
            # charged amount can never drift from the pricing formula.
            _, final_price_kes = cls._compute_vat_inclusive_price(base_price_kes, discount_percent, vat_percent)

            if SparkPackage.objects.filter(slug=slug).exists():
                return {"status": "fail", "data": "slug_already_exists"}

            pkg = SparkPackage.objects.create(
                slug=slug,
                name=name,
                description=payload.get("description", ""),
                base_spark_amount=base_spark_amount,
                base_price_kes=base_price_kes,
                discount_percent=discount_percent,
                vat_percent=vat_percent,
                final_price_kes=final_price_kes,
                is_active=payload.get("is_active", True),
            )
            logger.info("Spark package created slug=%s id=%s", slug, pkg.spark_package_id)
            return {
                "status": "success",
                "data": {"package": cls._serialize_spark_package(pkg)},
            }
        except Exception:
            logger.exception("Error creating spark package")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def update_spark_package(cls, slug: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            pkg = SparkPackage.objects.get(slug=slug)
            if "slug" in payload:
                new_slug = cls._sanitize_slug(payload["slug"])
                if not new_slug:
                    return {"status": "fail", "data": "slug_required"}
                payload["slug"] = new_slug
            # update fields if provided
            for field in [
                "name",
                "description",
                "slug",
                "base_spark_amount",
                "base_price_kes",
                "discount_percent",
                "vat_percent",
            ]:
                if field in payload:
                    setattr(
                        pkg,
                        field,
                        _to_decimal(payload[field]) if "price" in field or "percent" in field else payload[field],
                    )
            if "is_active" in payload:
                pkg.is_active = bool(payload["is_active"])

            # Re-derive the final price server-side so a client-supplied
            # final_price_kes can never override the pricing formula.
            if any(f in payload for f in ("base_price_kes", "discount_percent", "vat_percent")):
                if not (Decimal("0") <= pkg.discount_percent <= Decimal("100")):
                    return {"status": "fail", "data": "invalid_discount_percent"}
                if not (Decimal("0") <= pkg.vat_percent <= Decimal("100")):
                    return {"status": "fail", "data": "invalid_vat_percent"}
                if pkg.base_price_kes < 0:
                    return {"status": "fail", "data": "invalid_base_price"}
                _, pkg.final_price_kes = cls._compute_vat_inclusive_price(
                    pkg.base_price_kes, pkg.discount_percent, pkg.vat_percent
                )

            pkg.save()
            logger.info("Spark package updated slug=%s", slug)
            return {
                "status": "success",
                "data": {"package": cls._serialize_spark_package(pkg)},
            }
        except SparkPackage.DoesNotExist:
            return {"status": "fail", "data": "package_not_found"}
        except Exception:
            logger.exception("Error updating spark package")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def delete_spark_package(cls, slug: str) -> Dict[str, Any]:
        try:
            pkg = SparkPackage.objects.get(slug=slug)
            pkg.delete()
            logger.info("Spark package deleted slug=%s", slug)
            return {"status": "success", "data": "package_deleted"}
        except SparkPackage.DoesNotExist:
            return {"status": "fail", "data": "package_not_found"}
        except Exception:
            logger.exception("Error deleting spark package")
            return {"status": "fail", "data": "server_error"}

    # --------------------------------- Subscription Plans ---------------------------------
    @classmethod
    def list_subscription_plans(cls, active_only: bool = False) -> Dict[str, Any]:
        try:
            qs = SubscriptionPlan.objects.all()
            if active_only:
                qs = qs.filter(is_active=True)
            qs = qs.order_by("price")
            data = [cls._serialize_subscription_plan(sp) for sp in qs]
            return {"status": "success", "data": {"plans": data, "count": len(data)}}
        except Exception:
            logger.exception("Error listing subscription plans")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def create_subscription_plan(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            slug = cls._sanitize_slug(payload.get("slug", ""))
            if not slug:
                return {"status": "fail", "data": "slug_required"}
            name = payload.get("name", "").strip()
            if not name:
                return {"status": "fail", "data": "name_required"}
            price = float(payload.get("price", 0))
            billing_cycle = payload.get("billing_cycle", "monthly")
            if billing_cycle not in dict(SubscriptionPlan.BillingCycle.choices):
                return {"status": "fail", "data": "invalid_billing_cycle"}

            if SubscriptionPlan.objects.filter(slug=slug).exists():
                return {"status": "fail", "data": "slug_already_exists"}

            plan = SubscriptionPlan.objects.create(
                slug=slug,
                name=name,
                description=payload.get("description", ""),
                price=price,
                currency=payload.get("currency", "KES"),
                billing_cycle=billing_cycle,
                daily_token_limit=payload.get("daily_token_limit", 0),
                total_token_limit=payload.get("total_token_limit", 0),
                included_sparks=payload.get("included_sparks", 0),
                max_tokens_per_request=payload.get("max_tokens_per_request", 0),
                is_active=payload.get("is_active", True),
                # new fields
                included_message_pairs=payload.get("included_message_pairs"),
                included_characters=payload.get("included_characters"),
                included_images=payload.get("included_images"),
                fair_use_message_cap=payload.get("fair_use_message_cap"),
                fair_use_character_cap=payload.get("fair_use_character_cap"),
                fair_use_image_cap=payload.get("fair_use_image_cap"),
            )
            logger.info("Subscription plan created slug=%s", slug)
            return {
                "status": "success",
                "data": {"plan": cls._serialize_subscription_plan(plan)},
            }
        except Exception:
            logger.exception("Error creating subscription plan")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def update_subscription_plan(cls, slug: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            plan = SubscriptionPlan.objects.get(slug=slug)
            if "slug" in payload:
                new_slug = cls._sanitize_slug(payload["slug"])
                if not new_slug:
                    return {"status": "fail", "data": "slug_required"}
                payload["slug"] = new_slug
            updatable_fields = [
                "name",
                "description",
                "slug",
                "price",
                "currency",
                "billing_cycle",
                "daily_token_limit",
                "total_token_limit",
                "included_sparks",
                "max_tokens_per_request",
                "is_active",
                "included_message_pairs",
                "included_characters",
                "included_images",
                "fair_use_message_cap",
                "fair_use_character_cap",
                "fair_use_image_cap",
            ]
            for field in updatable_fields:
                if field in payload:
                    setattr(plan, field, payload[field])
            plan.save()
            logger.info("Subscription plan updated slug=%s", slug)
            return {
                "status": "success",
                "data": {"plan": cls._serialize_subscription_plan(plan)},
            }
        except SubscriptionPlan.DoesNotExist:
            return {"status": "fail", "data": "plan_not_found"}
        except Exception:
            logger.exception("Error updating subscription plan")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def delete_subscription_plan(cls, slug: str) -> Dict[str, Any]:
        try:
            plan = SubscriptionPlan.objects.get(slug=slug)
            plan.delete()
            logger.info("Subscription plan deleted slug=%s", slug)
            return {"status": "success", "data": "plan_deleted"}
        except SubscriptionPlan.DoesNotExist:
            return {"status": "fail", "data": "plan_not_found"}
        except Exception:
            logger.exception("Error deleting subscription plan")
            return {"status": "fail", "data": "server_error"}

    # ------------------------- Serializers -------------------------
    @staticmethod
    def _serialize_spark_package(pkg):
        return {
            "slug": pkg.slug,
            "name": pkg.name,
            "description": pkg.description,
            "base_spark_amount": pkg.base_spark_amount,
            "base_price_kes": str(pkg.base_price_kes),
            "discount_percent": str(pkg.discount_percent),
            "vat_percent": str(pkg.vat_percent),
            "final_price_kes": str(pkg.final_price_kes),
            "is_active": pkg.is_active,
            "created_at": pkg.created_at.isoformat(),
            "updated_at": pkg.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_subscription_plan(plan):
        return {
            "slug": plan.slug,
            "name": plan.name,
            "description": plan.description,
            "price": str(plan.price),
            "currency": plan.currency,
            "billing_cycle": plan.billing_cycle,
            "daily_token_limit": plan.daily_token_limit,
            "total_token_limit": plan.total_token_limit,
            "included_sparks": plan.included_sparks,
            "max_tokens_per_request": plan.max_tokens_per_request,
            "is_active": plan.is_active,
            "included_message_pairs": plan.included_message_pairs,
            "included_characters": plan.included_characters,
            "included_images": plan.included_images,
            "fair_use_message_cap": plan.fair_use_message_cap,
            "fair_use_character_cap": plan.fair_use_character_cap,
            "fair_use_image_cap": plan.fair_use_image_cap,
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
        }
