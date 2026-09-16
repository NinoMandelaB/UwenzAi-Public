import os

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include
from django.contrib import admin

# When SERVE_AGENT_ONLY=true, this process serves only the Agent Gateway routes.
# This lets you run a separate Railway service (same code, same DB) that only
# handles agent inference requests with its own worker pool — so heavy agent
# traffic never blocks the main app's WebSocket/HTTP workers.
SERVE_AGENT_ONLY = os.getenv("SERVE_AGENT_ONLY", "false").lower() in ("true", "1", "yes")

if SERVE_AGENT_ONLY:
    # Dedicated service: canonical inference base plus unchanged legacy aliases.
    # Do not mount management, account, or admin endpoints at the new public base.
    urlpatterns = [
        path("api/v1/", include("capp.urls.agent_public_urls")),
        path("api/v1/agent/", include("capp.urls.agent_gateway_urls")),
    ]
else:
    # Main app: full URL config (includes agent routes too)
    urlpatterns = [
        path("admin/", admin.site.urls),
        path("api/v1/auth/", include("capp.urls.auth_urls")),
        path("api/v1/characters/", include("capp.urls.character_urls")),
        path("api/v1/personalities/", include("capp.urls.personality_urls")),
        path("api/v1/occupations/", include("capp.urls.occupation_urls")),
        path("api/v1/hobbies/", include("capp.urls.hobby_urls")),
        path("api/v1/feedback/", include("capp.urls.feedback_urls")),
        path("api/v1/guest/", include("capp.urls.guest_urls")),
        path("api/v1/conversations/", include("capp.urls.conversation_urls")),
        path("api/v1/tokens/", include("capp.urls.token_urls")),
        path("api/v1/paystack/", include("paystack_app.urls")),
        path("api/v1/", include("capp.urls.package_urls")),
        path("api/v1/", include("capp.urls.billing_urls")),
        path("api/v1/", include("capp.urls.moderation_urls")),
        path("api/v1/spark_pool/", include("capp.urls.spark_pool_urls")),
        path("api/v1/support/", include("capp.urls.support_urls")),
        path("api/v1/admin/", include("capp.urls.admin_urls")),
        path("api/v1/proactive-chat/", include("capp.urls.proactive_chat_urls")),
        path("api/v1/push/", include("capp.urls.push_urls")),
        path("api/v1/memory/", include("capp.urls.memory_urls")),
        path("api/v1/reminders/", include("capp.urls.reminder_urls")),
        path("api/v1/agent/", include("capp.urls.agent_gateway_urls")),
    ]

# Django must never serve MEDIA_ROOT itself outside local development: it is an
# unauthenticated read of the whole media tree and bypasses the private bucket's
# signed URLs. ``static()`` returns an empty list when DEBUG is False, so this
# route simply does not exist in production.
# Only mount media routes for the main app (agent gateway doesn't need them).
if not SERVE_AGENT_ONLY:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
