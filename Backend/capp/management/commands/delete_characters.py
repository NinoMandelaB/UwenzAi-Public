from django.core.management.base import BaseCommand
from capp.models import Character


class Command(BaseCommand):
    help = "Delete all characters and their related data (conversations, messages, etc.)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        count = Character.objects.count()
        if count == 0:
            self.stdout.write("No characters to delete.")
            return

        if not options["yes"]:
            confirm = input(
                f"This will permanently delete ALL {count} characters and their related data.\n"
                "Are you sure? Type 'yes' to continue: "
            )
            if confirm.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        deleted, _ = Character.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} objects (characters and related data)."))
