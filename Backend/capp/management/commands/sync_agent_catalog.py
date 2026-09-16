# capp/management/commands/sync_agent_catalog.py
from django.core.management.base import BaseCommand, CommandError

from capp.classes.agent_gateway.catalog_sync import run_catalog_sync


class Command(BaseCommand):
    help = (
        "Fetch current model catalogs from all configured Agent Gateway providers and "
        "upsert them into AgentModelPrice (the developer-workspace pricing catalog). "
        "Run this once on a fresh environment, after adding a provider key, or any time "
        "to force a refresh outside the daily schedule."
    )

    def handle(self, *args, **options):
        summary = run_catalog_sync()
        ok = failed = skipped = 0
        for name in sorted(summary):
            result = summary[name]
            status = result["status"]
            detail = ""
            if result.get("error"):
                detail = " — {}".format(result["error"])
            line = "{}: {} ({} models){}".format(name, status, result["models"], detail)
            if status == "ok":
                ok += 1
                self.stdout.write(self.style.SUCCESS(line))
            elif status == "not_configured":
                skipped += 1
                self.stdout.write(self.style.NOTICE(line))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(line))
        self.stdout.write(
            "Catalog sync finished: {} refreshed, {} not configured, {} failed.".format(ok, skipped, failed)
        )
        # A partial refresh still updated the catalog, so only an across-the-board
        # failure should fail the command (e.g. in a deploy hook or fresh setup).
        if failed and ok == 0:
            raise CommandError("Catalog sync failed for every configured provider; no catalog was refreshed.")
