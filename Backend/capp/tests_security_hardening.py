"""Offline security regressions: real framework/SDK/transport, fake external I/O."""

import hashlib
import importlib
import io
import json
import os
import socket
import ssl
import time
import uuid
from types import SimpleNamespace
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from cryptography.fernet import Fernet
from datetime import timedelta
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db import close_old_connections, connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from capp.authentication import AccountJWTAuthentication
from capp.classes.auth.auth import Auth
from capp.classes.auth.codes import code_matches
from capp.classes.auth.email_verification_service import EmailVerificationService
from capp.classes.byok.byok_service import BYOKService
from capp.classes.byok.transport import (
    PinnedNetworkBackend,
    ProviderPolicy,
    ProviderURLRejected,
    provider_http_client,
)
from capp.fields import EncryptedFieldError, EncryptedTextField, UnavailableEncryptedValue
from capp.models import CustomUser, EmailChangeRequest, EmailVerification, PasswordRecovery
from capp.request_limits import BoundedHTTPBody, BoundedJSONParser, RequestBodyTooLarge
from capp.tests import _make_character, _make_user


class CookieBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.access = str(AccessToken.for_user(self.user))
        self.client = Client(enforce_csrf_checks=True)

    def test_arbitrary_or_invalid_authorization_never_bypasses_cookie_csrf(self):
        for header in ("Basic abc", "garbage", "Bearer invalid", "Bearer a b", ""):
            with self.subTest(header=header):
                response = self.client.post(
                    "/api/v1/auth/signout/", {}, content_type="application/json", HTTP_AUTHORIZATION=header
                )
                self.assertEqual(response.status_code, 403)

    def test_mixed_endpoint_accepts_real_active_access_jwt_without_csrf(self):
        response = self.client.post(
            "/api/v1/auth/signout/",
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertNotEqual(response.status_code, 403)

    def _expired_access(self):
        token = AccessToken.for_user(self.user)
        token.set_exp(from_time=timezone.now() - timedelta(hours=1), lifetime=timedelta(minutes=1))
        return str(token)

    def test_signout_with_expired_bearer_and_csrf_still_blacklists_refresh_cookie(self):
        """Idle > 15 min on a shared device: logout must still revoke the cookie."""
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

        csrf = self.client.get("/api/v1/auth/csrf/").json()["data"]["csrfToken"]
        refresh = RefreshToken.for_user(self.user)
        self.client.cookies[settings.JWT_REFRESH_COOKIE_NAME] = str(refresh)
        response = self.client.post(
            "/api/v1/auth/signout/",
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self._expired_access()}",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertEqual(response.cookies[settings.JWT_REFRESH_COOKIE_NAME].value, "")

    def test_signout_with_expired_bearer_without_csrf_is_rejected(self):
        self.client.cookies[settings.JWT_REFRESH_COOKIE_NAME] = str(RefreshToken.for_user(self.user))
        response = self.client.post(
            "/api/v1/auth/signout/",
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self._expired_access()}",
        )
        self.assertEqual(response.status_code, 403)

    def test_expired_bearer_is_still_rejected_on_authenticated_endpoints(self):
        response = self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {self._expired_access()}")
        self.assertEqual(response.status_code, 401)

    def test_refresh_always_requires_csrf_even_with_valid_access_jwt(self):
        with mock.patch.object(Auth, "refresh_access_token") as refresh:
            response = self.client.post(
                "/api/v1/auth/refresh/",
                {},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.access}",
            )
            self.assertEqual(response.status_code, 403)
            refresh.assert_not_called()

    def test_refresh_succeeds_with_valid_csrf_cookie_and_token(self):
        csrf = self.client.get("/api/v1/auth/csrf/").json()["data"]["csrfToken"]
        self.client.cookies[settings.JWT_REFRESH_COOKIE_NAME] = str(RefreshToken.for_user(self.user))
        response = self.client.post(
            "/api/v1/auth/refresh/",
            {},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json()["data"])

    def test_status_only_ban_is_not_bearer_authentication(self):
        self.user.account_status = "banned"
        self.user.save(update_fields=["account_status"])
        response = self.client.post(
            "/api/v1/auth/signout/",
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, 403)

    def test_refresh_rejects_inactive_deleted_suspended_and_status_only_banned(self):
        refresh = str(RefreshToken.for_user(self.user))
        for field, value in (
            ("is_active", False),
            ("is_deleted", True),
            ("account_status", "suspended"),
            ("account_status", "banned"),
        ):
            with self.subTest(field=field, value=value):
                CustomUser.objects.filter(pk=self.user.pk).update(
                    is_active=True, is_deleted=False, account_status="active"
                )
                CustomUser.objects.filter(pk=self.user.pk).update(**{field: value})
                self.assertEqual(Auth.refresh_access_token(refresh)["data"], "account_inactive")

    def test_http_jwt_rejects_status_only_suspension(self):
        self.user.account_status = "suspended"
        self.user.save(update_fields=["account_status"])
        from rest_framework.exceptions import AuthenticationFailed

        with self.assertRaises(AuthenticationFailed):
            AccountJWTAuthentication().get_user(AccessToken(self.access))


class UserMessageBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.for_user(self.user)}"}

    def test_malformed_and_oversize_content_stops_before_language_db_and_queue(self):
        from capp.views import conversation_views as views

        for payload in (
            [],
            ["text"],
            {"content": None},
            {"content": []},
            {"content": {}},
            {"content": 17},
            {"content": True},
            {"content": "x" * 801},
            {"content": " " * 801},
            {"content": "\U0001f600" * 801},
        ):
            with self.subTest(payload_type=type(payload).__name__), mock.patch.object(
                views.LanguageDetector, "detect"
            ) as detect, mock.patch.object(views.Message.objects, "create") as create, mock.patch.object(
                views.process_ai_response, "delay"
            ) as queue, mock.patch.object(
                views, "get_object_or_404"
            ) as lookup:
                cache.clear()
                response = self.client.post(
                    f"/api/v1/conversations/{uuid.uuid4()}/messages/",
                    payload,
                    content_type="application/json",
                    **self.header,
                )
                self.assertEqual(response.status_code, 400)
                detect.assert_not_called()
                create.assert_not_called()
                lookup.assert_not_called()
                queue.assert_not_called()

    def test_800_unicode_characters_are_accepted_and_ai_rows_are_not_truncated(self):
        from capp.models import Conversation, Message

        character = _make_character()
        conversation = Conversation.objects.create(user=self.user, character=character)
        ai = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.AI,
            content="a" * 4000,
        )
        with mock.patch("capp.views.conversation_views.LanguageDetector.detect", return_value="en"), mock.patch(
            "capp.views.conversation_views.process_ai_response.delay"
        ) as queue:
            response = self.client.post(
                f"/api/v1/conversations/{conversation.pk}/messages/",
                {"content": "\U0001f600" * 800},
                content_type="application/json",
                **self.header,
            )
        self.assertEqual(response.status_code, 201)
        queue.assert_called_once()
        ai.refresh_from_db()
        self.assertEqual(len(ai.content), 4000)


