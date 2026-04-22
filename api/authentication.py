"""DRF authentication class for the `X-API-Key` header.

Resolves the incoming raw key to an `ApiKey` row via prefix lookup + hash
compare, rejects inactive/revoked keys, and enforces the IP allowlist.
Returns `(None, api_key)` so DRF's `request.user` stays `AnonymousUser`
but `request.auth` is the `ApiKey` instance — views read it from there.
"""
from __future__ import annotations

from rest_framework import authentication, exceptions

from .models import ApiKey


HEADER_NAME = "HTTP_X_API_KEY"


def _client_ip(request):
    # Honour X-Forwarded-For if present (reverse proxies in prod).
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """Header: `X-API-Key: <prefix><remainder>`."""

    keyword = "X-API-Key"

    def authenticate(self, request):
        raw = request.META.get(HEADER_NAME, "")
        if not raw:
            return None  # No header → let DRF fall through / return 401
        raw = raw.strip()
        prefix = raw[:12]
        try:
            api_key = ApiKey.objects.get(key_prefix=prefix)
        except ApiKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid API key.")
        if not api_key.is_active or api_key.revoked_at is not None:
            raise exceptions.AuthenticationFailed("API key revoked.")
        if not api_key.verify(raw):
            raise exceptions.AuthenticationFailed("Invalid API key.")
        ip = _client_ip(request)
        if ip and not api_key.ip_allowed(ip):
            raise exceptions.AuthenticationFailed(
                f"Source IP {ip} not permitted for this API key."
            )
        api_key.mark_used()
        return (None, api_key)

    def authenticate_header(self, request):
        return self.keyword
