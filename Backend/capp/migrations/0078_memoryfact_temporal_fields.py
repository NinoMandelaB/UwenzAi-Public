"""Add bi-temporal fields to MemoryFact (Phase 1: temporal memory).

- valid_from: event time (when the fact became true)
- valid_to: system time (when the fact stopped being the current truth)
- superseded_by: append-only link to the newer fact that replaced this one
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0077_adminactionlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="memoryfact",
            name="valid_from",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoryfact",
            name="valid_to",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoryfact",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supersedes",
                to="capp.memoryfact",
            ),
        ),
        migrations.AddIndex(
            model_name="memoryfact",
            # Name must match Django's auto-generated convention exactly
            # (see ModelIndexNamer) or makemigrations --check emits a rename.
            index=models.Index(fields=["user", "valid_to"], name="memory_fact_user_id_0dac56_idx"),
        ),
    ]
