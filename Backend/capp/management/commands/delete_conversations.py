from django.core.management.base import BaseCommand
from capp.models import Conversation


class Command(BaseCommand):
    help = "Delete all conversations and their related data."

    def handle(self, *args, **options):
        count = Conversation.objects.count()
        if count == 0:
            self.stdout.write("No conversations to delete.")
            return

        confirm = input(
            f"This will permanently delete ALL {count} conversations and related messages. Type 'yes' to continue: "
        )
        if confirm.strip().lower() == "yes":
            deleted, _ = Conversation.objects.all().delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} objects (conversations, messages, etc.)."))
        else:
            self.stdout.write(self.style.WARNING("Aborted."))
