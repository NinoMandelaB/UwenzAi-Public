# capp/tests_promo_codes.py
"""
Single-use signup promo codes (flyer campaign).

Covers the properties the feature exists for:

* a valid code credits extra Sparks on top of the signup bonus, atomically,
  with its own ledger row;
* a bad, used, paused or revoked code never fails the signup — the outcome is
  reported back instead;
* one code per flyer and one promo per account, enforced by the database;
* the admin management surface (generate / pause / revoke / delete) with its
  audit log;
* the redemption race, against a real PostgreSQL server with real separate
  connections (``TransactionTestCase`` + barrier-released threads, the same
  pattern as the guest-transfer suite).
"""

import threading
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from capp.classes.auth.auth import Auth
from capp.classes.billing import promo_code_service

from capp.constants import SIGNUP_SPARKS
from capp.models import AdminActionLog, CustomUser, PromoCode, SparkPool, SparkTransaction
from capp.tests import _make_user

RECAPTCHA_OK = {"success": True}
SIGNUP_URL = "/api/v1/auth/signup/"
PROMO_LIST_URL = "/api/v1/admin/promo-codes/"
PROMO_CREATE_URL = "/api/v1/admin/promo-codes/create/"


def _signup_payload(username, promo_code=None):
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "pw123456",
        "confirm_password": "pw123456",
        "full_name": "Promo Tester",
        "age_confirmed": True,
        "terms_accepted": True,
        "privacy_accepted": True,
    }
    if promo_code is not None:
        payload["promo_code"] = promo_code
    return payload


def _make_code(code="DEMOCODE1", sparks=500, **kwargs):
    return PromoCode.objects.create(code=code, sparks_amount=sparks, **kwargs)


