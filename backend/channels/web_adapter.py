"""
WebAdapter — тонкий wrapper вокруг текущего /api/chat (Этап 1).

Нормализует HTTP-запрос в InboundMessage / RequestContext.
Streaming (SSE) остаётся в main.py — здесь только нормализация входных данных.
"""

from __future__ import annotations

import uuid
from typing import Any

from channels.base import InboundMessage
from schemas_agent import RequestContext


class WebAdapter:
    channel = "web"

    @staticmethod
    def normalize_inbound(
        user_id: str,
        message: str,
        conversation_id: str | None = None,
        legacy_mode: str | None = None,
        attachment_names: list[str] | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> InboundMessage:
        return InboundMessage(
            channel="web",
            user_id=user_id,
            external_user_id=None,
            external_chat_id=None,
            external_message_id=None,
            text=message,
            attachments=attachment_names or [],
            raw_payload=raw_payload or {},
        )

    @staticmethod
    def to_request_context(
        inbound: InboundMessage,
        conversation_id: str | None = None,
        legacy_mode: str | None = None,
        file_context: str | None = None,
        raw_file_context: str | None = None,
        history_tail: list[dict] | None = None,
    ) -> RequestContext:
        return RequestContext(
            request_id=str(uuid.uuid4()),
            user_id=inbound.user_id or "",
            message=inbound.text,
            conversation_id=conversation_id,
            legacy_mode=legacy_mode,
            channel="web",
            external_user_id=None,
            external_chat_id=None,
            external_message_id=None,
            channel_metadata={},
            attachments=inbound.attachments,
            file_context=file_context,
            raw_file_context=raw_file_context,
            history_summary=None,
            metadata={"history_tail": history_tail or []},
        )
