# capp/management/commands/add_tokens.py

from django.core.management.base import BaseCommand, CommandError
from capp.models import CustomUser, SparkPool


class Command(BaseCommand):
    help = "Add tokens to a user's spark_pool for testing purposes."

    def add_arguments(self, parser):
        parser.add_argument("username", type=str, help="Username of the user")
        parser.add_argument("amount", type=int, help="Number of tokens to add")

    def handle(self, *args, **options):
        username = options["username"]
        amount = options["amount"]

        try:
            user = CustomUser.objects.get(username=username)
        except CustomUser.DoesNotExist:
            raise CommandError(f"User '{username}' not found.")

        spark_pool, created = SparkPool.objects.get_or_create(user=user, defaults={"sparks": 0, "token_balance": 0})
        spark_pool.token_balance += amount
        spark_pool.save(update_fields=["token_balance", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(f"Added {amount} tokens to user '{username}'. New balance: {spark_pool.token_balance}")
        )
