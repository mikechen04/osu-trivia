"""Small fixed-window rate limiter on top of Django's cache.

Limits are applied per client IP and (when signed in) per user. IPs are hashed
before they go into cache keys so they are never stored in readable form.
"""

import hashlib
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render


def client_ip(request) -> str:
    """Real client IP. Only trusts X-Forwarded-For entries added by our own proxies:
    with N trusted proxies the client is the Nth entry from the right; anything
    further left could have been forged by the client."""
    n = settings.TRUSTED_PROXY_COUNT
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if n > 0 and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= n:
            return parts[-n]
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def allow(scope: str, ident, limit: int, window: int = 60) -> bool:
    digest = hashlib.sha256(f"{settings.SECRET_KEY}:{ident}".encode()).hexdigest()[:24]
    key = f"rl:{scope}:{digest}:{int(time.time() // window)}"
    if cache.add(key, 1, timeout=window * 2):
        return limit >= 1
    try:
        count = cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, timeout=window * 2)
        count = 1
    return count <= limit


def _limited(request, window: int):
    if request.path.startswith("/api/"):
        response = JsonResponse(
            {"error": {"code": "rate_limited", "message": "Too many requests. Please slow down."}},
            status=429,
        )
    else:
        response = render(request, "429.html", status=429)
    response["Retry-After"] = str(window)
    return response


def ratelimit(scope: str, *, user: int | None = None, ip: int | None = None, window: int = 60):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if settings.RATELIMIT_ENABLED:
                if ip and not allow(f"{scope}:ip", client_ip(request), ip, window):
                    return _limited(request, window)
                if user and request.user.is_authenticated and not allow(f"{scope}:user", request.user.pk, user, window):
                    return _limited(request, window)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
