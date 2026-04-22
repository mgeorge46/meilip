"""Console provider — writes the message to stdout.

Used when running without real provider creds (dev / CI / tests).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConsoleProvider:
    def __init__(self, channel):
        self.channel = channel
        self.name = f"console-{channel.lower()}"

    def send(self, delivery):
        logger.info(
            "[notify/%s] → %s | %s | %s",
            self.channel, delivery.recipient, delivery.subject, delivery.body,
        )
        return {"provider": self.name, "message_id": "console", "raw": {}}
