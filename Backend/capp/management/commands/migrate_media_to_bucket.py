# capp/management/commands/migrate_media_to_bucket.py

"""
Copy existing local media files into the configured (bucket) storage backend
WITHOUT touching database file names.

Files are keyed by their path RELATIVE to the source directory (default
``MEDIA_ROOT``), which is exactly how Django stores ``FileField.name``. Copying
each file to the same relative key in the bucket means every existing DB row
keeps working unchanged once ``STORAGES["default"]`` points at the bucket.

Safety properties:
  * ``--dry-run`` reports what would happen and writes nothing.
  * Idempotent: an object already present in the destination is SKIPPED
    (unless ``--overwrite`` is passed).
  * Never deletes local source files.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copy local media files into the configured storage backend (e.g. Railway Bucket) without renaming them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default=None,
            help="Directory to copy from (default: MEDIA_ROOT).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report actions without writing anything.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Re-upload files that already exist in the destination (default: skip).",
        )

    def handle(self, *args, **options):
        source = options["source"] or getattr(settings, "MEDIA_ROOT", None)
        if not source:
            raise CommandError("No source directory: pass --source or configure MEDIA_ROOT.")

        source_path = Path(source)
        if not source_path.is_dir():
            raise CommandError(f"Source directory does not exist: {source_path}")

        dry_run = options["dry_run"]
        overwrite = options["overwrite"]

        backend = default_storage.__class__.__name__
        self.stdout.write(f"Source: {source_path}")
        self.stdout.write(f"Destination backend: {backend}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no files will be written."))

        copied = skipped = failed = 0

        for root, _dirs, files in os.walk(source_path):
            for filename in files:
                abs_path = Path(root) / filename
                # Storage key = path relative to source, POSIX-style, matching
                # how Django persists FileField.name.
                key = abs_path.relative_to(source_path).as_posix()

                try:
                    exists = default_storage.exists(key)
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(self.style.ERROR(f"FAILED (exists check) {key}: {exc}"))
                    failed += 1
                    continue

                if exists and not overwrite:
                    self.stdout.write(f"SKIP (exists) {key}")
                    skipped += 1
                    continue

                if dry_run:
                    action = "OVERWRITE" if exists else "COPY"
                    self.stdout.write(f"{action} (dry-run) {key}")
                    copied += 1
                    continue

                try:
                    if exists and overwrite:
                        default_storage.delete(key)
                    with abs_path.open("rb") as fh:
                        saved_name = default_storage.save(key, File(fh))
                    if saved_name != key:
                        # file_overwrite=False could rename on collision; we
                        # deleted first when overwriting, so this signals drift.
                        self.stderr.write(self.style.WARNING(f"NOTE saved as {saved_name} (requested {key})"))
                    self.stdout.write(self.style.SUCCESS(f"COPIED {key}"))
                    copied += 1
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(self.style.ERROR(f"FAILED {key}: {exc}"))
                    failed += 1

        self.stdout.write(self.style.SUCCESS(f"Done. copied={copied} skipped={skipped} failed={failed}"))
        if failed:
            raise CommandError(f"{failed} file(s) failed to copy.")
