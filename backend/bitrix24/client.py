"""
Bitrix24Client — HTTP-клиент для Bitrix24 REST API (Этап 1 scaffold).

Минимальный интерфейс:
  - register_bot()   — регистрация бота (TODO Этап 2)
  - send_message()   — отправка сообщения через imbot.message.add
  - download_file()  — TODO Этап 2

Для Bitrix24 REST API используется webhook URL вида:
  https://<portal>.bitrix24.ru/rest/<user_id>/<webhook_code>/

Источник: https://apidocs.bitrix24.ru/api-reference/imopenlines/imbot/

При реализации Этапа 2 уточнять актуальные поля через Bitrix24 MCP server:
  claude mcp add --transport http b24-dev-mcp https://mcp-dev.bitrix24.tech/mcp
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("legal-assistant")

_BITRIX24_WEBHOOK_URL = os.getenv("BITRIX24_WEBHOOK_URL", "")
_BITRIX24_BOT_ID = os.getenv("BITRIX24_BOT_ID", "")
_TIMEOUT = 15.0


class Bitrix24Client:
    def __init__(
        self,
        webhook_url: str = "",
        bot_id: str = "",
    ) -> None:
        self._webhook_url = webhook_url or _BITRIX24_WEBHOOK_URL
        self._bot_id = bot_id or _BITRIX24_BOT_ID

    async def _call(self, method: str, params: dict[str, Any]) -> dict:
        """Вызов Bitrix24 REST API метода."""
        if not self._webhook_url:
            logger.warning("[Bitrix24Client] BITRIX24_WEBHOOK_URL не задан, вызов %r пропущен", method)
            return {}

        url = self._webhook_url.rstrip("/") + f"/{method}.json"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=params)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    logger.error("[Bitrix24Client] API error %r: %s", method, data["error"])
                return data
        except Exception as exc:
            logger.error("[Bitrix24Client] %r failed: %s", method, exc)
            return {}

    async def register_bot(
        self,
        name: str = "Legal Assistant",
        code: str = "legal_assistant",
        type: str = "B",  # B = bot
        event_handler: str = "",
    ) -> dict:
        """Регистрация бота в Bitrix24 (TODO Этап 2 — полная реализация).

        Метод: imbot.register
        Docs: https://apidocs.bitrix24.ru/api-reference/imopenlines/imbot/imbot-register.html
        """
        # TODO Этап 2: передать корректный OPENLINE_HANDLER, CLIENT_ID, CLIENT_SECRET
        logger.info("[Bitrix24Client] register_bot: stub (Этап 1)")
        return await self._call("imbot.register", {
            "CODE": code,
            "TYPE": type,
            "EVENT_MESSAGE_ADD": event_handler,
            "EVENT_WELCOME_MESSAGE": event_handler,
            "PROPERTIES": {
                "NAME": name,
                "COLOR": "GREEN",
            },
        })

    async def send_message(
        self,
        dialog_id: str,
        text: str,
        bot_id: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """Отправка сообщения в диалог Bitrix24.

        Метод: imbot.message.add
        Docs: https://apidocs.bitrix24.ru/api-reference/imopenlines/imbot/messages/imbot-message-add.html
        """
        params: dict[str, Any] = {
            "DIALOG_ID": dialog_id,
            "BOT_ID": bot_id or self._bot_id,
            "MESSAGE": text,
            **kwargs,
        }
        logger.info("[Bitrix24Client] send_message dialog_id=%r len=%d", dialog_id, len(text))
        return await self._call("imbot.message.add", params)

    async def download_file(self, file_id: str) -> bytes | None:
        """Скачивание файла из Bitrix24 Storage.

        TODO Этап 2: реализовать через disk.file.get + storage download.
        Docs: https://apidocs.bitrix24.ru/api-reference/disk/file/
        """
        logger.info("[Bitrix24Client] download_file: TODO Этап 2, file_id=%r", file_id)
        return None

    # ── Исходящая интеграция «Задача Битрикс» ─────────────────────────

    @property
    def portal_base(self) -> str:
        """Базовый адрес портала (scheme://host) из webhook URL."""
        parts = urlsplit(self._webhook_url)
        if not parts.scheme or not parts.netloc:
            return ""
        return f"{parts.scheme}://{parts.netloc}"

    def task_url(self, task_id: int | str, responsible_id: int | str) -> str:
        """Прямая ссылка на задачу в Битриксе."""
        base = self.portal_base
        if not base:
            return ""
        return f"{base}/company/personal/user/{responsible_id}/tasks/task/view/{task_id}/"

    @property
    def webhook_user_id(self) -> str:
        """ID пользователя из webhook URL (.../rest/<user_id>/<код>/)."""
        parts = [p for p in urlsplit(self._webhook_url).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "rest":
            return parts[1]
        return ""

    async def _resolve_storage_id(self) -> str | None:
        """Выбрать хранилище Диска для загрузки вложения.

        В корень Общего диска (ENTITY_TYPE=common) запись запрещена, поэтому
        выбираем личный диск владельца вебхука; если не найден — любое
        не-общее хранилище.
        """
        storages = await self._call("disk.storage.getlist", {})
        result = storages.get("result") or []
        if not result:
            return None
        uid = self.webhook_user_id
        own = [
            s for s in result
            if s.get("ENTITY_TYPE") == "user" and str(s.get("ENTITY_ID")) == uid
        ]
        if own:
            return own[0].get("ID")
        non_common = [s for s in result if s.get("ENTITY_TYPE") != "common"]
        if non_common:
            return non_common[0].get("ID")
        return result[0].get("ID")

    async def upload_file(
        self,
        filename: str,
        content: bytes,
        storage_id: int = 0,
    ) -> int | None:
        """Загрузить файл на Диск Битрикс24 и вернуть его ID.

        Метод: disk.storage.getlist (если storage_id не задан) → disk.storage.uploadfile.
        Docs: https://apidocs.bitrix24.ru/api-reference/disk/storage/
        """
        if not self._webhook_url:
            logger.warning("[Bitrix24Client] BITRIX24_WEBHOOK_URL не задан, upload_file пропущен")
            return None

        if not storage_id:
            storage_id = await self._resolve_storage_id()
            if not storage_id:
                logger.error("[Bitrix24Client] не удалось определить хранилище Диска для загрузки")
                return None

        encoded = base64.b64encode(content).decode("ascii")
        resp = await self._call("disk.storage.uploadfile", {
            "id": storage_id,
            "data": {"NAME": filename},
            "fileContent": [filename, encoded],
            "generateUniqueName": "Y",
        })
        file_obj = resp.get("result") or {}
        file_id = file_obj.get("ID")
        if not file_id:
            logger.error("[Bitrix24Client] disk.storage.uploadfile не вернул ID: %s", resp)
            return None
        logger.info("[Bitrix24Client] upload_file ok file_id=%s name=%r", file_id, filename)
        return int(file_id)

    async def create_task(
        self,
        title: str,
        description: str,
        responsible_id: int,
        accomplice_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> dict:
        """Создать задачу в Битрикс24.

        Метод: tasks.task.add
        Docs: https://apidocs.bitrix24.ru/api-reference/tasks/tasks/tasks-task-add.html
        """
        fields: dict[str, Any] = {
            "TITLE": title,
            "DESCRIPTION": description or "",
            "RESPONSIBLE_ID": responsible_id,
        }
        if accomplice_ids:
            fields["ACCOMPLICES"] = [int(a) for a in accomplice_ids]
        if file_ids:
            # Префикс "n" указывает Битриксу, что это новые вложения с Диска.
            fields["UF_TASK_WEBDAV_FILES"] = [f"n{fid}" for fid in file_ids]

        logger.info(
            "[Bitrix24Client] create_task title=%r responsible=%s accomplices=%s files=%s",
            title[:80], responsible_id, accomplice_ids, file_ids,
        )
        return await self._call("tasks.task.add", {"fields": fields})
