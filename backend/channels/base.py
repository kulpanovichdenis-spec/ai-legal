"""
Channel contracts — каналонезависимые dataclass-ы (Этап 1).

InboundMessage  — нормализованное входящее сообщение из любого канала.
ChannelResponse — ответ, адаптируемый под конкретный канал.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    channel: str                          # "web" | "bitrix24" | "api"
    user_id: str | None                   # internal user id (JWT-based, None для bitrix24)
    external_user_id: str | None          # ID пользователя в внешней системе
    external_chat_id: str | None          # ID диалога / чата в внешней системе
    external_message_id: str | None       # ID сообщения в внешней системе
    text: str
    attachments: list[Any] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelResponse:
    channel: str
    text: str
    structured_data: dict[str, Any] | None = None
    target_chat_id: str | None = None
    stream_events: list[dict] = field(default_factory=list)
