"""Origin-bound BYOK transport: resolve once, vet every IP, connect numerically.

An URL preflight alone cannot stop DNS rebinding. This httpcore backend checks
DNS at *each new TCP connection*, and connects the selected literal address
without resolving the hostname again. httpcore keeps the original origin for
Host, TLS SNI and certificate verification. No proxy or redirect escape hatch.
The httpx/httpcore versions are deliberately pinned; test before upgrading.
"""

import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

import certifi
import httpcore
import httpx
from django.conf import settings
from httpcore._backends.sync import SyncStream

# Stable across supported Python versions (ipaddress classifications change).
_LOCAL_V4 = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_PUBLIC_V6_SPACE = ipaddress.ip_network("2000::/3")
_SPECIAL_V6 = tuple(ipaddress.ip_network(value) for value in ("2001::/23", "3fff::/20"))
_PLATFORM_ADDRESSES = frozenset(
    ipaddress.ip_address(value) for value in ("168.63.129.16", "100.100.100.200", "fd00:ec2::254", "fd20:ce::254")
)


class ProviderURLRejected(ValueError):
    """Safe error: intentionally excludes URLs, API keys and DNS answers."""

    def __init__(self):
        super().__init__("Provider origin is not permitted.")


def origin(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ProviderURLRejected()
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ProviderURLRejected()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or "@" in parsed.netloc
            or "%" in parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderURLRejected()
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii").lower()
        if not host or host.endswith("."):
            raise ProviderURLRejected()
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ProviderURLRejected()
        return url.scheme, host, port
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise ProviderURLRejected() from None


def _is_public(address):
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return _is_public(address.ipv4_mapped)
        if address not in _PUBLIC_V6_SPACE or any(address in block for block in _SPECIAL_V6):
            return False
        # Transition/translation addresses can hide a non-public IPv4 route.
        if address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96"):
            return False
    else:
        if address in ipaddress.ip_network("192.0.0.0/24") and str(address) not in ("192.0.0.9", "192.0.0.10"):
            return False
        if address in ipaddress.ip_network("192.88.99.0/24"):
            return False
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
    )


@dataclass(frozen=True)
class ProviderPolicy:
    allowed_origin: tuple
    private_allowed: bool

    @classmethod
    def for_url(cls, value):
        target = origin(value)
        configured = set()
        for item in getattr(settings, "BYOK_PRIVATE_ORIGINS", ()):
            # Exact origins only: no credentials, paths, wildcards or CIDRs.
            parsed = urlsplit(item)
            if parsed.path not in ("", "/") or "*" in item:
                raise ProviderURLRejected()
            configured.add(origin(item))
        private_allowed = target in configured
        if target[0] != "https" and not private_allowed:
            raise ProviderURLRejected()
        return cls(target, private_allowed)

    def addresses(self, host, port):
        if (host, port) != self.allowed_origin[1:]:
            raise ProviderURLRejected()
        try:
            # Literal IP URLs work without DNS; otherwise check ALL A/AAAA
            # answers, not just the first address selected by a resolver.
            literal = ipaddress.ip_address(host)
            addresses = [literal]
        except ValueError:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers))
        if not addresses:
            raise ProviderURLRejected()
        for address in addresses:
            effective = getattr(address, "ipv4_mapped", None) or address
            # Never permit cloud metadata/link-local or non-unicast destinations,
            # even when an operator enables local providers.
            if (
                effective.is_link_local
                or effective.is_multicast
                or effective.is_unspecified
                or effective in _PLATFORM_ADDRESSES
            ):
                raise ProviderURLRejected()
            local_network = (
                any(effective in block for block in _LOCAL_V4)
                if effective.version == 4
                else effective in ipaddress.ip_network("fc00::/7")
            )
            approved_private = self.private_allowed and (effective.is_loopback or local_network)
            if not approved_private and not _is_public(address):
                raise ProviderURLRejected()
        return addresses


class PinnedNetworkBackend(httpcore.NetworkBackend):
    def __init__(self, policy):
        self.policy = policy

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = self.policy.addresses(host, port)
        last_error = None
        for address in addresses:
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(timeout)
                for option in socket_options or ():
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # Numeric socket.connect performs no second hostname resolution.
                sock.connect((str(address), port))
                return SyncStream(sock)
            except OSError as exc:
                sock.close()
                last_error = exc
        if isinstance(last_error, socket.timeout):
            raise httpcore.ConnectTimeout("Provider connection timed out.") from None
        raise httpcore.ConnectError("Provider connection failed.") from None

    def connect_unix_socket(self, *args, **kwargs):
        raise ProviderURLRejected()


class _BoundedResponse(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        total = 0
        for chunk in self.stream:
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                raise httpx.DecodingError("Provider response is too large.")
            yield chunk

    def close(self):
        self.stream.close()


class ProviderTransport(httpx.HTTPTransport):
    def __init__(self, policy):
        # HTTPTransport owns httpx<->httpcore exception and stream adaptation.
        # Supply its pool explicitly so DNS validation happens at socket creation.
        self.policy = policy
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
            network_backend=PinnedNetworkBackend(policy),
            retries=0,
            max_connections=2,
            max_keepalive_connections=0,
            http2=False,
        )

    def handle_request(self, request):
        if origin(str(request.url)) != self.policy.allowed_origin:
            raise ProviderURLRejected()
        # No compressed bomb: a provider must return identity encoding.
        request.headers["accept-encoding"] = "identity"
        response = super().handle_request(request)
        if 300 <= response.status_code < 400:
            response.close()
            raise ProviderURLRejected()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            response.close()
            raise httpx.DecodingError("Provider response encoding is not supported.")
        response.stream = _BoundedResponse(response.stream)
        return response


def provider_http_client(api_url):
    policy = ProviderPolicy.for_url(api_url)
    return httpx.Client(
        transport=ProviderTransport(policy),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
