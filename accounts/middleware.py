"""Audit middleware.

Wraps every request so downstream code (signal handlers, views, services)
can reach the originating HTTP request via a thread-local, letting them
enrich AuditLog rows with IP + User-Agent without changing call signatures.

This is deliberately thin — it does NOT auto-log every request (that would
flood the table). Views and signal handlers call AuditLog.record() for the
actions that matter.
"""
import threading

_request_tls = threading.local()


def get_current_request():
    return getattr(_request_tls, "request", None)


class AuditRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _request_tls.request = request
        try:
            return self.get_response(request)
        finally:
            _request_tls.request = None