class BodyLimitTests(SimpleTestCase):
    def test_production_filters_even_explicit_loopback_origins_but_debug_keeps_them(self):
        from eaaicproject import settings as project_settings

        origins = [
            "https://app.example.com",
            "http://localhost:5173",
            "https://127.0.0.1:9999",
            "http://[::1]:3000",
            "https://sub.localhost",
        ]
        with mock.patch.object(project_settings, "DEBUG", False):
            self.assertEqual(project_settings.browser_origins_for_environment(origins), origins[:1])
        with mock.patch.object(project_settings, "DEBUG", True), mock.patch.object(
            project_settings, "IS_PRODUCTION", False
        ):
            self.assertEqual(project_settings.browser_origins_for_environment(origins), origins)
        with mock.patch.object(project_settings, "DEBUG", True), mock.patch.object(
            project_settings, "IS_PRODUCTION", True
        ):
            self.assertEqual(project_settings.browser_origins_for_environment(origins), origins[:1])

    def test_json_parser_counts_actual_bytes_even_without_content_length(self):
        with override_settings(JSON_REQUEST_MAX_BYTES=64):
            with self.assertRaises(RequestBodyTooLarge):
                BoundedJSONParser().parse(io.BytesIO(json.dumps({"text": "x" * 1000}).encode()))

    def test_json_parser_handles_short_reads_without_accepting_a_valid_prefix(self):
        class ShortStream(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(3, size))

        with override_settings(JSON_REQUEST_MAX_BYTES=64):
            with self.assertRaises(RequestBodyTooLarge):
                BoundedJSONParser().parse(ShortStream(b"{}" + b" " * 100))

    def test_summary_12000_escaped_astral_characters_fit(self):
        body = json.dumps({"summary_text": "\U0001f600" * 12000}).encode()
        self.assertLess(len(body), settings.JSON_REQUEST_MAX_BYTES)
        self.assertEqual(len(BoundedJSONParser().parse(io.BytesIO(body))["summary_text"]), 12000)

    async def test_ingress_caps_chunked_bytes_before_django_even_with_false_length(self):
        for headers in ([], [(b"content-length", b"1")]):
            with self.subTest(headers=headers), override_settings(JSON_REQUEST_MAX_BYTES=64):
                app = mock.AsyncMock()
                receive = mock.AsyncMock(
                    side_effect=[
                        {"type": "http.request", "body": b"a" * 50, "more_body": True},
                        {"type": "http.request", "body": b"b" * 50, "more_body": False},
                    ]
                )
                send = mock.AsyncMock()
                await BoundedHTTPBody(app)({"type": "http", "headers": headers}, receive, send)
                app.assert_not_called()
                self.assertEqual(send.call_args_list[0].args[0]["status"], 413)

    async def test_ingress_replays_exact_body_and_preserves_disconnect(self):
        events = []

        async def app(scope, receive, send):
            events.append(await receive())
            events.append(await receive())

        receive = mock.AsyncMock(
            side_effect=[
                {"type": "http.request", "body": b'{"ok":', "more_body": True},
                {"type": "http.request", "body": b"true}", "more_body": False},
                {"type": "http.disconnect"},
            ]
        )
        await BoundedHTTPBody(app)({"type": "http", "headers": []}, receive, mock.AsyncMock())
        self.assertEqual(events[0]["body"], b'{"ok":true}')
        self.assertEqual(events[1]["type"], "http.disconnect")

    async def test_declared_overflow_rejected_without_read_and_multipart_counted(self):
        receive, send, app = mock.AsyncMock(), mock.AsyncMock(), mock.AsyncMock()
        await BoundedHTTPBody(app)(
            {"type": "http", "headers": [(b"content-length", str(settings.JSON_REQUEST_MAX_BYTES + 1).encode())]},
            receive,
            send,
        )
        receive.assert_not_called()
        app.assert_not_called()
        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=100):
            receive = mock.AsyncMock(return_value={"type": "http.request", "body": b"x" * 101})
            await BoundedHTTPBody(app)(
                {"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=test")]},
                receive,
                send,
            )
        app.assert_not_called()
        self.assertEqual(send.call_args_list[-2].args[0]["status"], 413)

    async def test_message_multipart_budget_and_oversize_rejection(self):
        """The message send route accepts multipart (photos) in a photo-scoped
        budget — never the generic 100 MB upload budget, never the JSON budget.
        Oversize is rejected with the app envelope so the frontend can show
        the localized photo_too_large error."""
        path = f"/api/v1/conversations/{uuid.uuid4()}/messages/"
        send, app = mock.AsyncMock(), mock.AsyncMock()

        # In-budget multipart passes through to the app.
        receive = mock.AsyncMock(return_value={"type": "http.request", "body": b"x" * 101, "more_body": False})
        await BoundedHTTPBody(app)(
            {
                "type": "http",
                "path": path,
                "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
            },
            receive,
            send,
        )
        app.assert_called_once()

        # Over-budget multipart is rejected with 413 + photo_too_large envelope.
        app.reset_mock()
        with override_settings(MESSAGE_MULTIPART_MAX_BYTES=100):
            receive = mock.AsyncMock(
                side_effect=[
                    {"type": "http.request", "body": b"x" * 60, "more_body": True},
                    {"type": "http.request", "body": b"x" * 50, "more_body": False},
                ]
            )
            await BoundedHTTPBody(app)(
                {
                    "type": "http",
                    "path": path,
                    "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
                },
                receive,
                send,
            )
            app.assert_not_called()
            self.assertEqual(send.call_args_list[-2].args[0]["status"], 413)
            body = json.loads(send.call_args_list[-1].args[0]["body"])
            # The envelope carries the allowed size so the client can show a
            # precise "maximum X MB" message.
            self.assertEqual(body["data"], "photo_too_large")
            self.assertIn("max_mb", body["details"])
            self.assertIn("max_bytes", body["details"])

    async def test_message_route_json_still_json_bounded_and_length_headers_rejected(self):
        # Non-multipart on the message route keeps the (tiny) JSON budget.
        with override_settings(JSON_REQUEST_MAX_BYTES=100):
            receive = mock.AsyncMock(return_value={"type": "http.request", "body": b"x" * 101})
            send, app = mock.AsyncMock(), mock.AsyncMock()
            await BoundedHTTPBody(app)(
                {
                    "type": "http",
                    "path": f"/api/v1/conversations/{uuid.uuid4()}/messages/",
                    "headers": [(b"content-type", b"application/json")],
                },
                receive,
                send,
            )
            app.assert_not_called()
            self.assertEqual(send.call_args_list[-2].args[0]["status"], 413)

        # Absurd Content-Length headers are rejected without reading the body.
        receive, send, app = mock.AsyncMock(), mock.AsyncMock(), mock.AsyncMock()
        await BoundedHTTPBody(app)(
            {
                "type": "http",
                "path": f"/api/v1/conversations/{uuid.uuid4()}/messages/",
                "headers": [(b"content-length", b"1" * 5000)],
            },
            receive,
            send,
        )
        receive.assert_not_called()
        app.assert_not_called()


