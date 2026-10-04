"""
Verify the photo moderation provider against the live API before enabling
``PHOTO_UPLOADS_ENABLED`` in production.

Photo uploads are fail-closed: an unverified provider would either reject
every photo (outage) or — far worse — pass photos it never actually screened.
This command is the operational gate referenced by
``capp.classes.moderation.photo_moderation``: it sends a synthetic, locally
generated test image to the configured provider and asserts the response
parses into a real decision.

It deliberately does NOT try to produce a positive CSAM match — no known-bad
material is shipped or generated for tests. A clean "allow" decision with a
parsable, well-formed provider response is the evidence the integration
contract (auth, multipart field name, response shape) is correct. Hash-list
coverage itself is the provider's responsibility.

Usage:
    python manage.py check_photo_moderation
"""

import struct
import zlib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from capp.classes.moderation.photo_moderation import (
    ModerationDecision,
    PhotoModerationUnavailable,
    moderation_provider_name,
    moderate_photo,
)


def _tiny_png_bytes() -> bytes:
    """A 1x1 transparent PNG, built without Pillow so CI needs no extra deps."""
    width = height = 1

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw_scanline = b"\x00" + b"\x00\x00\x00\x00"  # filter byte + RGBA pixel
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw_scanline)) + chunk(b"IEND", b"")
    )


class Command(BaseCommand):
    help = (
        "Send a synthetic test image through the configured photo moderation "
        "provider and verify the response parses into a real decision. Run "
        "once against the live provider before enabling PHOTO_UPLOADS_ENABLED."
    )

    def handle(self, *args, **options):
        provider = moderation_provider_name()
        if provider is None:
            if settings.DEBUG:
                self.stdout.write(
                    self.style.WARNING(
                        "No IMAGE_MODERATION_PROVIDER configured; in DEBUG the "
                        "dev moderator allows everything. Nothing live to check."
                    )
                )
                return
            raise CommandError(
                "IMAGE_MODERATION_PROVIDER is not configured (or unknown). "
                "Photo uploads would be disabled in production."
            )

        if not getattr(settings, "HIVE_API_KEY", "") and provider == "hive":
            raise CommandError("HIVE_API_KEY is not configured for the 'hive' provider.")
        if provider == "mistral_vlm" and not getattr(settings, "MISTRAL_API_KEY", ""):
            raise CommandError("MISTRAL_API_KEY is not configured for the 'mistral_vlm' provider.")

        image = _tiny_png_bytes()
        self.stdout.write(f"Provider: {provider} — sending synthetic 1x1 PNG test image...")

        try:
            decision = moderate_photo(image, "image/png")
        except PhotoModerationUnavailable as exc:
            raise CommandError(
                "Provider call failed or returned an unparsable response — "
                "photo uploads would fail closed (every photo rejected). "
                "Fix the provider integration before enabling PHOTO_UPLOADS_ENABLED."
            ) from exc

        if not isinstance(decision, ModerationDecision):
            raise CommandError(f"Unexpected decision type: {type(decision)!r}")

        if decision.action not in ("allow", "blocked"):
            raise CommandError(f"Unexpected decision action: {decision.action!r}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Provider responded with a parsable decision: action="
                f"{decision.action}, provider={decision.provider}. "
                f"The integration contract (auth, request shape, response "
                f"parsing) checks out."
            )
        )
        if provider == "mistral_vlm":
            self.stdout.write(
                self.style.NOTICE(
                    "This proves connectivity and JSON parsing only — NOT "
                    "detection quality. Before enabling PHOTO_UPLOADS_ENABLED in "
                    "production, also run a small internal evaluation set "
                    "(clearly-labelled benign and known-blocked images you "
                    "control) against this live model and review the verdicts. "
                    "A general vision model has no known-CSAM hash matching; "
                    "the 'hive' provider remains the upgrade path for that."
                )
            )
        elif provider == "hive":
            self.stdout.write(
                self.style.NOTICE(
                    "This proves connectivity and response parsing only — NOT "
                    "policy coverage. Before enabling PHOTO_UPLOADS_ENABLED in "
                    "production, ALSO confirm with Hive that coverage includes "
                    "non-sexual child nudity, not just known-CSAM hash matching "
                    "and the CSAM classifier."
                )
            )
