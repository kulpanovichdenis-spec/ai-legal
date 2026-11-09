"""
Bitrix24Adapter — scaffold для будущей интеграции с Bitrix24 Chat (Этап 1).

На этапе 1:
  - нормализует входящий webhook payload в InboundMessage;
  - создаёт RequestContext для общего orchestrator pipeline;
  - не имеет своей routing-логики;
  - поддерживает команды (команды → scenario hint, не legacy mode);
  - Bitrix24 использует тот же orchestrator, что и web.

На этапе 2:
  - полноценная отправка ответа через Bitrix24Client.send_message();
  - скачивание вложений через Bitrix24Client.download_file();
  - регистрация бота.

Команды Bitrix24:
  /консультация → preferred_scenario_hint = "legal_consultation"
  /реклама      → preferred_scenario_hint = "ad_check_general"
  /документ     → preferred_scenario_hint = "contract_analysis" (router уточняет)
  /регламент    → preferred_scenario_hint = "regulation_lookup"
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from channels.base import InboundMessage
from schemas_agent import RequestContext

logger = logging.getLogger("legal-assistant")

# Команды → scenario hint (не legacy mode)
_COMMAND_MAP: dict[str, str] = {
    "/консультация": "legal_consultation",
    "/реклама":      "ad_check_general",
    "/документ":     "contract_analysis",
    "/регламент":    "regulation_lookup",
    "/pretenziya":   "claim_drafting",
    "/pretension":   "claim_drafting",
}

_COMMAND_RE = re.compile(r"^(/[а-яёa-z_]+)", re.IGNORECASE)


def _parse_command(text: str) -> tuple[str | None, str]:
    """Вернуть (scenario_hint | None, cleaned_text)."""
    m = _COMMAND_RE.match(text.strip())
    if not m:
        return None, text

    cmd = m.group(1).lower()
    hint = _COMMAND_MAP.get(cmd)
    cleaned = text[m.end():].strip()
    return hint, cleaned


class Bitrix24Adapter:
    channel = "bitrix24"

    @staticmethod
    def normalize_inbound(payload: dict[str, Any]) -> InboundMessage:
        """Нормализует входящий Bitrix24 webhook payload в InboundMessage.

        Поля payload (от bot webhook event):
          data.PARAMS.MESSAGE  — текст сообщения
          data.USER_ID         — ID пользователя Bitrix24
          data.DIALOG_ID       — ID диалога
          data.MESSAGE_ID      — ID сообщения
        """
        data = payload.get("data", payload)
        params = data.get("PARAMS", {})

        text = (
            params.get("MESSAGE")
            or data.get("message", {}).get("text", "")
            or ""
        )
        external_user_id = str(data.get("USER_ID") or data.get("user_id") or "")
        external_chat_id = str(data.get("DIALOG_ID") or data.get("dialog_id") or "")
        external_message_id = str(data.get("MESSAGE_ID") or data.get("message_id") or "")

        return InboundMessage(
            channel="bitrix24",
            user_id=None,   # будет заполнено через channel_sessions (Этап 2)
            external_user_id=external_user_id or None,
            external_chat_id=external_chat_id or None,
            external_message_id=external_message_id or None,
            text=text,
            attachments=[],  # TODO Этап 2: download_file из Bitrix24 Storage
            raw_payload=payload,
        )

    @staticmethod
    def to_request_context(
        inbound: InboundMessage,
        internal_user_id: str | None = None,
        conversation_id: str | None = None,
        file_context: str | None = None,
        raw_file_context: str | None = None,
    ) -> tuple[RequestContext, str | None]:
        """Вернуть (RequestContext, scenario_hint).

        scenario_hint используется orchestrator как подсказка к router,
        но router может её игнорировать или изменить.
        """
        text = inbound.text
        scenario_hint, cleaned_text = _parse_command(text)

        if scenario_hint:
            logger.info(
                "[Bitrix24Adapter] command hint → scenario_hint=%r, user=%s",
                scenario_hint, inbound.external_user_id,
            )
            text = cleaned_text or text

        ctx = RequestContext(
            request_id=str(uuid.uuid4()),
            user_id=internal_user_id or "",
            message=text,
            conversation_id=conversation_id,
            legacy_mode=None,   # не используем legacy mode для Bitrix24
            channel="bitrix24",
            external_user_id=inbound.external_user_id,
            external_chat_id=inbound.external_chat_id,
            external_message_id=inbound.external_message_id,
            channel_metadata={
                "raw_payload": inbound.raw_payload,
                "scenario_hint": scenario_hint,
            },
            attachments=inbound.attachments,
            file_context=file_context,
            raw_file_context=raw_file_context,
            history_summary=None,
            metadata={"history_tail": [], "scenario_hint": scenario_hint},
        )
        return ctx, scenario_hint
