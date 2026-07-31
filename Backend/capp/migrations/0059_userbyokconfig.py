# capp/migrations/0059_userbyokconfig.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0056_usagecharge_guest_session_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBYOKConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "api_url",
                    models.CharField(
                        help_text="Base URL of the OpenAI-compatible API, e.g. https://api.cortecs.ai/v1",
                        max_length=512,
                    ),
                ),
                (
                    "api_key_encrypted",
                    models.TextField(
                        help_text="Fernet-encrypted API key (FIELD_ENCRYPTION_KEY). Never stored in plain text.",
                    ),
                ),
                (
                    "model_name",
                    models.CharField(
                        help_text="Model identifier passed to the API, e.g. deepseek-v4-flash",
                        max_length=256,
                    ),
                ),
                (
                    "system_prompt",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text=(
                            "Optional system prompt override. Stored in clear text for moderation. "
                            "Must comply with platform Terms of Service."
                        ),
                    ),
                ),
                (
                    "store_on_platform",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "If True the config persists between sessions. "
                            "If False it should be deleted when the user logs out."
                        ),
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="User can toggle BYOK off without deleting credentials.",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="byok_config",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "User BYOK Config",
                "verbose_name_plural": "User BYOK Configs",
            },
        ),
    ]
