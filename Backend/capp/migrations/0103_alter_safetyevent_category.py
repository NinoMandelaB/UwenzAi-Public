# Adds the photo_child_safety category for blocked photo uploads (F4).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("capp", "0102_alter_adminactionlog_action_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="safetyevent",
            name="category",
            field=models.CharField(
                choices=[
                    ("self_harm", "Self-harm concern (care, non-punitive)"),
                    ("weapons_context", "Weapons interest (concern signal)"),
                    ("weapons_instructions", "Weapon construction instructions"),
                    ("violence_planning", "Violence planning"),
                    ("threats_targeted", "Targeted threat against a person"),
                    ("human_trafficking", "Human trafficking"),
                    ("gender_violence", "Gender-based violence"),
                    ("csam", "Child sexual abuse material"),
                    ("model_refusal", "Model refused a request (advisory, never punitive)"),
                    ("photo_child_safety", "Blocked child-safety photo upload"),
                ],
                max_length=40,
            ),
        ),
    ]
