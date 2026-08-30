# capp/tests_fal_capabilities.py
"""
Tests for the fal.ai per-endpoint capability map
(`capp/classes/ai/fal_capabilities.py`) and its integration with
`CharacterService._generate_fal_seedream_image`.

The contract under test: fal.ai validates every request against a per-model
Pydantic schema and returns a NON-RETRYABLE 422 for unsupported input
(https://fal.ai/docs/documentation/model-apis/errors), so a payload must never
contain a parameter the selected endpoint does not support.
"""

import base64
import json
import threading
import time
from unittest import mock

import requests
from django.test import TestCase

from capp.classes.ai.fal_capabilities import (
    FAL_ENDPOINT_CAPABILITIES,
    build_fal_payload,
    normalize_endpoint_id,
)

T2I = "fal-ai/bytedance/seedream/v5/lite/text-to-image"
EDIT = "fal-ai/bytedance/seedream/v5/lite/edit"
FLUX_PRO = "fal-ai/flux-pro/v1.1"
FLUX_2_PRO = "fal-ai/flux-2-pro"
FLUX_DEV = "fal-ai/flux/dev"
KONTEXT = "fal-ai/flux-pro/kontext"
KONTEXT_MULTI = "fal-ai/flux-pro/kontext/multi"

_LOGGER = "capp.classes.ai.fal_capabilities"
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FalCapabilityMapTests(TestCase):
    """Pure unit tests over build_fal_payload / the capability map."""

    def test_every_map_entry_documents_its_source(self):
        for endpoint_id, entry in FAL_ENDPOINT_CAPABILITIES.items():
            self.assertTrue(entry["params"], endpoint_id)
            self.assertTrue(entry["docs_url"].startswith("https://fal.ai/"), endpoint_id)

    def test_endpoint_id_normalisation(self):
        self.assertEqual(normalize_endpoint_id("bytedance/seedream/v5/lite/edit"), EDIT)
        self.assertEqual(normalize_endpoint_id("/fal-ai/flux/dev/"), FLUX_DEV)
        self.assertEqual(normalize_endpoint_id(" fal-ai/flux-2-pro "), FLUX_2_PRO)

    # (a) Seedream t2i must never receive safety_tolerance -------------------
    def test_seedream_t2i_never_contains_safety_tolerance(self):
        payload, dropped = build_fal_payload(
            T2I,
            {
                "prompt": "hi",
                "image_size": "square_hd",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
                "safety_tolerance": "6",  # FLUX-only — must not leak to Seedream
            },
        )
        self.assertNotIn("safety_tolerance", payload)
        self.assertIn("safety_tolerance", dropped)
        self.assertEqual(
            payload,
            {
                "prompt": "hi",
                "image_size": "square_hd",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
            },
        )

    def test_seedream_v4_and_v5_share_the_t2i_schema(self):
        for endpoint in (
            "fal-ai/bytedance/seedream/v4/text-to-image",
            "fal-ai/bytedance/seedream/v5/lite/text-to-image",
        ):
            params = FAL_ENDPOINT_CAPABILITIES[endpoint]["params"]
            self.assertNotIn("safety_tolerance", params)
            self.assertNotIn("image_urls", params)
            self.assertIn("enable_safety_checker", params)

    def test_edit_endpoints_require_image_urls_max_ten(self):
        for endpoint in ("fal-ai/bytedance/seedream/v4/edit", EDIT):
            params = FAL_ENDPOINT_CAPABILITIES[endpoint]["params"]
            self.assertTrue(params["image_urls"]["required"])
            self.assertEqual(params["image_urls"]["max_items"], 10)

    def test_image_urls_truncated_to_ten(self):
        urls = [f"https://fal.cdn/{i}.webp" for i in range(12)]
        payload, _dropped = build_fal_payload(EDIT, {"prompt": "hi", "image_urls": urls})
        self.assertEqual(payload["image_urls"], urls[:10])

    # (b) FLUX safety_tolerance handling -------------------------------------
    def test_flux_pro_accepts_valid_safety_tolerance(self):
        payload, _dropped = build_fal_payload(FLUX_PRO, {"prompt": "hi", "safety_tolerance": "4"})
        self.assertEqual(payload["safety_tolerance"], "4")

    def test_flux_pro_coerces_int_tolerance_to_string_enum(self):
        # fal declares safety_tolerance as a STRING enum; an int would 422.
        payload, _dropped = build_fal_payload(FLUX_PRO, {"prompt": "hi", "safety_tolerance": 6})
        self.assertEqual(payload["safety_tolerance"], "6")

    def test_flux_pro_out_of_range_tolerance_falls_back_to_default_with_warning(self):
        with self.assertLogs(_LOGGER, level="WARNING") as logs:
            payload, dropped = build_fal_payload(FLUX_PRO, {"prompt": "hi", "safety_tolerance": "9"})
        self.assertEqual(payload["safety_tolerance"], "2")
        self.assertNotIn("safety_tolerance", dropped)
        self.assertTrue(any("falling back to default" in line for line in logs.output))

    def test_flux2_pro_bounds_differ_from_v1(self):
        # FLUX 2 Pro only allows "1"-"5" — "6" is valid on v1/v1.1 but not here.
        payload, _dropped = build_fal_payload(FLUX_2_PRO, {"prompt": "hi", "safety_tolerance": "6"})
        self.assertEqual(payload["safety_tolerance"], "2")
        payload, _dropped = build_fal_payload(FLUX_2_PRO, {"prompt": "hi", "safety_tolerance": "5"})
        self.assertEqual(payload["safety_tolerance"], "5")
        # FLUX 2 Pro has no num_images knob either.
        payload, dropped = build_fal_payload(FLUX_2_PRO, {"prompt": "hi", "num_images": 2})
        self.assertNotIn("num_images", payload)
        self.assertIn("num_images", dropped)

    def test_flux_dev_has_checker_but_no_tolerance(self):
        params = FAL_ENDPOINT_CAPABILITIES[FLUX_DEV]["params"]
        self.assertIn("enable_safety_checker", params)
        self.assertNotIn("safety_tolerance", params)
        self.assertNotIn("safety_tolerance", FAL_ENDPOINT_CAPABILITIES["fal-ai/flux/schnell"]["params"])

    # (c) unknown params are dropped with a log ------------------------------
    def test_unknown_param_dropped_with_debug_log(self):
        with self.assertLogs(_LOGGER, level="DEBUG") as logs:
            payload, dropped = build_fal_payload(T2I, {"prompt": "hi", "cg_nonsense": 1})
        self.assertNotIn("cg_nonsense", payload)
        self.assertIn("cg_nonsense", dropped)
        self.assertTrue(
            any("param cg_nonsense not supported by endpoint" in line and "dropping" in line for line in logs.output)
        )

    def test_numeric_params_clamped_to_documented_range(self):
        payload, _dropped = build_fal_payload(T2I, {"prompt": "hi", "num_images": 42, "max_images": 0})
        self.assertEqual(payload["num_images"], 6)  # clamped to documented max
        self.assertEqual(payload["max_images"], 1)  # clamped to documented min

    def test_empty_and_none_values_omitted(self):
        payload, _dropped = build_fal_payload(T2I, {"prompt": "hi", "image_size": "", "seed": None})
        self.assertNotIn("image_size", payload)
        self.assertNotIn("seed", payload)

    # unknown endpoint ids: permissive core pass-through, documented ---------
    def test_unknown_endpoint_passes_core_params_and_warns(self):
        with self.assertLogs(_LOGGER, level="WARNING") as logs:
            payload, dropped = build_fal_payload(
                "fal-ai/acme/brand-new-model",
                {
                    "prompt": "hi",
                    "image_size": "square_hd",
                    "enable_safety_checker": False,
                    "safety_tolerance": "6",  # NOT in the core set — dropped
                    "mystery_knob": True,
                },
            )
        self.assertEqual(
            payload,
            {"prompt": "hi", "image_size": "square_hd", "enable_safety_checker": False},
        )
        self.assertEqual(dropped, frozenset({"safety_tolerance", "mystery_knob"}))
        self.assertTrue(any("no capability entry" in line for line in logs.output))


