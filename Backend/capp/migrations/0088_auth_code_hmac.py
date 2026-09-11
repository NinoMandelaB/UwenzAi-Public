"""Preserve live OTP/expiry/attempt state while removing weak stored secrets."""

import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def upgrade_codes(apps, schema_editor):
    # Freeze the v1 format here rather than importing changeable service code.
    key = (getattr(settings, "AUTH_CODE_HMAC_KEY", None) or settings.SECRET_KEY).encode()
    alias = schema_editor.connection.alias
    for model_name, field, default_purpose in (
        ("PasswordRecovery", "code", "password_reset"),
        ("EmailChangeRequest", "code", "email_change"),
        ("EmailVerification", "code_hash", "email_verification"),
    ):
        model = apps.get_model("capp", model_name)
        for row in model.objects.using(alias).all().iterator(chunk_size=500):
            value = getattr(row, field)
            if value.startswith("h1$"):
                continue
            purpose = default_purpose
            if model_name == "EmailVerification":
                weak_hash = value
                if row.new_email_hash:
                    purpose = "email_change"
            else:
                weak_hash = hashlib.sha256(value.encode()).hexdigest()
            payload = f"uwenzai:otp:v1:{purpose}:{row.user_id}:{weak_hash}".encode()
            digest = "h1$" + hmac.new(key, payload, hashlib.sha256).hexdigest()
            model.objects.using(alias).filter(pk=row.pk).update(**{field: digest})


def downgrade_codes(apps, schema_editor):
    """Rollback: plaintext OTPs are never reconstructed. Converted digests would not
    fit the pre-migration 6-character columns and could never be verified by the
    old code anyway, so in-flight converted codes are discarded; users request a
    fresh code. EmailVerification.code_hash keeps its width, so its digests stay.
    """
    alias = schema_editor.connection.alias
    for model_name in ("PasswordRecovery", "EmailChangeRequest"):
        model = apps.get_model("capp", model_name)
        model.objects.using(alias).filter(code__startswith="h1$").delete()


class Migration(migrations.Migration):
    dependencies = [("capp", "0087_memory_generation_fences")]
    operations = [
        migrations.AlterField("passwordrecovery", "code", models.CharField(max_length=128, db_index=True)),
        migrations.AlterField("emailchangerequest", "code", models.CharField(max_length=128)),
        # Reverse never reconstructs plaintext OTPs; see downgrade_codes.
        migrations.RunPython(upgrade_codes, downgrade_codes),
    ]
