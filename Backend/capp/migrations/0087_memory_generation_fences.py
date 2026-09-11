from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("capp", "0086_reminder_durable_lifecycle")]
    operations = [
        migrations.AddField(
            model_name="conversation",
            name="memory_generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="conversation",
            name="summary_generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="conversation",
            name="summary_source_cutoff",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoryfact",
            name="content_key",
            field=models.CharField(max_length=64, blank=True, default="", db_index=True),
        ),
    ]