class KontextCapabilityTests(TestCase):
    """FLUX Pro Kontext entries and payload-builder param-name translation.

    Docs: https://fal.ai/models/fal-ai/flux-pro/kontext/api and
    https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt
    """

    DESIRED_EDIT = {
        "prompt": "same character, beach",
        "image_size": "portrait_4_3",
        "num_images": 1,
        "max_images": 1,  # Seedream-only knob — never valid on Kontext
        "enable_safety_checker": False,  # not in the Kontext schema at all
        "safety_tolerance": "6",
        "image_urls": ["https://fal.cdn/ref.webp"],
        "seed": 1234,
    }

    def test_single_kontext_schema_matches_docs(self):
        params = FAL_ENDPOINT_CAPABILITIES[KONTEXT]["params"]
        self.assertTrue(params["image_url"]["required"])
        self.assertNotIn("image_urls", params)
        self.assertIn("aspect_ratio", params)
        self.assertNotIn("image_size", params)
        self.assertTrue(params["enhance_prompt"]["default"] is False)

    def test_kontext_never_contains_image_size_image_urls_or_checker(self):
        payload, dropped = build_fal_payload(KONTEXT, self.DESIRED_EDIT)
        self.assertEqual(
            payload,
            {
                "prompt": "same character, beach",
                "aspect_ratio": "3:4",  # translated from image_size portrait_4_3
                "num_images": 1,
                "safety_tolerance": "6",
                "image_url": "https://fal.cdn/ref.webp",  # singular, translated
                "seed": 1234,
            },
        )
        for forbidden in ("image_size", "image_urls", "max_images", "enable_safety_checker"):
            self.assertNotIn(forbidden, payload)
        # image_size/image_urls are consumed by translation (not "dropped");
        # Seedream-only params are dropped with a debug log.
        self.assertEqual(dropped, frozenset({"max_images", "enable_safety_checker"}))

    def test_kontext_multi_keeps_image_urls_list(self):
        urls = ["https://fal.cdn/a.webp", "https://fal.cdn/b.webp"]
        payload, _dropped = build_fal_payload(KONTEXT_MULTI, {**self.DESIRED_EDIT, "image_urls": urls})
        self.assertEqual(payload["image_urls"], urls)
        self.assertNotIn("image_url", payload)
        self.assertEqual(payload["aspect_ratio"], "3:4")
        self.assertNotIn("image_size", payload)

    def test_kontext_multi_has_no_enhance_prompt(self):
        # Single kontext accepts enhance_prompt; multi's schema does not.
        payload, dropped = build_fal_payload(KONTEXT_MULTI, {"prompt": "hi", "enhance_prompt": True})
        self.assertNotIn("enhance_prompt", payload)
        self.assertIn("enhance_prompt", dropped)
        payload, _dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "enhance_prompt": True})
        self.assertIs(payload["enhance_prompt"], True)

    def test_multi_image_collapse_warns_and_uses_first_url(self):
        with self.assertLogs(_LOGGER, level="WARNING") as logs:
            payload, _dropped = build_fal_payload(
                KONTEXT,
                {
                    "prompt": "hi",
                    "image_urls": ["https://fal.cdn/first.webp", "https://fal.cdn/second.webp"],
                },
            )
        self.assertEqual(payload["image_url"], "https://fal.cdn/first.webp")
        self.assertNotIn("image_urls", payload)
        self.assertTrue(any("dropping the rest" in line for line in logs.output))

    def test_empty_image_urls_cannot_translate(self):
        payload, dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "image_urls": []})
        self.assertNotIn("image_url", payload)
        self.assertIn("image_urls", dropped)

    def test_explicit_image_url_wins_over_list(self):
        payload, _dropped = build_fal_payload(
            KONTEXT,
            {
                "prompt": "hi",
                "image_url": "https://fal.cdn/explicit.webp",
                "image_urls": ["https://fal.cdn/ignored.webp"],
            },
        )
        self.assertEqual(payload["image_url"], "https://fal.cdn/explicit.webp")

    def test_safety_tolerance_env_reaches_kontext_but_not_checker(self):
        payload, _dropped = build_fal_payload(
            KONTEXT,
            {"prompt": "hi", "image_urls": ["https://fal.cdn/ref.webp"], "safety_tolerance": "5"},
        )
        self.assertEqual(payload["safety_tolerance"], "5")
        self.assertNotIn("enable_safety_checker", payload)

    def test_guidance_scale_clamped_to_kontext_range(self):
        payload, _dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "guidance_scale": 99})
        self.assertEqual(payload["guidance_scale"], 20)
        payload, _dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "guidance_scale": 0})
        self.assertEqual(payload["guidance_scale"], 1)

    def test_image_size_to_aspect_ratio_preset_mapping(self):
        cases = {
            "square_hd": "1:1",
            "square": "1:1",
            "portrait_4_3": "3:4",
            "portrait_16_9": "9:16",
            "landscape_4_3": "4:3",
            "landscape_16_9": "16:9",
            "2:3": "2:3",  # already an aspect-ratio string passes through
        }
        for image_size, expected in cases.items():
            with self.subTest(image_size=image_size):
                payload, _dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "image_size": image_size})
                self.assertEqual(payload.get("aspect_ratio"), expected)
                self.assertNotIn("image_size", payload)

    def test_custom_width_height_maps_to_nearest_ratio(self):
        cases = [
            ({"width": 1024, "height": 1024}, "1:1"),
            ({"width": 1920, "height": 1080}, "16:9"),
            ({"width": 1080, "height": 1920}, "9:16"),
            ({"width": 1600, "height": 900}, "16:9"),
            ({"width": 1200, "height": 900}, "4:3"),
            ({"width": 2560, "height": 1080}, "21:9"),
        ]
        for image_size, expected in cases:
            with self.subTest(image_size=image_size):
                payload, _dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "image_size": image_size})
                self.assertEqual(payload.get("aspect_ratio"), expected)

    def test_unmappable_image_size_dropped_with_warning(self):
        # Seedream-only auto presets carry no ratio info; Kontext then uses
        # its own default geometry rather than receiving a made-up ratio.
        with self.assertLogs(_LOGGER, level="WARNING") as logs:
            payload, dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "image_size": "auto_2K"})
        self.assertNotIn("aspect_ratio", payload)
        self.assertNotIn("image_size", payload)
        self.assertIn("image_size", dropped)
        self.assertTrue(any("no aspect_ratio mapping" in line for line in logs.output))

    def test_explicit_aspect_ratio_wins_over_image_size(self):
        payload, _dropped = build_fal_payload(
            KONTEXT, {"prompt": "hi", "aspect_ratio": "21:9", "image_size": "square_hd"}
        )
        self.assertEqual(payload["aspect_ratio"], "21:9")
        self.assertNotIn("image_size", payload)

    def test_invalid_aspect_ratio_dropped_on_kontext(self):
        # aspect_ratio has NO documented default on Kontext, so an out-of-enum
        # value is dropped (with a warning), not silently replaced.
        payload, dropped = build_fal_payload(KONTEXT, {"prompt": "hi", "aspect_ratio": "7:3"})
        self.assertNotIn("aspect_ratio", payload)
        self.assertIn("aspect_ratio", dropped)

    def test_seedream_unaffected_when_given_aspect_ratio_style_input(self):
        # Translation is endpoint-driven: Seedream still wants image_size and
        # must not suddenly accept aspect_ratio.
        payload, dropped = build_fal_payload(EDIT, {"prompt": "hi", "aspect_ratio": "16:9"})
        self.assertNotIn("aspect_ratio", payload)
        self.assertIn("aspect_ratio", dropped)