def dns_answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))


class FakeProviderSocket:
    """Only the last OS boundary is fake; actual httpcore/httpx/OpenAI execute."""

    def __init__(self, response=None):
        body = (
            b'{"id":"offline","choices":[{"message":{"content":"ok"}}],'
            b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
        )
        self.response = (
            response
            or b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        self.sent = b""
        self.destination = None
        self.closed = False

    def connect(self, address):
        self.destination = address

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *args):
        pass

    def send(self, data):
        self.sent += data
        return len(data)

    def recv(self, size):
        data, self.response = self.response[:size], self.response[size:]
        return data

    def close(self):
        self.closed = True

    def fileno(self):
        return -1


@override_settings(BYOK_PRIVATE_ORIGINS=[])
class ProviderTransportTests(SimpleTestCase):
    def test_uwenzai_gateway_detection_is_origin_exact(self):
        from capp.classes.byok.transport import is_uwenzai_gateway_url

        # Canonical production origin matches regardless of path or port style.
        self.assertTrue(is_uwenzai_gateway_url("https://agent-api.uwenzai.com/api/v1"))
        self.assertTrue(is_uwenzai_gateway_url("https://agent-api.uwenzai.com/api/v1/"))
        # Lookalikes must NOT match.
        self.assertFalse(is_uwenzai_gateway_url("https://agent-api.uwenzai.com.evil.example/v1"))
        self.assertFalse(is_uwenzai_gateway_url("https://api.cortecs.ai/v1"))
        self.assertFalse(is_uwenzai_gateway_url("https://agent-api.uwenzai.com:8443/v1"))
        self.assertFalse(is_uwenzai_gateway_url("http://agent-api.uwenzai.com/v1"))
        self.assertFalse(is_uwenzai_gateway_url(None))

        # The advertised AGENT_BASE_URL and extra origins count as the gateway.
        with override_settings(AGENT_BASE_URL="https://agent-dev.example.test/api/v1"):
            self.assertTrue(is_uwenzai_gateway_url("https://agent-dev.example.test/api/v1"))
        with override_settings(BYOK_EXTRA_GATEWAY_ORIGINS=["https://gw2.example.test"]):
            self.assertTrue(is_uwenzai_gateway_url("https://gw2.example.test/any/path"))
            self.assertFalse(is_uwenzai_gateway_url("https://other.example.test/v1"))

    def test_default_policy_rejects_local_http_and_malformed_urls(self):
        for url in (
            "http://example.com/v1",
            "ftp://example.com/v1",
            "https://user:pw@example.com",
            "https://example.com?x=1",
            "https://example.com/#fragment",
            "https://host%2ecom",
            "https://example.com\\@localhost",
            "https://example.com:0",
            "https://example.com.",
            "https://[fe80::1%25eth0]/v1",
            "https://example.com/\n",
            None,
            {},
        ):
            with self.subTest(url=url), self.assertRaises(ProviderURLRejected):
                ProviderPolicy.for_url(url)

    def test_all_dns_answers_must_be_public_and_no_socket_opens(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "64:ff9b::a00:1",
            "64:ff9b:1::1",
            "2001:1::1",
            "3fff::1",
            "2002:7f00:1::",
            "192.0.0.8",
            "192.88.99.1",
            "168.63.129.16",
            "0.0.0.0",  # nosec B104: rejection fixture, not a bind
        ):
            with self.subTest(address=address), mock.patch(
                "socket.getaddrinfo", return_value=[dns_answer("8.8.8.8"), dns_answer(address)]
            ), mock.patch("socket.socket") as create:
                backend = PinnedNetworkBackend(ProviderPolicy.for_url("https://model.example/v1"))
                with self.assertRaises(ProviderURLRejected):
                    backend.connect_tcp("model.example", 443, timeout=1)
                create.assert_not_called()

    def test_explicit_local_origins_support_private_overlay_and_ula_networks(self):
        for host in ("100.64.0.8", "10.0.0.8", "fd12::8"):
            url = f"http://[{host}]:8000" if ":" in host else f"http://{host}:8000"
            with self.subTest(host=host), override_settings(BYOK_PRIVATE_ORIGINS=[url]):
                self.assertEqual(str(ProviderPolicy.for_url(url).addresses(host, 8000)[0]), host)

    def test_platform_addresses_are_blocked_even_with_operator_origin_permission(self):
        for host in ("168.63.129.16", "100.100.100.200", "fd00:ec2::254", "fd20:ce::254"):
            url = f"http://[{host}]:8000" if ":" in host else f"http://{host}:8000"
            with self.subTest(host=host), override_settings(BYOK_PRIVATE_ORIGINS=[url]):
                with self.assertRaises(ProviderURLRejected):
                    ProviderPolicy.for_url(url).addresses(host, 8000)

    def test_actual_sdk_uses_pinned_public_ip_keeps_path_host_and_verified_tls_sni(self):
        sock = FakeProviderSocket()
        tls = []

        def start_tls(stream, ssl_context, server_hostname=None, timeout=None):
            tls.append((server_hostname, ssl_context.check_hostname, ssl_context.verify_mode))
            return stream

        with mock.patch.dict(
            os.environ, {"HTTPS_PROXY": "http://127.0.0.1:8888", "ALL_PROXY": "http://127.0.0.1:8888"}
        ), mock.patch(
            "socket.getaddrinfo", side_effect=[[dns_answer("8.8.8.8")], [dns_answer("127.0.0.1")]]
        ) as dns, mock.patch(
            "socket.socket", return_value=sock
        ), mock.patch(
            "httpcore._backends.sync.SyncStream.start_tls", new=start_tls
        ):
            result = BYOKService.validate_credentials("https://model.example/proxy/tenant/v1", "offline-key", "model")
        self.assertEqual(result["status"], "success")
        self.assertEqual(dns.call_count, 1)  # second, hostile DNS answer is never read
        self.assertEqual(sock.destination, ("8.8.8.8", 443))
        self.assertIn(b"POST /proxy/tenant/v1/chat/completions HTTP/1.1", sock.sent)
        self.assertIn(b"Host: model.example", sock.sent)
        self.assertIn(b"Authorization: Bearer offline-key", sock.sent)
        self.assertEqual(tls, [("model.example", True, ssl.CERT_REQUIRED)])
        self.assertTrue(sock.closed)

    def test_public_literal_needs_no_dns(self):
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("No DNS for literal")), mock.patch(
            "socket.socket", return_value=FakeProviderSocket()
        ) as create:
            backend = PinnedNetworkBackend(ProviderPolicy.for_url("https://8.8.8.8/v1"))
            stream = backend.connect_tcp("8.8.8.8", 443, timeout=1)
            self.assertEqual(create.return_value.destination, ("8.8.8.8", 443))
            stream.close()

    @override_settings(BYOK_PRIVATE_ORIGINS=["http://ollama.internal:11434", "http://127.0.0.1:8000"])
    def test_explicit_operator_local_ollama_and_vllm_origin_only(self):
        for url, host, port, ip in (
            ("http://ollama.internal:11434/v1", "ollama.internal", 11434, "10.1.2.3"),
            ("http://127.0.0.1:8000/v1", "127.0.0.1", 8000, "127.0.0.1"),
        ):
            with self.subTest(url=url), mock.patch("socket.getaddrinfo", return_value=[dns_answer(ip)]), mock.patch(
                "socket.socket", return_value=FakeProviderSocket()
            ) as sock:
                with provider_http_client(url) as client:
                    self.assertEqual(client.post(url + "/chat/completions", json={}).status_code, 200)
                self.assertEqual(sock.return_value.destination, (ip, port))
        for url in ("http://ollama.internal:80/v1", "http://10.1.2.3:11434/v1", "http://evil.internal:11434/v1"):
            with self.assertRaises(ProviderURLRejected):
                ProviderPolicy.for_url(url)

    def test_redirects_never_send_credentials_to_another_origin(self):
        redirect = (
            b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://169.254.169.254/latest\r\nContent-Length: 0\r\n\r\n"
        )
        with mock.patch("socket.getaddrinfo", return_value=[dns_answer("8.8.8.8")]) as dns, mock.patch(
            "socket.socket", return_value=FakeProviderSocket(redirect)
        ) as sock, mock.patch("httpcore._backends.sync.SyncStream.start_tls", new=lambda self, **kwargs: self):
            result = BYOKService.validate_credentials("https://model.example/v1", "offline-key", "model")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(sock.call_count, 1)

    def test_rebinding_is_rechecked_on_every_new_connection(self):
        with mock.patch(
            "socket.getaddrinfo", side_effect=[[dns_answer("8.8.8.8")], [dns_answer("10.0.0.1")]]
        ), mock.patch("socket.socket", return_value=FakeProviderSocket()) as sock, mock.patch(
            "httpcore._backends.sync.SyncStream.start_tls", new=lambda self, **kwargs: self
        ):
            with provider_http_client("https://model.example/v1") as client:
                client.post("https://model.example/v1/chat/completions", json={})
                with self.assertRaises(ProviderURLRejected):
                    client.post("https://model.example/v1/chat/completions", json={})
        self.assertEqual(sock.call_count, 1)

    def test_transport_rejects_origin_override_and_unix_socket(self):
        with provider_http_client("https://model.example/v1") as client:
            with self.assertRaises(ProviderURLRejected):
                client.get("https://8.8.8.8/v1")
        with self.assertRaises(ProviderURLRejected):
            PinnedNetworkBackend(ProviderPolicy.for_url("https://model.example")).connect_unix_socket("x")

    def test_failure_logger_does_not_crash_or_echo_keys(self):
        with mock.patch(
            "capp.classes.byok.byok_service.OpenAI", side_effect=RuntimeError("offline-key")
        ), self.assertLogs("capp.classes.byok.byok_service", level="WARNING") as logs:
            result = BYOKService.validate_credentials("https://model.example/v1", "offline-key", "model")
        self.assertEqual(result["status"], "fail")
        self.assertNotIn("offline-key", "".join(logs.output))

    def test_gateway_insufficient_sparks_maps_to_clear_reason(self):
        # UwenzAi's own Agent Gateway rejects a spend beyond the remaining
        # Sparks balance with 402 insufficient_sparks; that must surface as a
        # dedicated reason, not a generic validation failure.
        with mock.patch(
            "capp.classes.byok.byok_service.OpenAI",
            side_effect=RuntimeError(
                "Error code: 402 - {'error': {'status': 'insufficient_sparks', "
                "'message': 'Insufficient sparks for this request'}}"
            ),
        ):
            result = BYOKService.validate_credentials("https://model.example/v1", "offline-key", "model")
        self.assertEqual(result, {"status": "fail", "data": "insufficient_sparks"})

    def test_chat_completion_uses_same_guarded_sdk_path_as_validation(self):
        config = SimpleNamespace(
            api_url="https://model.example/v1", model_name="model", get_decrypted_api_key=lambda: "offline-key"
        )
        with mock.patch("socket.getaddrinfo", return_value=[dns_answer("8.8.8.8")]), mock.patch(
            "socket.socket", return_value=FakeProviderSocket()
        ) as sock, mock.patch("httpcore._backends.sync.SyncStream.start_tls", new=lambda self, **kwargs: self):
            result = BYOKService.chat_completion(config, [{"role": "user", "content": "hello"}])
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(sock.return_value.destination, ("8.8.8.8", 443))
        self.assertTrue(sock.return_value.closed)

    def test_public_ipv6_is_supported(self):
        address = "2606:4700:4700::1111"
        with mock.patch("socket.getaddrinfo", return_value=[dns_answer(address)]), mock.patch(
            "socket.socket", return_value=FakeProviderSocket()
        ) as sock:
            stream = PinnedNetworkBackend(ProviderPolicy.for_url("https://model.example")).connect_tcp(
                "model.example", 443, timeout=1
            )
            self.assertEqual(sock.return_value.destination, (address, 443))
            self.assertEqual(sock.call_args.args[0], socket.AF_INET6)
            stream.close()

    def test_resolver_empty_answers_and_socket_timeout_fail_closed(self):
        import httpcore

        backend = PinnedNetworkBackend(ProviderPolicy.for_url("https://model.example"))
        with mock.patch("socket.getaddrinfo", return_value=[]), self.assertRaises(ProviderURLRejected):
            backend.connect_tcp("model.example", 443, timeout=1)
        for error, expected in ((socket.timeout(), httpcore.ConnectTimeout), (OSError(), httpcore.ConnectError)):
            sock = FakeProviderSocket()
            sock.connect = mock.Mock(side_effect=error)
            with mock.patch("socket.getaddrinfo", return_value=[dns_answer("8.8.8.8")]), mock.patch(
                "socket.socket", return_value=sock
            ), self.assertRaises(expected):
                backend.connect_tcp("model.example", 443, timeout=1, socket_options=[(1, 2, 3)])
            self.assertTrue(sock.closed)

    def test_invalid_operator_allowlist_never_becomes_wildcard_access(self):
        for item in ("http://*.internal:11434", "http://ollama.internal:11434/v1", "http://user@host"):
            with self.subTest(item=item), override_settings(BYOK_PRIVATE_ORIGINS=[item]):
                with self.assertRaises(ProviderURLRejected):
                    ProviderPolicy.for_url("https://model.example")

    def test_operator_allowlist_still_cannot_route_to_metadata_or_another_origin(self):
        with override_settings(BYOK_PRIVATE_ORIGINS=["http://ollama.internal:11434"]), mock.patch(
            "socket.getaddrinfo", return_value=[dns_answer("169.254.169.254")]
        ):
            policy = ProviderPolicy.for_url("http://ollama.internal:11434/v1")
            with self.assertRaises(ProviderURLRejected):
                policy.addresses("ollama.internal", 11434)
            with self.assertRaises(ProviderURLRejected):
                policy.addresses("another.internal", 11434)

    def test_compressed_response_and_response_overflow_fail_without_buffering_all(self):
        from capp.classes.byok.transport import _BoundedResponse
        import httpx

        stream = mock.Mock()
        stream.__iter__ = mock.Mock(return_value=iter([b"x" * (4 * 1024 * 1024), b"x", b"never-read"]))
        with self.assertRaises(httpx.DecodingError):
            list(_BoundedResponse(stream))
        compressed = b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 1\r\n\r\nx"
        with mock.patch("socket.getaddrinfo", return_value=[dns_answer("8.8.8.8")]), mock.patch(
            "socket.socket", return_value=FakeProviderSocket(compressed)
        ), mock.patch("httpcore._backends.sync.SyncStream.start_tls", new=lambda self, **kwargs: self):
            with provider_http_client("https://model.example/v1") as client:
                with self.assertRaises(httpx.DecodingError):
                    client.post("https://model.example/v1", json={})


class SavedProviderKeyTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        from capp.models import UserBYOKConfig

        UserBYOKConfig.create_or_update(
            user=self.user,
            api_url="https://old.example/v1",
            api_key="saved-key",
            model_name="model",
            system_prompt="",
            store_on_platform=True,
        )

    def test_changed_origin_requires_reentry_before_any_validation_call(self):
        with mock.patch.object(BYOKService, "validate_credentials") as validate:
            result = BYOKService.save_config(self.user, "https://new.example/v1", "", "model")
        self.assertEqual(result["data"], "api_key_required_for_origin_change")
        validate.assert_not_called()

    def test_same_origin_path_change_retains_key_and_explicit_new_key_changes_origin(self):
        with mock.patch.object(BYOKService, "validate_credentials", return_value={"status": "success"}) as validate:
            result = BYOKService.save_config(self.user, "https://old.example:443/proxy/v1", "", "model")
            self.assertEqual(result["status"], "success")
            self.assertEqual(validate.call_args.args[1], "saved-key")
            result = BYOKService.save_config(self.user, "https://new.example/v1", "new-key", "model")
            self.assertEqual(result["status"], "success")
            self.assertEqual(validate.call_args.args[1], "new-key")

    def test_config_reports_uses_gateway_only_for_own_gateway_origins(self):
        self.assertFalse(BYOKService.get_config(self.user)["data"]["uses_gateway"])
        with mock.patch.object(BYOKService, "validate_credentials", return_value={"status": "success"}):
            result = BYOKService.save_config(self.user, "https://agent-api.uwenzai.com/api/v1", "new-key", "model")
        self.assertTrue(result["data"]["uses_gateway"])
        self.assertTrue(BYOKService.get_config(self.user)["data"]["uses_gateway"])
        # A lookalike origin must not flip the flag: the companion UI relies
        # on it to explain that tool calls are unavailable on this endpoint.
        with mock.patch.object(BYOKService, "validate_credentials", return_value={"status": "success"}):
            result = BYOKService.save_config(
                self.user, "https://agent-api.uwenzai.com.evil.example/api/v1", "new-key", "model"
            )
        self.assertFalse(result["data"]["uses_gateway"])
        self.assertFalse(BYOKService.get_config(self.user)["data"]["uses_gateway"])


