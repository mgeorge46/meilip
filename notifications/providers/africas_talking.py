"""Africa's Talking SMS + WhatsApp provider (httpx, sync).

Swappable: set `NOTIFICATION_PROVIDERS = {"SMS": "console", ...}` in
settings to disable outbound calls in dev/test.
"""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


SMS_ENDPOINT = "https://api.africastalking.com/version1/messaging"
WHATSAPP_ENDPOINT = "https://chat.africastalking.com/whatsapp/message/send"


def _auth_headers():
    api_key = getattr(settings, "AT_API_KEY", "")
    username = getattr(settings, "AT_USERNAME", "sandbox")
    return username, {
        "apiKey": api_key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }


class AfricasTalkingSMSProvider:
    name = "africastalking-sms"

    def send(self, delivery):
        username, headers = _auth_headers()
        sender_id = getattr(settings, "AT_SENDER_ID", "") or None
        data = {
            "username": username,
            "to": delivery.recipient,
            "message": delivery.body,
        }
        if sender_id:
            data["from"] = sender_id
        timeout = httpx.Timeout(
            getattr(settings, "NOTIFICATION_HTTP_TIMEOUT", 15.0)
        )
        with httpx.Client(timeout=timeout) as client:
            response = client.post(SMS_ENDPOINT, headers=headers, data=data)
            response.raise_for_status()
            payload = response.json()
        recipients = (payload.get("SMSMessageData", {}) or {}).get("Recipients", []) or []
        msg_id = recipients[0].get("messageId", "") if recipients else ""
        return {"provider": self.name, "message_id": msg_id, "raw": payload}


class AfricasTalkingWhatsAppProvider:
    name = "africastalking-whatsapp"

    def send(self, delivery):
        username, headers = _auth_headers()
        headers["Content-Type"] = "application/json"
        channel_num = getattr(settings, "AT_WHATSAPP_CHANNEL", "")
        body = {
            "username": username,
            "waGatewayId": channel_num,
            "phoneNumber": delivery.recipient,
            "body": {"text": delivery.body},
        }
        timeout = httpx.Timeout(
            getattr(settings, "NOTIFICATION_HTTP_TIMEOUT", 15.0)
        )
        with httpx.Client(timeout=timeout) as client:
            response = client.post(WHATSAPP_ENDPOINT, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        return {
            "provider": self.name,
            "message_id": str(payload.get("id", "")),
            "raw": payload,
        }