class FalPayloadEnvOverrideTests(TestCase):
    """
    The request helper reads FAL_ENABLE_SAFETY_CHECKER / FAL_SAFETY_TOLERANCE /
    the endpoint-id settings at request time and routes the result through the
    capability map.
    """

    def setUp(self):
        self._post_returns = self._fake_ok_response()

    @staticmethod
    def _fake_ok_response():
        resp = mock.Mock()
        resp.status_code = 200
        resp.headers = {}
        resp.iter_content.return_value = [json.dumps({"images": [{"url": "https://fal.cdn/out.webp"}]}).encode("utf-8")]
        return resp

    def _call_helper(self, _image_urls=None, **settings_overrides):
        from capp.classes.characters.character_service import CharacterService

        post = mock.Mock(return_value=self._fake_ok_response())
        get_response = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        get_response.iter_content.return_value = [ONE_PIXEL_PNG]
        get = mock.Mock(return_value=get_response)
        base = {
            "FAL_API_KEY": "k",
            "FAL_API_BASE_URL": "https://fal.run",
            "FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS": ("fal.cdn",),
        }
        base.update(settings_overrides)
        with mock.patch("requests.post", post), mock.patch("requests.get", get), self.settings(**base):
            out = CharacterService._generate_fal_seedream_image(
                prompt="hi",
                image_urls=_image_urls,
                image_size="square_hd",
            )
        return out, post

    def test_env_safety_checker_override_reaches_payload(self):
        out, post = self._call_helper(FAL_ENABLE_SAFETY_CHECKER=True)
        self.assertEqual(out, ONE_PIXEL_PNG)
        self.assertIs(post.call_args.kwargs["json"]["enable_safety_checker"], True)

    def test_default_safety_checker_matches_legacy_payload(self):
        _out, post = self._call_helper()
        self.assertIs(post.call_args.kwargs["json"]["enable_safety_checker"], False)

    def test_env_endpoint_id_changes_payload_target(self):
        _out, post = self._call_helper(FAL_TEXT_TO_IMAGE_MODEL="fal-ai/flux-pro/v1.1")
        self.assertEqual(post.call_args.args[0], "https://fal.run/fal-ai/flux-pro/v1.1")
        payload = post.call_args.kwargs["json"]
        # Swapping to a FLUX Pro endpoint must not send Seedream-only max_images,
        # and the default FAL_SAFETY_TOLERANCE ("2") is valid there.
        self.assertNotIn("max_images", payload)
        self.assertEqual(payload["safety_tolerance"], "2")

    def test_invalid_tolerance_env_falls_back_with_warning_on_flux(self):
        with self.assertLogs(_LOGGER, level="WARNING") as logs:
            _out, post = self._call_helper(
                FAL_TEXT_TO_IMAGE_MODEL="fal-ai/flux-pro/v1.1",
                FAL_SAFETY_TOLERANCE="extreme",
            )
        self.assertEqual(post.call_args.kwargs["json"]["safety_tolerance"], "2")
        self.assertTrue(any("falling back to default" in line for line in logs.output))

    def test_seedream_edit_never_sends_tolerance_even_when_env_set(self):
        _out, post = self._call_helper(FAL_SAFETY_TOLERANCE="6")
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("safety_tolerance", payload)

    def test_kontext_edit_endpoint_works_purely_via_env(self):
        # FAL_IMAGE_EDIT_MODEL=fal-ai/flux-pro/kontext: the builder translates
        # image_urls -> image_url (singular) and image_size -> aspect_ratio.
        out, post = self._call_helper(
            _image_urls=["https://fal.cdn/ref.webp"],
            FAL_IMAGE_EDIT_MODEL="fal-ai/flux-pro/kontext",
            FAL_SAFETY_TOLERANCE="5",
        )
        self.assertEqual(out, ONE_PIXEL_PNG)
        self.assertEqual(post.call_args.args[0], "https://fal.run/fal-ai/flux-pro/kontext")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["image_url"], "https://fal.cdn/ref.webp")
        self.assertEqual(payload["aspect_ratio"], "1:1")  # image_size square_hd
        self.assertEqual(payload["safety_tolerance"], "5")  # env flows through
        for forbidden in ("image_urls", "image_size", "max_images", "enable_safety_checker"):
            self.assertNotIn(forbidden, payload)

    def test_kontext_multi_edit_endpoint_keeps_reference_list(self):
        urls = ["https://fal.cdn/a.webp", "https://fal.cdn/b.webp"]
        _out, post = self._call_helper(_image_urls=urls, FAL_IMAGE_EDIT_MODEL="fal-ai/flux-pro/kontext/multi")
        self.assertEqual(post.call_args.args[0], "https://fal.run/fal-ai/flux-pro/kontext/multi")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["image_urls"], urls)
        self.assertNotIn("image_url", payload)
        self.assertEqual(payload["aspect_ratio"], "1:1")

    def test_seedream_edit_payload_untouched_by_kontext_translation(self):
        # Regression: default Seedream edit stays byte-equivalent.
        _out, post = self._call_helper(_image_urls=["https://fal.cdn/ref.webp"], FAL_SAFETY_TOLERANCE="6")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["image_urls"], ["https://fal.cdn/ref.webp"])
        self.assertEqual(payload["image_size"], "square_hd")
        self.assertNotIn("image_url", payload)
        self.assertNotIn("aspect_ratio", payload)
        self.assertNotIn("safety_tolerance", payload)

    def test_unknown_edit_endpoint_fails_closed_before_http(self):
        from capp.classes.characters.character_service import CharacterService

        with (
            mock.patch("requests.post") as post,
            self.settings(
                FAL_API_KEY="k",
                FAL_API_BASE_URL="https://fal.run",
                FAL_IMAGE_EDIT_MODEL="fal-ai/acme/unknown-edit-model",
            ),
        ):
            out = CharacterService._generate_fal_seedream_image(
                prompt="hi",
                image_urls=["https://fal.cdn/ref.webp"],
            )

        self.assertIsNone(out)
        post.assert_not_called()

    def test_disallowed_download_url_is_rejected_and_request_is_deleted(self):
        from capp.classes.characters.character_service import CharacterService

        response = self._fake_ok_response()
        response.headers = {"X-Fal-Request-Id": "request-123"}
        response.iter_content.return_value = [
            json.dumps({"images": [{"url": "https://attacker.example/image.png"}]}).encode("utf-8")
        ]
        with (
            mock.patch("requests.post", return_value=response),
            mock.patch("requests.get") as get,
            mock.patch.object(CharacterService, "_schedule_fal_cleanup") as delete,
            self.settings(FAL_API_KEY="k", FAL_API_BASE_URL="https://fal.run"),
        ):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")

        self.assertIsNone(out)
        get.assert_not_called()
        delete.assert_called_once_with("request-123", "k")

    def test_non_success_response_with_request_id_is_deleted(self):
        from capp.classes.characters.character_service import CharacterService

        response = mock.Mock(status_code=422, text="invalid")
        response.headers = {"X-Fal-Request-Id": "request-422"}
        response.iter_content.return_value = [b"invalid"]
        with (
            mock.patch("requests.post", return_value=response),
            mock.patch.object(CharacterService, "_schedule_fal_cleanup") as delete,
            self.settings(FAL_API_KEY="k", FAL_API_BASE_URL="https://fal.run"),
        ):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")

        self.assertIsNone(out)
        delete.assert_called_once_with("request-422", "k")

    def test_streaming_download_stops_at_byte_limit(self):
        from capp.classes.characters.character_service import CharacterService

        response = self._fake_ok_response()
        response.headers = {"X-Fal-Request-Id": "request-large"}
        download = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        download.iter_content.return_value = [b"1234", b"5678"]
        with (
            mock.patch("requests.post", return_value=response),
            mock.patch("requests.get", return_value=download) as get,
            mock.patch.object(CharacterService, "_schedule_fal_cleanup") as delete,
            self.settings(
                FAL_API_KEY="k",
                FAL_API_BASE_URL="https://fal.run",
                FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS=("fal.cdn",),
                FAL_IMAGE_MAX_RESPONSE_BYTES=5,
            ),
        ):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")

        self.assertIsNone(out)
        self.assertTrue(get.call_args.kwargs["stream"])
        download.close.assert_called_once()
        delete.assert_called_once_with("request-large", "k")

    def test_streaming_network_error_returns_none_for_provider_fallback(self):
        from capp.classes.characters.character_service import CharacterService

        response = self._fake_ok_response()
        response.headers = {"X-Fal-Request-Id": "request-timeout"}
        download = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        download.iter_content.side_effect = requests.ReadTimeout("slow stream")
        with (
            mock.patch("requests.post", return_value=response),
            mock.patch("requests.get", return_value=download),
            mock.patch.object(CharacterService, "_schedule_fal_cleanup") as delete,
            self.settings(
                FAL_API_KEY="k",
                FAL_API_BASE_URL="https://fal.run",
                FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS=("fal.cdn",),
            ),
        ):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")

        self.assertIsNone(out)
        download.close.assert_called_once()
        delete.assert_called_once_with("request-timeout", "k")

    def test_soft_time_limit_from_provider_call_propagates(self):
        from celery.exceptions import SoftTimeLimitExceeded
        from capp.classes.characters.character_service import CharacterService

        with (
            mock.patch("requests.post", side_effect=SoftTimeLimitExceeded()),
            self.settings(FAL_API_KEY="k", FAL_API_BASE_URL="https://fal.run"),
            self.assertRaises(SoftTimeLimitExceeded),
        ):
            CharacterService._generate_fal_seedream_image(prompt="hi")

    def test_expired_shared_deadline_blocks_provider_call(self):
        from capp.classes.characters.character_service import (
            CharacterService,
            ImageGenerationDeadlineExceeded,
        )

        with (
            mock.patch("requests.post") as post,
            self.settings(FAL_API_KEY="k", FAL_API_BASE_URL="https://fal.run"),
            self.assertRaises(ImageGenerationDeadlineExceeded),
        ):
            CharacterService._generate_fal_seedream_image(
                prompt="hi",
                deadline=0,
            )

        post.assert_not_called()

    def test_wall_clock_deadline_interrupts_non_yielding_io(self):
        from capp.classes.characters.character_service import (
            CharacterService,
            ImageGenerationDeadlineExceeded,
        )

        release = threading.Event()
        completed = threading.Event()

        def blocked_operation():
            release.wait(timeout=1)
            completed.set()

        started = time.monotonic()
        try:
            with self.assertRaises(ImageGenerationDeadlineExceeded):
                CharacterService._run_with_image_deadline(
                    blocked_operation,
                    deadline=started + 0.03,
                )

            self.assertFalse(completed.is_set())
        finally:
            release.set()

    def test_blocking_response_close_cannot_recapture_caller(self):
        from capp.classes.characters.character_service import (
            CharacterService,
            ImageGenerationDeadlineExceeded,
        )

        release = threading.Event()
        close_completed = threading.Event()

        def blocked_close():
            release.wait(timeout=1)
            close_completed.set()

        response = mock.Mock(headers={})
        response.iter_content.return_value = [b"image"]
        response.close.side_effect = blocked_close
        started = time.monotonic()
        try:
            with self.assertRaises(ImageGenerationDeadlineExceeded):
                CharacterService._run_with_image_deadline(
                    lambda: CharacterService._read_and_close_bounded_response(
                        response,
                        deadline=started + 0.03,
                        max_bytes=1024,
                    ),
                    deadline=started + 0.03,
                )

            self.assertFalse(close_completed.is_set())
        finally:
            release.set()

    def test_bounded_executor_rejects_instead_of_queueing(self):
        from capp.classes.characters.character_service import _BoundedExecutor

        release = threading.Event()
        executor = _BoundedExecutor(max_workers=1, thread_name_prefix="test-image-io")
        running = executor.submit(lambda: release.wait(timeout=0.3))
        self.assertIsNotNone(running)
        self.assertIsNone(executor.submit(lambda: None))
        release.set()
        running.result(timeout=0.3)

    def test_soft_time_limit_from_stream_propagates(self):
        from celery.exceptions import SoftTimeLimitExceeded
        from capp.classes.characters.character_service import CharacterService

        response = self._fake_ok_response()
        download = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        download.iter_content.side_effect = SoftTimeLimitExceeded()
        with (
            mock.patch("requests.post", return_value=response),
            mock.patch("requests.get", return_value=download),
            self.settings(
                FAL_API_KEY="k",
                FAL_API_BASE_URL="https://fal.run",
                FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS=("fal.cdn",),
            ),
            self.assertRaises(SoftTimeLimitExceeded),
        ):
            CharacterService._generate_fal_seedream_image(prompt="hi")

        download.close.assert_called_once()

    def test_reference_upload_uses_short_lifecycle(self):
        from capp.classes.characters.character_service import CharacterService

        client = mock.Mock()
        lifecycle = mock.Mock()
        with (
            mock.patch("fal_client.SyncClient", return_value=client),
            mock.patch("fal_client.StorageSettings", return_value=lifecycle) as settings_factory,
            self.settings(FAL_API_KEY="k"),
        ):
            client.upload.return_value = "https://fal.media/reference.webp"
            result = CharacterService._upload_to_fal_storage(b"image", "reference.webp")

        self.assertEqual(result, "https://fal.media/reference.webp")
        settings_factory.assert_called_once_with(expires_in="1h")
        client.upload.assert_called_once_with(
            b"image",
            "image/webp",
            file_name="reference.webp",
            lifecycle=lifecycle,
        )

    def test_reference_upload_obeys_wall_clock_deadline(self):
        from capp.classes.characters.character_service import (
            CharacterService,
            ImageGenerationDeadlineExceeded,
        )

        release = threading.Event()
        upload_completed = threading.Event()

        def blocked_upload(*args, **kwargs):
            release.wait(timeout=1)
            upload_completed.set()

        client = mock.Mock()
        client.upload.side_effect = blocked_upload
        started = time.monotonic()
        try:
            with (
                mock.patch("fal_client.SyncClient", return_value=client),
                mock.patch("fal_client.StorageSettings"),
                self.settings(FAL_API_KEY="k"),
                self.assertRaises(ImageGenerationDeadlineExceeded),
            ):
                CharacterService._upload_to_fal_storage(
                    b"image",
                    "reference.webp",
                    deadline=started + 0.03,
                )

            self.assertFalse(upload_completed.is_set())
        finally:
            release.set()

    def test_reference_storage_read_obeys_wall_clock_deadline(self):
        from capp.classes.characters.character_service import (
            CharacterService,
            ImageGenerationDeadlineExceeded,
        )

        release = threading.Event()
        read_completed = threading.Event()
        closed = threading.Event()

        def blocked_read():
            release.wait(timeout=1)
            read_completed.set()

        avatar = mock.Mock()
        avatar.read.side_effect = blocked_read
        avatar.close.side_effect = closed.set
        character = mock.Mock(avatar=avatar, character_id="char-1")
        started = time.monotonic()
        try:
            with self.assertRaises(ImageGenerationDeadlineExceeded):
                CharacterService._get_character_reference_bytes(
                    character,
                    deadline=started + 0.03,
                )

            self.assertFalse(read_completed.is_set())
            self.assertFalse(closed.is_set())
        finally:
            release.set()


