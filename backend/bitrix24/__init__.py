"""Bitrix24 integration package (scaffold, Этап 1)."""

from bitrix24.client import Bitrix24Client
from bitrix24.schemas import Bitrix24WebhookPayload, Bitrix24BotMessage
from bitrix24.auth import verify_webhook_token

__all__ = ["Bitrix24Client", "Bitrix24WebhookPayload", "Bitrix24BotMessage", "verify_webhook_token"]