class EncryptedReadTests(SimpleTestCase):
    def test_correct_key_and_legacy_plaintext_round_trip(self):
        field = EncryptedTextField()
        encrypted = field.get_prep_value("private text")
        self.assertEqual(field.from_db_value(encrypted, None, None), "private text")
        self.assertEqual(field.from_db_value("legacy plaintext", None, None), "legacy plaintext")
        self.assertIsNone(field.from_db_value(None, None, None))

    def test_wrong_missing_or_corrupt_key_never_returns_ciphertext_or_overwrites_data(self):
        field = EncryptedTextField()
        encrypted = field.get_prep_value("private text")
        for key in (Fernet.generate_key().decode(), None, "invalid"):
            with self.subTest(key=bool(key)), override_settings(MESSAGE_ENCRYPTION_KEY=key):
                result = field.from_db_value(encrypted, None, None)
                self.assertIsInstance(result, UnavailableEncryptedValue)
                self.assertNotIn(encrypted, str(result))
                with self.assertRaises(EncryptedFieldError):
                    field.get_prep_value(result)
        result = field.from_db_value(encrypted[:-8] + "corrupt!", None, None)
        self.assertIsInstance(result, UnavailableEncryptedValue)


class OTPTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_new_codes_are_keyed_user_and_purpose_bound_not_plaintext_or_weak_hash(self):
        recovery = PasswordRecovery.create_for_user(self.user)
        code = recovery.raw_code
        recovery.refresh_from_db()
        self.assertRegex(code, r"^[0-9]{6}$")
        self.assertTrue(code_matches(code, recovery.code, self.user.pk, "password_reset"))
        self.assertNotEqual(recovery.code, code)
        self.assertNotEqual(recovery.code, hashlib.sha256(code.encode()).hexdigest())
        self.assertFalse(code_matches(code, recovery.code, uuid.uuid4(), "password_reset"))
        self.assertFalse(code_matches(code, recovery.code, self.user.pk, "email_change"))
        with override_settings(AUTH_CODE_HMAC_KEY="different-offline-key"):
            self.assertFalse(code_matches(code, recovery.code, self.user.pk, "password_reset"))

    def test_verification_attempt_budget_expiry_and_single_consume(self):
        code = EmailVerificationService.create_verification_for_user(self.user)["code"]
        row = EmailVerification.objects.get(user=self.user, status="pending")
        self.assertTrue(row.code_hash.startswith("h1$"))
        for _ in range(5):
            self.assertEqual(EmailVerificationService.verify_code(self.user, "xxxxxx")["status"], "fail")
        self.assertEqual(
            EmailVerificationService.verify_code(self.user, code)["data"], "verification_attempts_exceeded"
        )
        code = EmailVerificationService.create_verification_for_user(self.user)["code"]
        self.assertEqual(EmailVerificationService.verify_code(self.user, code)["status"], "success")
        self.assertEqual(EmailVerificationService.verify_code(self.user, code)["data"], "verification_not_found")
        code = EmailVerificationService.create_verification_for_user(self.user)["code"]
        EmailVerification.objects.filter(status="pending").update(expires_at=timezone.now())
        self.assertEqual(EmailVerificationService.verify_code(self.user, code)["data"], "verification_expired")

    def test_reset_attempts_and_success_consume_are_preserved(self):
        recovery = PasswordRecovery.create_for_user(self.user)
        payload = {
            "email": "alice@example.com",
            "password": "pwDifferent123",
            "confirm_password": "pwDifferent123",
            "code": "xxxxxx",
        }
        self.assertEqual(Auth.confirm_password_reset(payload)["data"], "invalid_recovery_code")
        recovery.refresh_from_db()
        self.assertEqual(recovery.attempts, 1)
        payload["code"] = recovery.raw_code
        self.assertEqual(Auth.confirm_password_reset(payload)["status"], "success")
        self.assertEqual(Auth.confirm_password_reset(payload)["status"], "fail")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("pwDifferent123"))

    def test_password_email_receives_raw_code_not_digest(self):
        with mock.patch(
            "capp.classes.auth.auth.PasswordRecoveryEmailService.send_recovery_code", return_value=True
        ) as send:
            Auth.request_password_reset({"email": "alice@example.com"})
        code = send.call_args.kwargs["code"]
        self.assertRegex(code, r"^[0-9]{6}$")
        self.assertTrue(
            code_matches(code, PasswordRecovery.objects.get(user=self.user).code, self.user.pk, "password_reset")
        )

    def test_email_change_hash_attempts_expiry_and_consume(self):
        with mock.patch(
            "capp.classes.auth.auth.VerificationEmailService.send_verification_code", return_value=True
        ) as send:
            self.assertEqual(
                Auth.request_email_change(self.user, {"new_email": "changed@example.com"})["status"], "success"
            )
        code = send.call_args.kwargs["code"]
        row = EmailChangeRequest.objects.get(user=self.user)
        self.assertTrue(code_matches(code, row.code, self.user.pk, "email_change"))
        self.assertEqual(Auth.confirm_email_change(self.user, {"code": "xxxxxx"})["data"], "invalid_code")
        row.refresh_from_db()
        self.assertEqual(row.attempts, 1)
        self.assertEqual(Auth.confirm_email_change(self.user, {"code": code})["status"], "success")
        self.assertEqual(Auth.confirm_email_change(self.user, {"code": code})["status"], "fail")

    def test_migration_upgrades_live_legacy_codes_preserving_all_state(self):
        from django.apps import apps

        recovery = PasswordRecovery.create_for_user(self.user)
        PasswordRecovery.objects.filter(pk=recovery.pk).update(code="123456", attempts=2)
        verification = EmailVerificationService.create_verification_for_user(self.user)
        row = EmailVerification.objects.get(user=self.user, status="pending")
        EmailVerification.objects.filter(pk=row.pk).update(
            code_hash=hashlib.sha256(verification["code"].encode()).hexdigest(), attempts=3
        )
        before = recovery.expires_at
        migration = importlib.import_module("capp.migrations.0088_auth_code_hmac")
        migration.upgrade_codes(apps, SimpleNamespace(connection=connection))
        recovery.refresh_from_db()
        row.refresh_from_db()
        self.assertTrue(code_matches("123456", recovery.code, self.user.pk, "password_reset"))
        self.assertTrue(code_matches(verification["code"], row.code_hash, self.user.pk, "email_verification"))
        self.assertEqual((recovery.attempts, recovery.is_used, recovery.expires_at), (2, False, before))
        self.assertEqual(row.attempts, 3)

    def test_migration_converts_plain_email_change_and_legacy_change_verification(self):
        from django.apps import apps

        change = EmailChangeRequest.objects.create(
            user=self.user,
            code="123456",
            expires_at=timezone.now(),
            new_email_hash="offline-hash",
            new_email_encrypted="offline-encrypted",
        )
        EmailChangeRequest.objects.filter(pk=change.pk).update(code="123456", attempts=4, is_used=True)
        row = EmailVerification.objects.create(
            user=self.user,
            code_hash=hashlib.sha256(b"123456").hexdigest(),
            new_email_hash="offline-hash",
            expires_at=timezone.now(),
            attempts=4,
            status="used",
        )
        migration = importlib.import_module("capp.migrations.0088_auth_code_hmac")
        migration.upgrade_codes(apps, SimpleNamespace(connection=connection))
        migration.upgrade_codes(apps, SimpleNamespace(connection=connection))
        change.refresh_from_db()
        row.refresh_from_db()
        self.assertTrue(code_matches("123456", change.code, self.user.pk, "email_change"))
        self.assertTrue(code_matches("123456", row.code_hash, self.user.pk, "email_change"))
        self.assertEqual((change.attempts, change.is_used), (4, True))
        self.assertEqual((row.attempts, row.status), (4, "used"))