class SeedreamPayloadRegressionTests(TestCase):
    """
    Byte-for-byte regression: with default settings the refactored builder
    produces exactly the payloads the ad-hoc construction used to send.
    """

    def test_text_to_image_payload_unchanged(self):
        payload, dropped = build_fal_payload(
            "bytedance/seedream/v5/lite/text-to-image",
            {
                "prompt": "a portrait",
                "image_size": "square_hd",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
                "safety_tolerance": "2",  # always offered now; must be dropped
            },
        )
        self.assertEqual(
            payload,
            {
                "prompt": "a portrait",
                "image_size": "square_hd",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
            },
        )
        self.assertEqual(dropped, frozenset({"safety_tolerance"}))

    def test_edit_payload_with_seed_unchanged(self):
        payload, _dropped = build_fal_payload(
            "bytedance/seedream/v5/lite/edit",
            {
                "prompt": "same character, beach",
                "image_size": "portrait_4_3",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
                "safety_tolerance": "2",
                "image_urls": ["https://fal.cdn/ref.webp"],
                "seed": 1234,
            },
        )
        self.assertEqual(
            payload,
            {
                "prompt": "same character, beach",
                "image_size": "portrait_4_3",
                "num_images": 1,
                "max_images": 1,
                "enable_safety_checker": False,
                "image_urls": ["https://fal.cdn/ref.webp"],
                "seed": 1234,
            },
        )
