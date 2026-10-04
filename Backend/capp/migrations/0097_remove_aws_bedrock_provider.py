# Removal of the aws-bedrock provider (2026-09-20): the provider is no longer
# served by the gateway, so its catalog rows must go too — an active leftover
# row would still win the cheapest-provider routing while the provider
# registry can no longer resolve it. Usage logs, reservation ledgers and
# consent records keep their historical provider strings untouched.

from django.db import migrations, models


def remove_bedrock_catalog_rows(apps, schema_editor):
    AgentModelPrice = apps.get_model("capp", "AgentModelPrice")
    AgentModelPrice.objects.filter(provider="aws-bedrock").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0096_aws_bedrock_provider"),
    ]

    operations = [
        migrations.RunPython(remove_bedrock_catalog_rows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agentmodelprice",
            name="provider",
            field=models.CharField(
                help_text=("Provider name (e.g., 'cortecs', 'mistral', 'fikra', 'aki', " "'ovh', 'inceptron')"),
                max_length=50,
            ),
        ),
    ]
