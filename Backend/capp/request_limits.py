"""Bound actual bytes, not only Content-Length or Django request.body access."""

import io
import tempfile

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.parsers import JSONParser


class RequestBodyTooLarge(APIException):
    status_code = 413
    default_detail = "Request body is too large."
    default_code = "request_too_large"


class BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        limit = settings.JSON_REQUEST_MAX_BYTES
        body = io.BytesIO()
        while True:
            chunk = stream.read(min(16384, limit + 1 - body.tell()))
            if not chunk:
                break
            body.write(chunk)
            if body.tell() > limit:
                raise RequestBodyTooLarge()
        body.seek(0)
        return super().parse(body, media_type, parser_context)


class BoundedHTTPBody:
    """Reject oversize ASGI ingress before Django buffers it in a spool file.

    Non-multipart requests receive the JSON byte budget. Multipart retains the
    existing upload budget, now enforced as a cap rather than memory threshold.
    An edge must additionally enforce body/time/concurrent-connection limits.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", ()))
        multipart = headers.get(b"content-type", b"").lower().startswith(b"multipart/form-data")
        path = scope.get("path", "")
        # A media-type spoof must not give the USER-message endpoint the much
        # larger file-upload budget. Actual file routes keep multipart support.
        message_route = path.startswith("/api/v1/conversations/") and path.endswith("/messages/")
        multipart = multipart and not message_route
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE if multipart else settings.JSON_REQUEST_MAX_BYTES

        async def reject():
            await send(
                {"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": b'{"detail":"Request body is too large."}'})

        length = headers.get(b"content-length")
        if length and (len(length) > 20 or not length.isdigit() or int(length) > limit):
            return await reject()
        # Retain multipart compatibility without holding a 100 MiB upload (or
        # an unbounded list of zero-byte chunks) in Python memory.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    return await reject()
                body.write(chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    chunk = body.read(65536)
                    delivered = body.tell() >= total
                    return {"type": "http.request", "body": chunk, "more_body": not delivered}
                return await receive()

            await self.app(scope, bounded_receive, send)
