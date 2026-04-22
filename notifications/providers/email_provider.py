"""Email provider — thin wrapper over Django's email backend.

Configure `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL` etc. in settings as usual.
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage


class DjangoEmailProvider:
    name = "django-email"

    def send(self, delivery):
        msg = EmailMessage(
            subject=delivery.subject or "Meili Property",
            body=delivery.body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@meili.test"),
            to=[delivery.recipient],
        )
        msg.send(fail_silently=False)
        return {"provider": self.name, "message_id": "", "raw": {}}
