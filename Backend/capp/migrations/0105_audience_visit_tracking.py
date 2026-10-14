# Hand-written to match the generated style (see 0104 for reference): two
# AddField operations per model for the audience-analytics visit tracking.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0104_agent_key_usage_source_and_retention"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="first_visit_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customuser",
            name="last_visit_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customuser",
            name="visit_country_code",
            field=models.CharField(blank=True, max_length=2, null=True),
        ),
        migrations.AddField(
            model_name="guestidentity",
            name="first_visit_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="guestidentity",
            name="last_visit_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="guestidentity",
            name="visit_country_code",
            field=models.CharField(blank=True, max_length=2, null=True),
        ),
    ]
