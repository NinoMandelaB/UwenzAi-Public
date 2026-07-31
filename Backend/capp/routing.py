from django.urls import re_path

from capp.consumers import PingPongConsumer, ChatConsumer, SparkPoolConsumer

websocket_urlpatterns = [
    re_path("ws/ping-pong/", PingPongConsumer.as_asgi()),
    re_path(r"ws/chat/(?P<conversation_id>[0-9a-f-]+)/$", ChatConsumer.as_asgi()),
    re_path(r"ws/spark_pool/$", SparkPoolConsumer.as_asgi()),
]
