"""Audit signal handlers — wire Django auth signals into AuditLog."""
from django.contrib.auth.signals import (
    user_logged_in, user_logged_out, user_login_failed,
)
from django.dispatch import receiver

from .middleware import get_current_request
from .models import AuditAction, AuditLog


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    AuditLog.record(
        AuditAction.LOGIN_SUCCESS, actor=user, request=request,
        target_repr=getattr(user, "email", str(user)),
    )


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    AuditLog.record(
        AuditAction.LOGOUT, actor=user, request=request,
        target_repr=getattr(user, "email", str(user)) if user else "",
    )


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    request = request or get_current_request()
    email = (credentials or {}).get("username") or (credentials or {}).get("email") or ""
    AuditLog.record(
        AuditAction.LOGIN_FAILED,
        actor=None, request=request,
        target_repr=email[:255],
        detail={"attempted_email": email},
    )
