from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin


from capp.models import CustomUser
from capp.models import UserBYOKConfig
from capp.models import AgentAPIKey, AgentUsageLog, AgentModelPrice


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    model = CustomUser

    list_display = (
        "username",
        "full_name",
        "auth_provider",
        "account_status",
        "is_verified",
        "age_verified",
        "is_staff",
        "is_active",
        "is_deleted",
        "created_at",
    )

    list_filter = (
        "auth_provider",
        "account_status",
        "is_verified",
        "age_verified",
        "is_staff",
        "is_active",
        "is_deleted",
        "preferred_language",
        "created_at",
    )

    search_fields = (
        "username",
        "full_name",
        "display_name",
        "email_hash",
        "phone_number_hash",
        "google_sub",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "user_id",
        "created_at",
        "updated_at",
        "deleted_at",
        "age_verified_at",
        "email_hash",
        "phone_number_hash",
        "kdf_salt",
        "encrypted_dek",
        "recovery_encrypted_dek",
        "recovery_verifier",
    )

    fieldsets = (
        (
            "Authentication",
            {
                "fields": (
                    "username",
                    "password",
                    "auth_provider",
                    "google_sub",
                )
            },
        ),
        (
            "Profile",
            {
                "fields": (
                    "user_id",
                    "full_name",
                    "display_name",
                    "avatar",
                    "preferred_language",
                )
            },
        ),
        (
            "Encrypted Contact Data",
            {
                "fields": (
                    "email_encrypted",
                    "email_hash",
                    "phone_number_encrypted",
                    "phone_number_hash",
                )
            },
        ),
        (
            "Account Status",
            {
                "fields": (
                    "account_status",
                    "ban_reason",
                    "is_verified",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "is_deleted",
                    "deleted_at",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Age Verification",
            {
                "fields": (
                    "age_verified",
                    "age_verified_at",
                    "age_verification_method",
                    "compliance_version",
                )
            },
        ),
        (
            "Quota Tracking",
            {
                "fields": (
                    "daily_token_quota",
                    "total_token_quota",
                    "daily_tokens_used",
                    "total_tokens_used",
                    "daily_quota_reset_at",
                )
            },
        ),
        (
            "Encryption / Recovery",
            {
                "fields": (
                    "kdf_salt",
                    "encrypted_dek",
                    "recovery_encrypted_dek",
                    "recovery_verifier",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "last_login",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "full_name",
                    "display_name",
                    "auth_provider",
                    "preferred_language",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_active",
                ),
            },
        ),
    )


@admin.register(UserBYOKConfig)
class UserBYOKConfigAdmin(admin.ModelAdmin):
    """
    Admin view for BYOK configurations.
    The API key is NEVER shown — only a hint (last 4 chars).
    System prompts are shown in full for moderation purposes.
    """

    list_display = (
        "get_username",
        "api_url",
        "model_name",
        "get_api_key_hint",
        "store_on_platform",
        "is_active",
        "created_at",
        "updated_at",
    )

    list_filter = (
        "is_active",
        "store_on_platform",
        "created_at",
    )

    search_fields = (
        "user__username",
        "user__full_name",
        "api_url",
        "model_name",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "user",
        "get_api_key_hint",
        "created_at",
        "updated_at",
    )

    # Never expose api_key_encrypted in fieldsets
    fieldsets = (
        (
            "User",
            {
                "fields": ("user",),
            },
        ),
        (
            "API Connection",
            {
                "fields": (
                    "api_url",
                    "model_name",
                    "get_api_key_hint",
                ),
            },
        ),
        (
            "System Prompt (moderation)",
            {
                "fields": ("system_prompt",),
                "description": (
                    "Stored in clear text for moderation. "
                    "Review for ToS compliance. Admins may clear this field if it violates policy."
                ),
            },
        ),
        (
            "Settings",
            {
                "fields": ("store_on_platform", "is_active"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    # Prevent creating BYOK configs via admin (only via user Settings UI)
    def has_add_permission(self, request):
        return False

    @admin.display(description="Username")
    def get_username(self, obj):
        return obj.user.username if obj.user else "-"

    @admin.display(description="API Key (hint)")
    def get_api_key_hint(self, obj):
        try:
            raw = obj.get_decrypted_api_key()
            if not raw:
                return "(empty)"
            return "••••••••" + raw[-4:]
        except Exception:
            return "(decryption error)"


# ---------------------------------------------------------------------------
# Agent Gateway admin registrations
# ---------------------------------------------------------------------------


@admin.register(AgentAPIKey)
class AgentAPIKeyAdmin(admin.ModelAdmin):
    """Read-only admin for Agent API keys — except the monthly Spark budget.

    Everything stays read-only (keys are created only via the developer API,
    so no key material can ever originate here). The one editable field is
    monthly_spark_budget: support can cap a suspected-leaked key without
    waiting for the owner. Clearing the field restores an unlimited budget.
    """

    list_display = (
        "label",
        "key_hint",
        "user",
        "is_active",
        "monthly_spark_budget",
        "created_at",
        "last_used_at",
        "expires_at",
    )
    list_filter = ("is_active",)
    search_fields = ("label", "key_hint", "user__username")
    readonly_fields = (
        "key_hash",
        "key_hint",
        "user",
        "label",
        "is_active",
        "created_at",
        "last_used_at",
        "expires_at",
    )
    fields = (
        "key_hash",
        "key_hint",
        "user",
        "label",
        "is_active",
        "monthly_spark_budget",
        "created_at",
        "last_used_at",
        "expires_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(AgentUsageLog)
class AgentUsageLogAdmin(admin.ModelAdmin):
    """Read-only admin for Agent Gateway usage logs."""

    list_display = (
        "created_at",
        "user",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "sparks_charged",
        "status",
        "latency_ms",
    )
    list_filter = ("status", "provider")
    search_fields = ("model", "user__username", "key_hint")
    readonly_fields = (
        "created_at",
        "user",
        "key",
        "key_hint",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cost_native",
        "cost_currency",
        "fx_rate",
        "sparks_charged",
        "status",
        "latency_ms",
        "client_operation_id",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AgentModelPrice)
class AgentModelPriceAdmin(admin.ModelAdmin):
    """Admin for the cost catalog. Prices editable without deploy."""

    list_display = (
        "provider",
        "model",
        "currency",
        "input_price_per_1m",
        "output_price_per_1m",
        "is_active",
        "updated_at",
    )
    list_filter = ("provider", "currency", "is_active")
    search_fields = ("provider", "model")
    ordering = ("provider", "model")
