from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Rename coin/wallet terminology to spark/spark pool.

    Covers model renames, field (column) renames, table renames and the two
    TextChoices *value* changes that live as string data in existing rows
    (PaymentTransaction.purpose and UsageCharge.charge_source).

    Token-related fields are intentionally left untouched.
    """

    dependencies = [
        ("capp", "0068_alter_guesttokenquota_daily_reset_at"),
    ]

    operations = [
        # ── Model renames ────────────────────────────────────────────────
        migrations.RenameModel(old_name="CoinPackage", new_name="SparkPackage"),
        migrations.RenameModel(old_name="Wallet", new_name="SparkPool"),
        migrations.RenameModel(old_name="WalletTransaction", new_name="SparkTransaction"),
        # ── Table renames ────────────────────────────────────────────────
        migrations.AlterModelTable(name="sparkpackage", table="spark_packages"),
        migrations.AlterModelTable(name="sparkpool", table="spark_pools"),
        migrations.AlterModelTable(name="sparktransaction", table="spark_transactions"),
        # ── Field / column renames ───────────────────────────────────────
        migrations.RenameField("guesttokenquota", "coin_balance", "sparks"),
        migrations.RenameField("subscriptionplan", "included_coins", "included_sparks"),
        migrations.RenameField("sparkpackage", "coin_package_id", "spark_package_id"),
        migrations.RenameField("sparkpackage", "base_coin_amount", "base_spark_amount"),
        migrations.RenameField("usersubscription", "coins_granted", "sparks_granted"),
        migrations.RenameField("usersubscription", "coins_remaining", "sparks_remaining"),
        migrations.RenameField("sparkpool", "wallet_id", "spark_pool_id"),
        migrations.RenameField("sparkpool", "coin_balance", "sparks"),
        migrations.RenameField("sparktransaction", "wallet_transaction_id", "spark_transaction_id"),
        migrations.RenameField("sparktransaction", "wallet", "spark_pool"),
        migrations.RenameField("sparktransaction", "coin_amount", "spark_amount"),
        migrations.RenameField("sparktransaction", "coin_balance_before", "sparks_before"),
        migrations.RenameField("sparktransaction", "coin_balance_after", "sparks_after"),
        migrations.RenameField("paymenttransaction", "wallet", "spark_pool"),
        migrations.RenameField("paymenttransaction", "coins_awarded", "sparks_awarded"),
        migrations.RenameField("usagecharge", "wallet_transaction", "spark_transaction"),
        migrations.RenameField("usagecharge", "coins_charged", "sparks_charged"),
        # ── Meta / related_name updates (no column change) ────────────────
        migrations.AlterModelOptions(
            name="sparkpackage",
            options={"ordering": ["base_spark_amount"]},
        ),
        migrations.AlterField(
            model_name="sparkpool",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="spark_pool",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="sparktransaction",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="spark_transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # ── Index renames / removals / adds ──────────────────────────────
        migrations.RemoveIndex(
            model_name="sparktransaction",
            name="wallet_tran_wallet__aa27da_idx",
        ),
        migrations.RenameIndex(
            model_name="sparktransaction",
            new_name="spark_trans_user_id_1f5e38_idx",
            old_name="wallet_tran_user_id_5cf180_idx",
        ),
        migrations.RenameIndex(
            model_name="sparktransaction",
            new_name="spark_trans_source_1eeb7f_idx",
            old_name="wallet_tran_source_cfbed6_idx",
        ),
        migrations.AddIndex(
            model_name="sparktransaction",
            index=models.Index(fields=["spark_pool", "created_at"], name="spark_trans_spark_p_ed61e2_idx"),
        ),
        # ── TextChoices field definition updates (match new values) ───────
        migrations.AlterField(
            model_name="paymenttransaction",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("subscription", "Subscription"),
                    ("spark_purchase", "Spark Purchase"),
                    ("token_purchase", "Token Purchase"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="usagecharge",
            name="charge_source",
            field=models.CharField(
                choices=[
                    ("subscription", "Subscription"),
                    ("spark_pool", "Spark Pool"),
                    ("free_tier", "Free Tier"),
                    ("purchased_tokens", "Purchased Tokens"),
                ],
                max_length=20,
            ),
        ),
        # ── TextChoices value data updates (string values in rows) ────────
        migrations.RunSQL(
            sql="UPDATE payment_transactions SET purpose = 'spark_purchase' WHERE purpose = 'coin_purchase';",
            reverse_sql="UPDATE payment_transactions SET purpose = 'coin_purchase' WHERE purpose = 'spark_purchase';",
        ),
        migrations.RunSQL(
            sql="UPDATE usage_charges SET charge_source = 'spark_pool' WHERE charge_source = 'wallet';",
            reverse_sql="UPDATE usage_charges SET charge_source = 'wallet' WHERE charge_source = 'spark_pool';",
        ),
    ]
