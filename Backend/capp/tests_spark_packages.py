# capp/tests_spark_packages.py
"""
VAT handling for spark packages.

The pricing invariant under test: ``final_price_kes`` is always derived
server-side from ``base_price_kes``, ``discount_percent`` and ``vat_percent``,
with VAT applied on top of the *discounted* price. A client-supplied
``final_price_kes`` can never override the formula, because that field is what
the payment gateway charges.
"""

from decimal import Decimal

from django.test import TestCase

from capp.classes.billing.package_service import PackageService
from capp.models import SparkPackage


def _payload(**overrides):
    base = {
        "slug": "starter",
        "name": "Starter",
        "description": "300 sparks",
        "base_spark_amount": 300,
        "base_price_kes": 30,
        "discount_percent": 30,
        "vat_percent": 16,
        "is_active": True,
    }
    base.update(overrides)
    return base


class SparkPackageVATTests(TestCase):
    def test_create_computes_vat_inclusive_price(self):
        """30 KES base, 30% discount, 16% VAT → 21.00 subtotal → 24.36 final."""
        result = PackageService.create_spark_package(_payload())
        self.assertEqual(result["status"], "success")

        pkg = SparkPackage.objects.get(slug="starter")
        self.assertEqual(pkg.final_price_kes, Decimal("24.36"))
        self.assertEqual(pkg.vat_percent, Decimal("16.00"))
        serialized = result["data"]["package"]
        self.assertEqual(serialized["final_price_kes"], "24.36")
        self.assertEqual(serialized["vat_percent"], "16.00")

    def test_create_ignores_client_supplied_final_price(self):
        """The charged amount must come from the formula, not the payload."""
        result = PackageService.create_spark_package(_payload(final_price_kes=1))  # hostile / stale client value
        self.assertEqual(result["status"], "success")
        pkg = SparkPackage.objects.get(slug="starter")
        self.assertEqual(pkg.final_price_kes, Decimal("24.36"))

    def test_zero_vat_keeps_discounted_price(self):
        """vat_percent=0 must preserve the pre-VAT behaviour exactly."""
        result = PackageService.create_spark_package(_payload(vat_percent=0))
        self.assertEqual(result["status"], "success")
        pkg = SparkPackage.objects.get(slug="starter")
        self.assertEqual(pkg.final_price_kes, Decimal("21.00"))

    def test_update_recomputes_price_from_new_inputs(self):
        PackageService.create_spark_package(_payload())
        result = PackageService.update_spark_package("starter", {"vat_percent": 8})
        self.assertEqual(result["status"], "success")
        pkg = SparkPackage.objects.get(slug="starter")
        # 30 − 30% = 21.00, + 8% VAT = 22.68
        self.assertEqual(pkg.final_price_kes, Decimal("22.68"))
        self.assertEqual(pkg.vat_percent, Decimal("8.00"))

    def test_update_rejects_out_of_range_vat(self):
        PackageService.create_spark_package(_payload())
        result = PackageService.update_spark_package("starter", {"vat_percent": 150})
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "invalid_vat_percent")
        pkg = SparkPackage.objects.get(slug="starter")
        self.assertEqual(pkg.final_price_kes, Decimal("24.36"))

    def test_create_rejects_out_of_range_vat(self):
        result = PackageService.create_spark_package(_payload(vat_percent=-1))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "invalid_vat_percent")
        self.assertFalse(SparkPackage.objects.filter(slug="starter").exists())

    def test_rounding_is_half_up_to_two_places(self):
        """base 33.33, 10% discount → 29.997 → 30.00; +16% VAT → 34.80."""
        result = PackageService.create_spark_package(_payload(base_price_kes="33.33", discount_percent=10))
        self.assertEqual(result["status"], "success")
        pkg = SparkPackage.objects.get(slug="starter")
        self.assertEqual(pkg.final_price_kes, Decimal("34.80"))
