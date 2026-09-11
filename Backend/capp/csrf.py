"""
CSRF enforcement for endpoints that accept an ambient cookie credential.

``rest_framework.decorators.api_view`` marks the view it produces
``csrf_exempt``, and ``CsrfViewMiddleware`` — including the copy that
``csrf_protect`` installs — honours that flag. A bare ``@csrf_protect``
stacked above ``@api_view`` therefore silently does nothing, because
``functools.wraps`` copies ``csrf_exempt = True`` onto the wrapper. The
decorators here strip that flag from the callable the CSRF check inspects, so
the check actually runs.
"""

from functools import wraps

from django.views.decorators.csrf import csrf_protect


def _enforced(view):
    """``view`` wrapped in a real CSRF check, ignoring DRF's csrf_exempt flag."""

    @wraps(view)
    def _inner(request, *args, **kwargs):
        return view(request, *args, **kwargs)

    _inner.csrf_exempt = False
    return csrf_protect(_inner)


def _carries_bearer_credential(request) -> bool:
    """
    True only for an authenticated, eligible account's valid access JWT.

    Header presence alone is not authentication: a permitted CORS origin can
    send arbitrary headers alongside a victim's ambient guest/refresh cookie.
    Invalid headers must never switch a cookie request out of CSRF enforcement.
    """
    from rest_framework.exceptions import AuthenticationFailed
    from capp.authentication import AccountJWTAuthentication

    try:
        result = AccountJWTAuthentication().authenticate(request)
    except AuthenticationFailed:
        return False
    return result is not None


def csrf_protect_api(view):
    """Require a CSRF token on an ``api_view`` that authenticates by cookie."""
    protected = _enforced(view)

    @wraps(view)
    def _wrapper(request, *args, **kwargs):
        return protected(request, *args, **kwargs)

    return _wrapper


def csrf_protect_for_guests(view):
    """
    Require a CSRF token only from callers relying on ambient cookies.

    Used on the mixed endpoints that serve both JWT-bearing accounts and
    cookie-bearing guests.
    """
    protected = _enforced(view)

    @wraps(view)
    def _wrapper(request, *args, **kwargs):
        if _carries_bearer_credential(request):
            return view(request, *args, **kwargs)
        return protected(request, *args, **kwargs)

    return _wrapper