class SocketRevocationTests(TransactionTestCase):
    async def test_both_socket_admission_paths_reject_all_disabled_account_states(self):
        from asgiref.sync import sync_to_async
        from capp.consumers import ChatConsumer, SparkPoolConsumer

        user = await sync_to_async(_make_user)()
        token = str(AccessToken.for_user(user))
        for consumer_class in (ChatConsumer, SparkPoolConsumer):
            for state in (
                {"is_active": False},
                {"is_deleted": True},
                {"account_status": "banned"},
                {"account_status": "suspended"},
            ):
                with self.subTest(consumer=consumer_class.__name__, state=state):
                    await CustomUser.objects.filter(pk=user.pk).aupdate(
                        is_active=True, is_deleted=False, account_status="active"
                    )
                    await CustomUser.objects.filter(pk=user.pk).aupdate(**state)
                    consumer = consumer_class()
                    consumer.scope = {
                        "query_string": f"token={token}".encode(),
                        "url_route": {"kwargs": {"conversation_id": str(uuid.uuid4())}},
                    }
                    consumer.base_send = mock.AsyncMock()
                    consumer.channel_layer = SimpleNamespace(group_add=mock.AsyncMock())
                    await consumer.connect()
                    consumer.channel_layer.group_add.assert_not_called()
                    self.assertEqual(consumer.base_send.call_args.args[0]["type"], "websocket.close")

    async def test_existing_socket_suppresses_messages_when_account_status_changes(self):
        from asgiref.sync import sync_to_async
        from capp.consumers import ChatConsumer

        user = await sync_to_async(_make_user)()
        consumer = ChatConsumer()
        consumer.user, consumer.is_guest, consumer.token_exp = user, False, time.time() + 60
        consumer.base_send = mock.AsyncMock()
        await consumer.send(text_data="before")
        consumer.base_send.assert_called_once()
        await CustomUser.objects.filter(pk=user.pk).aupdate(account_status="banned")
        consumer.base_send.reset_mock()
        await consumer.send(text_data="after")
        self.assertEqual(consumer.base_send.call_args.args[0]["type"], "websocket.close")
        self.assertNotIn("after", str(consumer.base_send.call_args))

    async def test_admitted_socket_survives_token_expiry_but_closes_for_deleted_account(self):
        """JWT expiry is enforced at admission only; current clients never reconnect."""
        from asgiref.sync import sync_to_async
        from capp.consumers import ChatConsumer

        user = await sync_to_async(_make_user)()
        consumer = ChatConsumer()
        consumer.user, consumer.is_guest, consumer.token_exp = user, False, time.time() - 1
        consumer.base_send = mock.AsyncMock()
        await consumer.send(text_data="still delivered")
        self.assertEqual(consumer.base_send.call_args.args[0]["type"], "websocket.send")
        consumer.base_send.reset_mock()
        consumer.user = SimpleNamespace(pk=uuid.uuid4())  # account no longer exists
        await consumer.send(text_data="gone")
        self.assertEqual(consumer.base_send.call_args.args[0]["type"], "websocket.close")
        self.assertNotIn("gone", str(consumer.base_send.call_args))
        consumer._start_auth_watch()
        await consumer._stop_auth_watch()


