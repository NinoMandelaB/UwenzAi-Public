import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eaaicproject.settings")

django_asgi_app = get_asgi_application()

# ✅ Import AFTER Django is fully set up
from capp.routing import websocket_urlpatterns  # noqa: E402 (import after Django setup)
from capp.request_limits import BoundedHTTPBody  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": BoundedHTTPBody(django_asgi_app),
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
