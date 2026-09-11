import uuid

import capp.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def remember_existing_creates(apps, schema_editor):
    """Do not recreate extant pre-upgrade schedules when their turn is retried."""
    Reminder = apps.get_model("capp", "Reminder")
    Execution = apps.get_model("capp", "ReminderToolExecution")
    db = schema_editor.connection.alias
    for reminder in Reminder.objects.using(db).filter(source_message__isnull=False).order_by("created_at").iterator():
        Execution.objects.using(db).get_or_create(
            user_id=reminder.user_id,
            source_message_key=reminder.source_message_id,
            family="reminder",
            defaults={
                "tool_name": "create_reminder",
                "result": (
                    f"[create_reminder result: this message already created reminder {reminder.pk}. "
                    "Do not create it again. Check the current schedule before describing its current state.]"
                ),
            },
        )


class Migration(migrations.Migration):
    dependencies = [("capp", "0085_user_feature_preferences")]

    operations = [
        migrations.AddField(model_name="reminder", name="revision", field=models.PositiveIntegerField(default=1)),
        migrations.CreateModel(
            name="ReminderDeliveryAttempt",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("attempt_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("reminder_key", models.UUIDField(db_index=True)),
                ("revision", models.PositiveIntegerField()),
                ("lease_until", models.DateTimeField(db_index=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("refund_pending", "Refund pending"),
                            ("refunded", "Closed without delivery"),
                            ("delivered", "Delivered"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "reservation",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reminder_attempt",
                        to="capp.billingreservation",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "reminder_delivery_attempts",
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(status="active"),
                        fields=("reminder_key",),
                        name="unique_active_reminder_attempt",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ReminderToolExecution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_message_key", models.UUIDField()),
                ("family", models.CharField(max_length=20)),
                ("tool_name", models.CharField(max_length=40)),
                ("result", capp.fields.EncryptedTextField()),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "reminder_tool_executions",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "source_message_key", "family"),
                        name="unique_reminder_tool_execution",
                    )
                ],
            },
        ),
        migrations.RunPython(remember_existing_creates, migrations.RunPython.noop),
    ]
