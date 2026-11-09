"""
MemoryManager — управление историей диалога (Этап 1).

Вынесен из main.py. На этапе 1 — без summary сообщений.
Ограничение глубины истории берётся из ScenarioConfig.max_history_messages.
"""

from __future__ import annotations

import logging

import aiosqlite

from schemas_agent import MemoryContext

logger = logging.getLogger("legal-assistant")


class MemoryManager:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def load(
        self,
        conversation_id: str | None,
        max_messages: int = 50,
    ) -> MemoryContext:
        if not conversation_id:
            return MemoryContext(
                messages=[],
                summary=None,
                total_messages_in_db=0,
                messages_used=0,
                truncated=False,
            )

        try:
            async with aiosqlite.connect(self._db_path, timeout=10.0) as db:
                db.row_factory = aiosqlite.Row

                # Считаем общее количество сообщений
                async with db.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                    (conversation_id,),
                ) as cur:
                    row = await cur.fetchone()
                    total = row[0] if row else 0

                # Загружаем последние max_messages
                async with db.execute(
                    """
                    SELECT role, content FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (conversation_id, max_messages),
                ) as cur:
                    rows = await cur.fetchall()

            # Переворачиваем: хотим хронологический порядок
            messages = [
                {"role": r["role"], "content": r["content"]}
                for r in reversed(rows)
            ]

            messages_used = len(messages)
            truncated = total > messages_used

            summary: str | None = None
            if truncated:
                logger.info(
                    "[MemoryManager] conversation=%s: total=%d, using last %d (truncated)",
                    conversation_id, total, messages_used,
                )
                summary = (
                    f"[КОНТЕКСТ ДИАЛОГА: диалог содержит {total} сообщений. "
                    f"В системный контекст включены последние {messages_used}. "
                    f"Более ранние сообщения не переданы — если пользователь ссылается "
                    f"на начало диалога, сообщи, что ранний контекст недоступен.]"
                )

            return MemoryContext(
                messages=messages,
                summary=summary,
                total_messages_in_db=total,
                messages_used=messages_used,
                truncated=truncated,
            )

        except Exception as exc:
            logger.error("[MemoryManager] failed to load conversation %s: %s", conversation_id, exc)
            return MemoryContext(
                messages=[],
                summary=None,
                total_messages_in_db=0,
                messages_used=0,
                truncated=False,
            )
