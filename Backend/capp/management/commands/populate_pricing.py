# capp/management/commands/populate_pricing.py
from django.core.management.base import BaseCommand
from decimal import Decimal
from capp.models import SparkPackage, SubscriptionPlan


class Command(BaseCommand):
    help = (
        "Populate spark packages and subscription plans with default pricing "
        "from the client's specification. Existing spark packages are deleted "
        "first; subscription plans are updated (or created) to preserve "
        "referenced records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    "This will DELETE all existing SparkPackages and UPDATE "
                    "all SubscriptionPlans to match defaults. "
                    "Are you sure? [yes/no] "
                )
            )
            answer = input().strip().lower()
            if answer not in ("yes", "y"):
                self.stdout.write(self.style.NOTICE("Aborted."))
                return

        # ---------- Spark Packages (safe to delete & recreate) ----------
        self.stdout.write("Deleting existing spark packages...")
        SparkPackage.objects.all().delete()

        spark_data = [
            (
                "sparks_1500",
                "1,500 Sparks",
                "A small top-up for casual chats and light usage.",
                1500,
                150,
                Decimal("2.5"),
                146,
            ),
            (
                "sparks_15000",
                "15,000 Sparks",
                "Great for regular users who chat daily.",
                15000,
                1500,
                Decimal("5.0"),
                1425,
            ),
            (
                "sparks_30000",
                "30,000 Sparks",
                "Best value for frequent conversations and image generation.",
                30000,
                3000,
                Decimal("10.0"),
                2700,
            ),
            (
                "sparks_60000",
                "60,000 Sparks",
                "Power plan for creators and heavy users.",
                60000,
                6000,
                Decimal("15.0"),
                5100,
            ),
            (
                "sparks_180000",
                "180,000 Sparks",
                "Maximum savings for dedicated users and long-term subscriptions.",
                180000,
                18000,
                Decimal("20.0"),
                14400,
            ),
        ]

        for slug, name, desc, sparks, base, discount, final in spark_data:
            SparkPackage.objects.create(
                slug=slug,
                name=name,
                description=desc,
                base_spark_amount=sparks,
                base_price_kes=base,
                discount_percent=discount,
                final_price_kes=final,
                is_active=True,
            )
        self.stdout.write(self.style.SUCCESS(f"Created {len(spark_data)} spark packages."))

        # ---------- Subscription Plans (update_or_create to avoid ProtectedError) ----------
        self.stdout.write("Updating subscription plans...")

        plans = [
            # (slug, name, description, price, cycle, sparks, msg_pairs, msg_cap, chars, char_cap, imgs, img_cap)
            # --- DAILY ---
            (
                "light_daily",
                "Light Daily",
                "Light daily plan with hard caps.",
                50,
                "daily",
                500,
                10,
                10,
                0,
                0,
                0,
                0,
            ),
            (
                "base_daily",
                "Base Daily",
                "Base daily plan with hard caps.",
                83,
                "daily",
                830,
                15,
                15,
                1,
                1,
                1,
                1,
            ),
            (
                "standard_daily",
                "Standard Daily",
                "Standard daily plan with ~20% fair-use buffer.",
                167,
                "daily",
                1670,
                38,
                46,
                1,
                1,
                1,
                1,
            ),
            (
                "super_daily",
                "Super Daily",
                "Super daily plan with ~20% fair-use buffer.",
                500,
                "daily",
                5000,
                125,
                150,
                1,
                1,
                2,
                2,
            ),
            # --- WEEKLY ---
            (
                "light_weekly",
                "Light Weekly",
                "Light weekly plan with hard caps.",
                250,
                "weekly",
                2500,
                50,
                50,
                0,
                0,
                0,
                0,
            ),
            (
                "base_weekly",
                "Base Weekly",
                "Base weekly plan with hard caps.",
                625,
                "weekly",
                6250,
                150,
                150,
                1,
                1,
                2,
                2,
            ),
            (
                "standard_weekly",
                "Standard Weekly",
                "Standard weekly plan with ~20% fair-use buffer.",
                1250,
                "weekly",
                12500,
                375,
                450,
                2,
                2,
                5,
                6,
            ),
            (
                "super_weekly",
                "Super Weekly",
                "Super weekly plan with ~20% fair-use buffer.",
                3750,
                "weekly",
                37500,
                1250,
                1500,
                5,
                6,
                15,
                18,
            ),
            # --- MONTHLY ---
            (
                "light_monthly",
                "Light Monthly",
                "Light monthly plan with hard caps.",
                1000,
                "monthly",
                10000,
                200,
                200,
                0,
                0,
                0,
                0,
            ),
            (
                "base_monthly",
                "Base Monthly",
                "Base monthly plan with hard caps.",
                2500,
                "monthly",
                25000,
                600,
                600,
                3,
                3,
                8,
                8,
            ),
            (
                "standard_monthly",
                "Standard Monthly",
                "Standard monthly plan – unlimited chats (fair use).",
                5000,
                "monthly",
                50000,
                None,
                1800,
                7,
                8,
                20,
                24,
            ),
            (
                "super_monthly",
                "Super Monthly",
                "Super monthly plan – unlimited chats, chars & imgs (fair use).",
                15000,
                "monthly",
                150000,
                None,
                6000,
                None,
                24,
                None,
                72,
            ),
            # --- YEARLY ---
            (
                "light_yearly",
                "Light Yearly",
                "Light yearly plan with hard caps.",
                10000,
                "yearly",
                100000,
                2400,
                2400,
                0,
                0,
                0,
                0,
            ),
            (
                "base_yearly",
                "Base Yearly",
                "Base yearly plan with hard caps.",
                25000,
                "yearly",
                250000,
                7200,
                7200,
                36,
                36,
                96,
                96,
            ),
            (
                "standard_yearly",
                "Standard Yearly",
                "Standard yearly plan – unlimited chats (fair use).",
                50000,
                "yearly",
                500000,
                None,
                21600,
                84,
                101,
                240,
                288,
            ),
            (
                "super_yearly",
                "Super Yearly",
                "Super yearly plan – unlimited chats, chars & imgs (fair use).",
                150000,
                "yearly",
                1500000,
                None,
                72000,
                None,
                288,
                None,
                864,
            ),
        ]

        for (
            slug,
            name,
            desc,
            price,
            cycle,
            sparks,
            msg_pairs,
            msg_cap,
            chars,
            char_cap,
            imgs,
            img_cap,
        ) in plans:
            SubscriptionPlan.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": desc,
                    "price": price,
                    "currency": "KES",
                    "billing_cycle": cycle,
                    "included_sparks": sparks,
                    "included_message_pairs": msg_pairs,
                    "fair_use_message_cap": msg_cap,
                    "included_characters": chars,
                    "fair_use_character_cap": char_cap,
                    "included_images": imgs,
                    "fair_use_image_cap": img_cap,
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Updated/created {len(plans)} subscription plans."))
        self.stdout.write(self.style.SUCCESS("Pricing data populated successfully."))
