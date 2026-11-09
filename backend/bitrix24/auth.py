"""
Bitrix24 webhook authentication (Этап 1 scaffold).

Bitrix24 не использует JWT. Проверка подлинности:
  1. application_token — токен приложения из настроек Bitrix24
  2. webhook_token     — секрет в URL webhook

На этапе 1 реализована проверка через application_token из .env.
На этапе 2: добавить проверку подписи (HMAC если Bitrix24 начнёт её поддерживать).
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets

logger = logging.getLogger("legal-assistant")

_APPLICATION_TOKEN = os.getenv("BITRIX24_APPLICATION_TOKEN", "")
_WEBHOOK_SECRET = os.getenv("BITRIX24_WEBHOOK_SECRET", "")


def verify_webhook_token(
    application_token: str | None = None,
    webhook_secret: str | None = None,
) -> bool:
    """Проверить подлинность входящего Bitrix24 webhook запроса.

    Возвращает True если проверка прошла.
    Возвращает False если токены не совпадают или не настроены.

    Если токены не настроены в .env — логирует предупреждение и блокирует запрос.
    """
    if not _APPLICATION_TOKEN and not _WEBHOOK_SECRET:
        logger.warning(
            "[Bitrix24Auth] BITRIX24_APPLICATION_TOKEN и BITRIX24_WEBHOOK_SECRET не заданы. "
            "Входящий webhook заблокирован для безопасности. "
            "Установите хотя бы один токен в .env."
        )
        return False

    # Проверяем application_token
    if _APPLICATION_TOKEN and application_token:
        if secrets.compare_digest(application_token, _APPLICATION_TOKEN):
            return True

    # Проверяем webhook_secret
    if _WEBHOOK_SECRET and webhook_secret:
        if secrets.compare_digest(webhook_secret, _WEBHOOK_SECRET):
            return True

    logger.warning("[Bitrix24Auth] webhook token verification failed")
    return False