class PromoSignupTests(TestCase):
    def setUp(self):
        # Throttles use the shared process cache; reset so tests are independent.
        cache.clear()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_valid_code_credits_sparks_on_top_of_signup_bonus(self, _recaptcha):
        _make_code(code="DEMOCODE1", sparks=500)

        resp = self.client.post(
            SIGNUP_URL, data=_signup_payload("promo1", "DEMOCODE1"), content_type="application/json"
        )
        self.assertIn(resp.status_code, (200, 201))
        body = resp.json()
        self.assertEqual(body["status"], "success")

        # Promo outcome is surfaced to the caller, never silent.
        self.assertEqual(body["data"]["promo"], {"status": "applied", "sparks": 500})
        # The reported balance includes both grants.
        self.assertEqual(body["data"]["spark_pool"]["sparks"], SIGNUP_SPARKS + 500)

        user = CustomUser.objects.get(username="promo1")
        pool = SparkPool.objects.get(user=user)
        self.assertEqual(pool.sparks, SIGNUP_SPARKS + 500)

        # Two ledger entries: the signup bonus and the promo bonus, the latter
        # referencing the code so it shows up in the user's transactions.
        bonus_rows = SparkTransaction.objects.filter(user=user, source=SparkTransaction.SourceChoices.BONUS)
        self.assertEqual(bonus_rows.count(), 2)
        promo_row = bonus_rows.get(reference="DEMOCODE1")
        self.assertEqual(promo_row.spark_amount, 500)
        self.assertEqual(promo_row.sparks_before, SIGNUP_SPARKS)
        self.assertEqual(promo_row.sparks_after, SIGNUP_SPARKS + 500)

        promo = PromoCode.objects.get(code="DEMOCODE1")
        self.assertEqual(promo.redeemed_by, user)
        self.assertIsNotNone(promo.redeemed_at)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signup_without_code_keeps_existing_response_shape(self, _recaptcha):
        resp = self.client.post(SIGNUP_URL, data=_signup_payload("plain1"), content_type="application/json")
        self.assertIn(resp.status_code, (200, 201))
        self.assertNotIn("promo", resp.json()["data"])
        self.assertEqual(SparkPool.objects.get(user__username="plain1").sparks, SIGNUP_SPARKS)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_unknown_code_does_not_block_signup(self, _recaptcha):
        resp = self.client.post(
            SIGNUP_URL, data=_signup_payload("ghost1", "NOSUCHCODE"), content_type="application/json"
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(resp.json()["data"]["promo"], {"status": "not_applied", "reason": "not_found"})
        self.assertEqual(SparkPool.objects.get(user__username="ghost1").sparks, SIGNUP_SPARKS)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_already_redeemed_code_is_rejected_but_signup_succeeds(self, _recaptcha):
        other = _make_user(username="first", email="first@example.com")
        promo = _make_code()
        promo.redeemed_by = other
        promo.redeemed_at = promo.created_at
        promo.save(update_fields=["redeemed_by", "redeemed_at", "updated_at"])

        resp = self.client.post(
            SIGNUP_URL, data=_signup_payload("second2", "DEMOCODE1"), content_type="application/json"
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(resp.json()["data"]["promo"], {"status": "not_applied", "reason": "already_redeemed"})
        self.assertEqual(SparkPool.objects.get(user__username="second2").sparks, SIGNUP_SPARKS)
        self.assertEqual(PromoCode.objects.get(pk=promo.pk).redeemed_by_id, other.pk)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_deactivated_and_revoked_codes_are_rejected(self, _recaptcha):
        _make_code(code="PAUSED1", sparks=100, is_active=False)
        _make_code(code="GONE11", sparks=100, is_revoked=True, is_active=False)

        for username, code in (("paused1", "PAUSED1"), ("revoked1", "GONE11")):
            resp = self.client.post(SIGNUP_URL, data=_signup_payload(username, code), content_type="application/json")
            self.assertIn(resp.status_code, (200, 201))
            self.assertEqual(resp.json()["data"]["promo"], {"status": "not_applied", "reason": "not_available"})

        for code in ("PAUSED1", "GONE11"):
            promo = PromoCode.objects.get(code=code)
            self.assertIsNone(promo.redeemed_at)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_code_input_is_normalized(self, _recaptcha):
        _make_code(code="DEMOCODE1", sparks=250)

        resp = self.client.post(
            SIGNUP_URL, data=_signup_payload("norm1", " democode-1 "), content_type="application/json"
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(resp.json()["data"]["promo"], {"status": "applied", "sparks": 250})
        self.assertEqual(SparkPool.objects.get(user__username="norm1").sparks, SIGNUP_SPARKS + 250)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_failed_signup_does_not_consume_the_code(self, _recaptcha):
        _make_code()

        # A failure inside the atomic block (verification row cannot be
        # created) must roll back the whole signup, including the redemption.
        with mock.patch(
            "capp.classes.auth.auth.EmailVerificationService.create_verification_for_user",
            side_effect=RuntimeError("verification unavailable"),
        ):
            resp = self.client.post(
                SIGNUP_URL, data=_signup_payload("doomed1", "DEMOCODE1"), content_type="application/json"
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "server_error")

        self.assertFalse(CustomUser.objects.filter(username="doomed1").exists())
        promo = PromoCode.objects.get(code="DEMOCODE1")
        self.assertIsNone(promo.redeemed_at)
        self.assertIsNone(promo.redeemed_by_id)
        self.assertFalse(SparkTransaction.objects.filter(reference="DEMOCODE1").exists())

    def test_one_promo_per_account_is_enforced_by_the_database(self):
        user = _make_user(username="greedy", email="greedy@example.com")
        _make_code(code="FIRST11", sparks=100, redeemed_by=user, redeemed_at=user.created_at)
        with self.assertRaises(IntegrityError):
            _make_code(code="SECON11", sparks=100, redeemed_by=user, redeemed_at=user.created_at)

    def test_account_erasure_keeps_the_code_consumed(self):
        user = _make_user(username="eraseme", email="eraseme@example.com")
        promo = _make_code(redeemed_by=user, redeemed_at=user.created_at)

        # Account erasure nulls the link (SET_NULL) but the timestamp keeps
        # the code consumed — a recycled flyer can never be redeemed twice.
        user.delete()

        promo.refresh_from_db()
        self.assertIsNone(promo.redeemed_by_id)
        self.assertIsNotNone(promo.redeemed_at)


class PromoAdminApiTests(TestCase):
    def setUp(self):
        self.admin = _make_user(username="admin1", email="admin1@example.com", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_non_admin_is_forbidden(self):
        outsider = _make_user(username="outsider", email="outsider@example.com")
        client = APIClient()
        client.force_authenticate(user=outsider)
        resp = client.get(PROMO_LIST_URL)
        self.assertEqual(resp.status_code, 403)

    def test_list_includes_redemption_details(self):
        user = _make_user(username="flyeruser", email="flyer@example.com")
        _make_code(code="USEDME1", redeemed_by=user, redeemed_at=user.created_at)
        _make_code(code="FRESH11")
        _make_code(code="PAUSED1", is_active=False)
        _make_code(code="REVOKED1", is_revoked=True, is_active=False)

        resp = self.client.get(PROMO_LIST_URL)
        self.assertEqual(resp.status_code, 200)
        codes = {row["code"]: row for row in resp.json()["data"]["promo_codes"]}
        self.assertEqual(codes["USEDME1"]["redeemed_by"]["username"], "flyeruser")
        self.assertIsNotNone(codes["USEDME1"]["redeemed_at"])
        self.assertIsNone(codes["FRESH11"]["redeemed_by"])

        for status_filter, expected in (
            ("available", {"FRESH11"}),
            ("redeemed", {"USEDME1"}),
            ("inactive", {"PAUSED1"}),
            ("revoked", {"REVOKED1"}),
        ):
            resp = self.client.get(PROMO_LIST_URL, {"status": status_filter})
            self.assertEqual({row["code"] for row in resp.json()["data"]["promo_codes"]}, expected, status_filter)

    def test_create_generates_unique_codes_and_logs_the_action(self):
        resp = self.client.post(
            PROMO_CREATE_URL,
            data={"count": 5, "sparks_amount": 750, "note": "Nairobi campus flyers"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()["data"]
        self.assertEqual(body["created"], 5)
        values = [row["code"] for row in body["promo_codes"]]
        self.assertEqual(len(set(values)), 5)
        self.assertTrue(all(PromoCode.objects.filter(code=v).exists() for v in values))
        self.assertEqual(
            AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.PROMO_CODE_CREATE).count(), 1
        )

    def test_create_rejects_invalid_parameters(self):
        for payload in (
            {"count": 0, "sparks_amount": 100},
            {"count": 501, "sparks_amount": 100},
            {"count": 3, "sparks_amount": 0},
            {"count": 3, "sparks_amount": 2_000_000},
            {"count": "many", "sparks_amount": 100},
        ):
            resp = self.client.post(PROMO_CREATE_URL, data=payload, content_type="application/json")
            self.assertEqual(resp.status_code, 400, payload)

    def test_pause_and_resume_are_reversible(self):
        promo = _make_code()
        url = f"/api/v1/admin/promo-codes/{promo.promo_code_id}/active/"

        resp = self.client.post(url, data={"is_active": False}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PromoCode.objects.get(pk=promo.pk).is_active)

        resp = self.client.post(url, data={"is_active": True}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(PromoCode.objects.get(pk=promo.pk).is_active)

    def test_revoked_and_redeemed_codes_cannot_be_toggled(self):
        revoked = _make_code(code="REVOKED1", is_revoked=True, is_active=False)
        user = _make_user(username="usedone", email="usedone@example.com")
        redeemed = _make_code(code="USEDME1", redeemed_by=user, redeemed_at=user.created_at)

        for promo in (revoked, redeemed):
            url = f"/api/v1/admin/promo-codes/{promo.promo_code_id}/active/"
            resp = self.client.post(url, data={"is_active": True}, content_type="application/json")
            self.assertEqual(resp.status_code, 409, promo.code)

    def test_revoke_is_permanent(self):
        promo = _make_code()
        url = f"/api/v1/admin/promo-codes/{promo.promo_code_id}/revoke/"
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        refreshed = PromoCode.objects.get(pk=promo.pk)
        self.assertTrue(refreshed.is_revoked)

        # Reactivation of a revoked code is refused.
        active_url = f"/api/v1/admin/promo-codes/{promo.promo_code_id}/active/"
        resp = self.client.post(active_url, data={"is_active": True}, content_type="application/json")
        self.assertEqual(resp.status_code, 409)

    def test_redeemed_codes_cannot_be_revoked_or_deleted(self):
        user = _make_user(username="keeper", email="keeper@example.com")
        promo = _make_code(code="KEEPER1", redeemed_by=user, redeemed_at=user.created_at)

        resp = self.client.post(f"/api/v1/admin/promo-codes/{promo.promo_code_id}/revoke/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["data"], "already_redeemed")

        resp = self.client.delete(f"/api/v1/admin/promo-codes/{promo.promo_code_id}/")
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(PromoCode.objects.filter(pk=promo.pk).exists())

    def test_delete_removes_unredeemed_codes(self):
        promo = _make_code()
        resp = self.client.delete(f"/api/v1/admin/promo-codes/{promo.promo_code_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PromoCode.objects.filter(pk=promo.pk).exists())
        self.assertTrue(AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.PROMO_CODE_DELETE).exists())

    def test_users_list_filters_by_promo_usage(self):
        redeemed_user = _make_user(username="flyerfan", email="flyerfan@example.com")
        _make_code(code="FAN111", redeemed_by=redeemed_user, redeemed_at=redeemed_user.created_at)
        _make_user(username="noflyer", email="noflyer@example.com")

        resp = self.client.get("/api/v1/admin/users/", {"promo_used": "true"})
        usernames = [u["username"] for u in resp.json()["data"]["users"]]
        self.assertIn("flyerfan", usernames)
        self.assertNotIn("noflyer", usernames)

        resp = self.client.get("/api/v1/admin/users/", {"promo_used": "false"})
        usernames = [u["username"] for u in resp.json()["data"]["users"]]
        self.assertIn("noflyer", usernames)
        self.assertNotIn("flyerfan", usernames)

    def test_user_summary_carries_the_redeemed_code(self):
        user = _make_user(username="coded", email="coded@example.com")
        _make_code(code="CODED11", redeemed_by=user, redeemed_at=user.created_at)

        resp = self.client.get(f"/api/v1/admin/users/{user.user_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["promo_code"]["code"], "CODED11")


def _run_in_threads(target, count):
    """Barrier-released threads, the guest-transfer concurrency pattern.

    Every thread closes its database connection in a ``finally``; a leaked
    connection holding the promo row lock would deadlock the suite.
    """
    barrier = threading.Barrier(count)
    results = [None] * count

    def wrapped(index):
        try:
            barrier.wait(timeout=30)
            results[index] = ("ok", target(index))
        except Exception as exc:  # noqa: BLE001 - recorded and asserted on
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


class PromoRedemptionConcurrencyTests(TransactionTestCase):
    """
    The redemption race, against a real PostgreSQL server with separate
    connections: however many signups present the same flyer code, exactly one
    account can redeem it.
    """

    def _signup(self, username, code):
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ), mock.patch(
            "capp.classes.email.verification_email_service.VerificationEmailService.send_verification_code",
            return_value=True,
        ):
            return Auth.signup_user(_signup_payload(username, code))

    def test_concurrent_signups_redeem_one_code_exactly_once(self):
        _make_code(code="RACE1111", sparks=400)

        results = _run_in_threads(lambda i: self._signup(f"racer{i}", "RACE1111"), 4)

        self.assertEqual([status for status, _ in results], ["ok"] * 4)
        outcomes = [payload["data"].get("promo") for _, payload in results]
        applied = [o for o in outcomes if o and o["status"] == "applied"]
        rejected = [o for o in outcomes if o and o["status"] == "not_applied"]
        self.assertEqual(len(applied), 1, outcomes)
        self.assertEqual(len(rejected), 3, outcomes)
        self.assertEqual(applied[0]["sparks"], 400)
        self.assertTrue(all(o["reason"] == "already_redeemed" for o in rejected))

        # One redemption in the ledger, one credited pool, one consumed code.
        promo = PromoCode.objects.get(code="RACE1111")
        self.assertIsNotNone(promo.redeemed_at)
        self.assertEqual(SparkTransaction.objects.filter(reference="RACE1111").count(), 1)

        # Every racer exists with the right balance: the winner got the promo.
        balances = {
            u.username: SparkPool.objects.get(user=u).sparks
            for u in CustomUser.objects.filter(username__startswith="racer")
        }
        self.assertEqual(len(balances), 4)
        self.assertEqual(sum(1 for b in balances.values() if b == SIGNUP_SPARKS + 400), 1)
        self.assertEqual(sum(1 for b in balances.values() if b == SIGNUP_SPARKS), 3)


class PromoHardeningTests(TestCase):
    """Edge cases pinned after review: malformed input, collision recovery,
    audit durability after delete, and the deleted-account placeholder."""

    def setUp(self):
        cache.clear()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_string_promo_code_does_not_break_signup(self, _recaptcha):
        # A number/array/object in the optional field is rejected as a promo
        # outcome, never as a server error — the signup itself must succeed.
        for bad_value in (12345, ["DEMOCODE1"], {"code": "DEMOCODE1"}, True):
            payload = _signup_payload(f"badtype{abs(hash(str(bad_value))) % 99999}")
            payload["promo_code"] = bad_value
            resp = self.client.post(SIGNUP_URL, data=payload, content_type="application/json")
            self.assertIn(resp.status_code, (200, 201), bad_value)
            body = resp.json()
            self.assertEqual(body["status"], "success", bad_value)
            self.assertNotIn("promo", body["data"], bad_value)

    def test_generation_survives_a_code_collision(self):
        # The first draw collides with an existing code; the retry inside the
        # same generation call must still succeed via the savepoint.
        _make_code(code="TAKEN001")
        with mock.patch(
            "capp.classes.billing.promo_code_service._generate_code_value",
            side_effect=["TAKEN001", "FRESH001"],
        ):
            codes = promo_code_service.generate_codes(count=1, sparks_amount=100)

        self.assertEqual(len(codes), 1)
        self.assertEqual(codes[0].code, "FRESH001")
        self.assertEqual(PromoCode.objects.count(), 2)

    def test_delete_audit_log_keeps_the_promo_code_id(self):
        # Django nulls the instance pk after delete(); the audit entry must
        # still carry the original UUID.
        promo = _make_code()
        admin = _make_user(username="deleter", email="deleter@example.com", is_staff=True)

        promo_code_service.delete(promo.promo_code_id, admin_user=admin)

        entry = AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.PROMO_CODE_DELETE).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata["promo_code_id"], str(promo.promo_code_id))
        self.assertEqual(entry.metadata["code"], "DEMOCODE1")

    def test_erased_redeemer_shows_as_deleted_account_placeholder(self):
        # Account erasure nulls redeemed_by while redeemed_at stays set; the
        # admin list must show the deleted-account placeholder, not an empty
        # cell, so the campaign history stays readable.
        user = _make_user(username="vanished", email="vanished@example.com")
        promo = _make_code(redeemed_by=user, redeemed_at=user.created_at)

        user.delete()

        client = APIClient()
        admin = _make_user(username="viewer", email="viewer@example.com", is_staff=True)
        client.force_authenticate(user=admin)
        resp = client.get(PROMO_LIST_URL)
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.json()["data"]["promo_codes"] if r["code"] == "DEMOCODE1")
        self.assertIsNotNone(row["redeemed_at"])
        self.assertIsNotNone(row["redeemed_by"])
        self.assertTrue(row["redeemed_by"]["account_deleted"])
        self.assertEqual(row["redeemed_by"]["username"], "(deleted account)")
        self.assertEqual(row["promo_code_id"], str(promo.promo_code_id))
