# capp/tests_profile.py
"""
Coverage for the update_timezone tool (capp.classes.profile.tool_loop):

- Happy path: a valid IANA timezone name persists to CustomUser.timezone.
- No-op: requesting the timezone the user already has is reported as such,
  not as a fresh change.
- Rejection: a bogus/non-IANA string is rejected without touching the DB,
  with a relayable failure message the model can act on (e.g. ask the user
  which city/region they mean).
- build_profile_tool_specs() shape sanity (name, required params).
"""

import json

from django.test import TestCase

from capp.classes.profile.tool_loop import (
    UPDATE_TIMEZONE_TOOL_NAME,
    build_profile_tool_specs,
    run_profile_tool_calls,
)
from capp.tests import _make_user


class UpdateTimezoneToolDispatchTests(TestCase):
    def setUp(self):
        self.user = _make_user(username="tzuser", email="tzuser@example.com")

    def _tool_call(self, **args):
        return [
            {
                "function": {
                    "name": UPDATE_TIMEZONE_TOOL_NAME,
                    "arguments": json.dumps(args),
                }
            }
        ]

    def test_valid_timezone_persists_and_confirms(self):
        self.assertEqual(self.user.timezone, "Africa/Nairobi")

        block, calls_used = run_profile_tool_calls(self._tool_call(timezone="Europe/Zurich"), user=self.user)

        self.assertEqual(calls_used, 1)
        self.assertIn("updated successfully", block)
        self.assertIn("Europe/Zurich", block)
        self.assertIn("Confirm this back to the user", block)

        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Zurich")

    def test_already_matching_timezone_is_reported_as_no_change(self):
        self.user.timezone = "America/New_York"
        self.user.save(update_fields=["timezone"])

        block, calls_used = run_profile_tool_calls(self._tool_call(timezone="America/New_York"), user=self.user)

        self.assertEqual(calls_used, 1)
        self.assertIn("already set to America/New_York", block)
        self.assertIn("no change needed", block)

        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "America/New_York")

    def test_invalid_timezone_name_is_rejected_without_touching_db(self):
        block, calls_used = run_profile_tool_calls(self._tool_call(timezone="Nairobi/Kenya"), user=self.user)

        self.assertEqual(calls_used, 1)
        self.assertIn("update_timezone failed", block)
        self.assertIn("not a recognized IANA timezone", block)

        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Africa/Nairobi")

    def test_empty_timezone_is_rejected(self):
        block, calls_used = run_profile_tool_calls(self._tool_call(timezone="   "), user=self.user)

        self.assertEqual(calls_used, 1)
        self.assertIn("update_timezone failed", block)
        self.assertIn("empty", block)

    def test_no_matching_tool_call_returns_empty(self):
        tool_calls = [{"function": {"name": "some_other_tool", "arguments": "{}"}}]

        block, calls_used = run_profile_tool_calls(tool_calls, user=self.user)

        self.assertEqual(block, "")
        self.assertEqual(calls_used, 0)

    def test_build_profile_tool_specs_shape(self):
        specs = build_profile_tool_specs()

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["function"]["name"], UPDATE_TIMEZONE_TOOL_NAME)
        self.assertIn("timezone", spec["function"]["parameters"]["properties"])
        self.assertIn("timezone", spec["function"]["parameters"]["required"])
