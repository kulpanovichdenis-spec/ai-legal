"""
Pydantic-схемы для Bitrix24 webhook (Этап 1 scaffold).

Источник: https://apidocs.bitrix24.ru/api-reference/imopenlines/imbot/
При реализации Этапа 2 использовать актуальные поля из документации Bitrix24.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Bitrix24WebhookParams(BaseModel):
    MESSAGE: str | None = None
    DIALOG_ID: str | None = None
    MESSAGE_ID: str | None = None
    BOT_ID: str | None = None

    model_config = {"extra": "allow"}


class Bitrix24WebhookData(BaseModel):
    USER_ID: str | None = None
    DIALOG_ID: str | None = None
    MESSAGE_ID: str | None = None
    PARAMS: Bitrix24WebhookParams = Field(default_factory=Bitrix24WebhookParams)

    model_config = {"extra": "allow"}


class Bitrix24WebhookPayload(BaseModel):
    """Входящий webhook payload от Bitrix24 bot."""
    event: str | None = None
    auth: dict[str, Any] | None = None
    data: Bitrix24WebhookData = Field(default_factory=Bitrix24WebhookData)
    ts: str | None = None

    model_config = {"extra": "allow"}


class Bitrix24BotMessage(BaseModel):
    """Параметры для imbot.message.add."""
    DIALOG_ID: str
    MESSAGE: str
    ATTACH: list[dict] | None = None
    KEYBOARD: dict | None = None
