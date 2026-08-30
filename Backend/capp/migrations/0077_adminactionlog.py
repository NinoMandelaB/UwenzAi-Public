"""Create AdminActionLog table for persistent admin audit trail."""

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0076_billingreservation_claim_provenance_erased_claimer"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminActionLog",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "log_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("user_status_change", "User Status Change"),
                            ("spark_adjustment", "Spark Adjustment"),
                            ("character_hard_delete", "Character Hard Delete"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "target_character_id",
                    models.UUIDField(blank=True, null=True),
                ),
                (
                    "target_character_name",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "reason",
                    models.TextField(blank=True, null=True),
                ),
                (
                    "metadata",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "admin_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="admin_actions_performed",
                        to="capp.customuser",
                    ),
                ),
                (
                    "target_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admin_actions_received",
                        to="capp.customuser",
                    ),
                ),
            ],
            options={
                "db_table": "admin_action_log",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="adminactionlog",
            index=models.Index(
                fields=["admin_user", "created_at"],
                name="admin_actio_admin_u_049b37_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="adminactionlog",
            index=models.Index(
                fields=["target_user", "created_at"],
                name="admin_actio_target__b60369_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="adminactionlog",
            index=models.Index(
                fields=["action_type", "created_at"],
                name="admin_actio_action__a1eff9_idx",
            ),
        ),
    ]
