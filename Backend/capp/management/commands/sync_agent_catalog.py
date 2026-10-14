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
        ok = failed = skipped_providers = 0
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
                new_skipped = result.get("new_skipped_models") or []
                if new_skipped:
                    self.stdout.write(
                        self.style.WARNING(
                            "{} new model(s) served upstream without a price — not in the catalog "
                            "until priced (add to the provider's MODEL_PRICES map in settings): "
                            "{}".format(len(new_skipped), ", ".join(new_skipped))
                        )
                    )
            elif status == "not_configured":
                skipped_providers += 1
                self.stdout.write(self.style.NOTICE(line))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(line))
        self.stdout.write(
            "Catalog sync finished: {} refreshed, {} not configured, {} failed.".format(ok, skipped_providers, failed)
        )
        # A partial refresh still updated the catalog, so only an across-the-board
        # failure should fail the command (e.g. in a deploy hook or fresh setup).
        if failed and ok == 0:
            raise CommandError("Catalog sync failed for every configured provider; no catalog was refreshed.")
