# capp/tests_fx_rates.py
"""
KES → USD/EUR display conversions on the public package listing.

Invariants under test:

* converted prices appear on the listing only when a usable rate exists;
* the charged currency stays KES — USD/EUR are informational fields;
* the provider is hit at most once per cache window (24h);
* a provider outage degrades to "no conversion shown", never an error page.
"""

from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from capp.classes.billing import fx_service
from capp.classes.billing.charge_service import ChargeService
from capp.models import SparkPackage


def _package(**overrides):
    defaults = {
        "slug": "starter",
        "name": "Starter",
        "base_spark_amount": 300,
        "base_price_kes": Decimal("30.00"),
        "discount_percent": Decimal("30.00"),
        "vat_percent": Decimal("16.00"),
        "final_price_kes": Decimal("24.36"),
        "is_active": True,
    }
    defaults.update(overrides)
    return SparkPackage.objects.create(**defaults)


def _provider_payload():
    # 1 KES ≈ 0.0077 USD / 0.0065 EUR (illustrative)
    return {
        "result": "success",
        "time_last_update_unix": 1789689751,
        "rates": {"USD": 0.0077, "EUR": 0.0065},
    }


class _FakeResponse:
    encoding = None

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        import json

        yield json.dumps(self._payload).encode()

    def close(self):
        pass


class FxRateTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_listing_includes_converted_prices_when_rates_available(self):
        _package()
        with mock.patch.object(fx_service.requests, "get", return_value=_FakeResponse(_provider_payload())):
            result = ChargeService.get_available_packages()

        self.assertEqual(result["status"], "success")
        entry = result["data"]["packages"][0]
        self.assertEqual(entry["final_price_kes"], "24.36")
        # 24.36 × 0.0077 = 0.1876 → 0.19 ; × 0.0065 = 0.1583 → 0.16
        self.assertEqual(entry["final_price_usd"], "0.19")
        self.assertEqual(entry["final_price_eur"], "0.16")
        self.assertEqual(entry["currency"], "KES")

    def test_provider_fetched_once_then_cached(self):
        _package()
        fetch = mock.Mock(return_value=_FakeResponse(_provider_payload()))
        with mock.patch.object(fx_service.requests, "get", fetch):
            for _ in range(3):
                result = ChargeService.get_available_packages()
                self.assertEqual(result["status"], "success")
        self.assertEqual(fetch.call_count, 1)

    def test_outage_hides_conversion_without_failing_listing(self):
        _package()
        with mock.patch.object(fx_service.requests, "get", side_effect=fx_service.requests.Timeout("down")):
            result = ChargeService.get_available_packages()

        self.assertEqual(result["status"], "success")
        entry = result["data"]["packages"][0]
        self.assertEqual(entry["final_price_kes"], "24.36")
        self.assertNotIn("final_price_usd", entry)
        self.assertNotIn("final_price_eur", entry)

    def test_garbage_payload_is_rejected(self):
        _package()
        with mock.patch.object(fx_service.requests, "get", return_value=_FakeResponse({"nope": 1})):
            result = ChargeService.get_available_packages()

        self.assertEqual(result["status"], "success")
        self.assertNotIn("final_price_usd", result["data"]["packages"][0])

    def test_negative_rate_is_rejected(self):
        _package()
        payload = _provider_payload()
        payload["rates"]["USD"] = -0.0077
        with mock.patch.object(fx_service.requests, "get", return_value=_FakeResponse(payload)):
            self.assertIsNone(fx_service.get_kes_fx_rates())

    def test_convert_kes_rounds_half_up(self):
        with mock.patch.object(
            fx_service.cache,
            "get",
            return_value={
                "rates": {"usd": Decimal("0.0077"), "eur": Decimal("0.0065")},
                "updated_at": None,
            },
        ):
            self.assertEqual(fx_service.convert_kes(Decimal("24.36"), "usd"), Decimal("0.19"))
            self.assertEqual(fx_service.convert_kes(Decimal("100.00"), "eur"), Decimal("0.65"))
