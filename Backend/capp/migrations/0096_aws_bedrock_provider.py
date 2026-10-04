# Generated for the aws-bedrock provider addition: only the AgentModelPrice
# provider help_text changes (cosmetic); no schema change is involved.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0095_spark_package_vat"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agentmodelprice",
            name="provider",
            field=models.CharField(
                help_text=(
                    "Provider name (e.g., 'cortecs', 'mistral', 'fikra', 'aki', " "'ovh', 'inceptron', 'aws-bedrock')"
                ),
                max_length=50,
            ),
        ),
    ]