class OTPConcurrencyTests(TransactionTestCase):
    """Real separate PostgreSQL connections, not mocks of row locking."""

    def setUp(self):
        self.user = _make_user()

    def race(self, operation, count=2):
        barrier = Barrier(count)

        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def test_parallel_verification_failures_do_not_lose_attempts(self):
        EmailVerificationService.create_verification_for_user(self.user)
        self.race(lambda: EmailVerificationService.verify_code(self.user, "xxxxxx"), count=5)
        self.assertEqual(EmailVerification.objects.get(user=self.user).attempts, 5)

    def test_all_code_flows_consume_once_under_two_connection_race(self):
        verify_code = EmailVerificationService.create_verification_for_user(self.user)["code"]
        results = self.race(lambda: EmailVerificationService.verify_code(self.user, verify_code))
        self.assertEqual(sum(r["status"] == "success" for r in results), 1)
        recovery = PasswordRecovery.create_for_user(self.user)
        payload = {
            "email": "alice@example.com",
            "code": recovery.raw_code,
            "password": "pwChanged123",
            "confirm_password": "pwChanged123",
        }
        results = self.race(lambda: Auth.confirm_password_reset(payload))
        self.assertEqual(sum(r["status"] == "success" for r in results), 1)
        with mock.patch(
            "capp.classes.auth.auth.VerificationEmailService.send_verification_code", return_value=True
        ) as send:
            Auth.request_email_change(self.user, {"new_email": "concurrent@example.com"})
        change_code = send.call_args.kwargs["code"]
        results = self.race(lambda: Auth.confirm_email_change(self.user, {"code": change_code}))
        self.assertEqual(sum(r["status"] == "success" for r in results), 1)


class PasswordPolicyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()

    def test_range_8_through_128_and_configured_weak_password_rules(self):
        self.assertIsNone(Auth._password_error("zQ9!yK8@"))
        self.assertIsNone(Auth._password_error("zQ9!yK8@" * 16))
        self.assertEqual(Auth._password_error("zQ9!yK8"), "password_too_short")
        self.assertEqual(Auth._password_error("zQ9!yK8@" * 16 + "a"), "password_too_long")
        for value in ("password", "123456789", "alice!!!!", [1, 2]):
            with self.subTest(value_type=type(value).__name__):
                self.assertIsNotNone(Auth._password_error(value, self.user))

    def test_reset_runs_user_aware_validators_only_after_the_code_is_verified(self):
        """A wrong code must not reveal name/e-mail similarity of the account's data."""
        payload = {
            "email": "alice@example.com",
            "code": "000000",
            "password": "example-reset-passphrase-2026",  # test fixture, not a credential
            "confirm_password": "example-reset-passphrase-2026",
        }
        with mock.patch("capp.classes.auth.auth.validate_password") as validator:
            result = Auth.confirm_password_reset(payload)
        self.assertEqual(result["data"], "invalid_recovery_code")
        self.assertTrue(all(call.kwargs.get("user") is None for call in validator.call_args_list))

    def test_signup_change_and_reset_all_invoke_strength_validators(self):
        with mock.patch(
            "capp.classes.auth.auth.validate_password",
            side_effect=importlib.import_module("django.core.exceptions").ValidationError("weak"),
        ) as validator:
            self.assertEqual(
                Auth.signup_user(
                    {
                        "username": "newbie",
                        "email": "newbie@example.com",
                        "password": "weakweak",
                        "confirm_password": "weakweak",
                    }
                )["data"],
                "password_validation_failed",
            )
            self.assertEqual(
                Auth.change_password(
                    self.user,
                    {"current_password": "pw123456", "new_password": "weakweak", "confirm_password": "weakweak"},
                )["data"],
                "password_validation_failed",
            )
            self.assertEqual(
                Auth.confirm_password_reset(
                    {
                        "email": "alice@example.com",
                        "code": "123456",
                        "password": "weakweak",
                        "confirm_password": "weakweak",
                    }
                )["data"],
                "password_validation_failed",
            )
        self.assertGreaterEqual(validator.call_count, 3)

    def test_existing_short_password_keeps_signing_in(self):
        self.user.set_password("old123")
        self.user.save(update_fields=["password"])
        with mock.patch("capp.classes.auth.auth.RecaptchaService.verify_token", return_value={"success": True}):
            result = Auth.signin_user({"username": self.user.username, "password": "old123"})
        self.assertEqual(result["status"], "success")

    def test_change_password_accepts_long_passphrase(self):
        password = "A fresh longer passphrase for this offline account!"
        self.assertEqual(
            Auth.change_password(
                self.user,
                {
                    "current_password": "pw123456",
                    "new_password": password,
                    "confirm_password": password,
                },
            )["status"],
            "success",
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))


@override_settings(GOOGLE_WEB_CLIENT_ID="offline-client")
class GoogleLinkConfirmationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.verifier = mock.patch(
            "capp.classes.auth.auth.id_token.verify_oauth2_token",
            return_value={"sub": "offline-google-sub", "email": "alice@example.com", "email_verified": True},
        )
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def post(self, payload, user=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.for_user(user)}"} if user else {}
        return self.client.post(
            "/api/v1/auth/google/",
            {"credential": "offline-token", **payload},
            content_type="application/json",
            **headers,
        )

    def test_email_match_alone_and_non_boolean_confirmation_never_link(self):
        for payload, user in (
            ({}, None),
            ({"confirm_link": True}, None),
            ({}, self.user),
            ({"confirm_link": "true"}, self.user),
        ):
            with self.subTest(payload=payload):
                response = self.post(payload, user)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["data"], "google_link_confirmation_required")
                self.user.refresh_from_db()
                self.assertIsNone(self.user.google_sub)

    def test_wrong_account_session_cannot_confirm(self):
        other = _make_user(username="other", email="other@example.com")
        response = self.post({"confirm_link": True}, other)
        self.assertEqual(response.json()["data"], "google_link_confirmation_required")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.google_sub)

    def test_existing_credential_signin_then_explicit_confirmation_links_once(self):
        with mock.patch("capp.classes.auth.auth.RecaptchaService.verify_token", return_value={"success": True}):
            signin = self.client.post(
                "/api/v1/auth/signin/", {"username": "alice", "password": "pw123456"}, content_type="application/json"
            )
        jwt = signin.json()["data"]["access_token"]
        response = self.client.post(
            "/api/v1/auth/google/",
            {"credential": "offline-token", "confirm_link": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {jwt}",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.google_sub, "offline-google-sub")
        # Already-linked Google sign-in remains unchanged: no account JWT/flag required.
        self.assertEqual(self.post({}).status_code, 200)

    def test_confirmation_cannot_switch_to_an_already_linked_different_account(self):
        self.user.google_sub = "offline-google-sub"
        self.user.save(update_fields=["google_sub"])
        other = _make_user(username="wrongconfirmer", email="wrongconfirmer@example.com")
        for confirmer in (None, other):
            with self.subTest(confirmer=bool(confirmer)):
                self.client.cookies.clear()
                response = self.post({"confirm_link": True}, confirmer)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["data"], "google_link_confirmation_required")
                self.assertNotIn("access_token", response.json())
                self.assertFalse(response.cookies)
        self.assertEqual(self.post({"confirm_link": True}, self.user).status_code, 200)
        # Ordinary sign-in is still possible without a prior password session.
        self.client.cookies.clear()
        self.assertEqual(self.post({}).status_code, 200)

    def test_confirmation_never_issues_tokens_or_cookies_or_changes_login_state(self):
        token = str(AccessToken.for_user(self.user))
        before_login = self.user.last_login
        for already_linked in (False, True):
            with self.subTest(already_linked=already_linked):
                with mock.patch(
                    "capp.classes.auth.auth.RefreshToken.for_user",
                    side_effect=AssertionError("Link confirmation must not mint login tokens"),
                ):
                    response = self.client.post(
                        "/api/v1/auth/google/",
                        data={"credential": "offline-google-token", "confirm_link": True},
                        content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {token}",
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "status": "success",
                        "data": {
                            "message": "google_link_successful",
                            "linked": True,
                            "user": {"user_id": str(self.user.pk)},
                        },
                    },
                )
                self.assertFalse(response.cookies)
                self.user.refresh_from_db()
                self.assertEqual(self.user.google_sub, "offline-google-sub")
                self.assertEqual(self.user.last_login, before_login)

    def test_google_cannot_register_a_new_account_or_replace_an_existing_binding(self):
        self.user.google_sub = "different-google-sub"
        self.user.save(update_fields=["google_sub"])
        self.assertEqual(self.post({"confirm_link": True}, self.user).json()["data"], "google_account_already_linked")
        self.user.delete()
        self.assertEqual(self.post({}).json()["data"], "google_signup_not_allowed")
