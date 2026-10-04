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
    Non-multipart requests receive the JSON byte budget. Multipart keeps the
    generic upload budget EXCEPT on the chat message send route, which now
    legitimately accepts one photo per message and gets a photo-scoped budget
    (MESSAGE_MULTIPART_MAX_BYTES) instead — a media-type spoof on that route
    still cannot reach the generic 100 MB budget, and the view additionally
    enforces per-file PHOTO_MAX_BYTES and caption limits.
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
        # The chat message send route accepts photo uploads (multipart), but
        # only within a photo-scoped budget — never the generic upload budget.
        message_route = path.startswith("/api/v1/conversations/") and path.endswith("/messages/")
        if multipart and message_route:
            limit = settings.MESSAGE_MULTIPART_MAX_BYTES
            # Same contract as the view-level photo_too_large refusal: carry
            # the allowed size so the client can show a precise message.
            import json as _json

            max_bytes = int(getattr(settings, "PHOTO_MAX_BYTES", 10 * 1024 * 1024))
            reject_body = _json.dumps(
                {
                    "status": "fail",
                    "data": "photo_too_large",
                    "details": {"max_bytes": max_bytes, "max_mb": round(max_bytes / (1024 * 1024))},
                }
            ).encode("utf-8")
        elif multipart:
            limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
            reject_body = b'{"detail":"Request body is too large."}'
        else:
            limit = settings.JSON_REQUEST_MAX_BYTES
            reject_body = b'{"detail":"Request body is too large."}'

        async def reject():
            await send(
                {"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": reject_body})

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
