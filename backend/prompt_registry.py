"""
PromptRegistry — источник system prompts с приоритетом production-данных из БД.

Иерархия источников:
  1. Таблица mode_instructions (production prompts, настраиваются admin UI)
  2. llm.py:MODES — fallback, если в БД нет инструкции

Mapping prompt_id → mode:
  consult_base           → consult
  ad_check_base          → ad_check
  document_gen_base      → document_gen
  regulation_lookup_base → regulation
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger("legal-assistant")

_PROMPT_ID_TO_MODE: dict[str, str] = {
    "consult_base":                "consult",
    "ad_check_base":               "ad_check",
    "document_gen_base":           "document_gen",
    "regulation_lookup_base":      "regulation",
    "presentation_base":           "presentation",
    # Под-сценарии document_gen изначально имели собственные строки в
    # mode_instructions, но на 70% дублировали базовый document_gen-промпт
    # (тот сам ведёт логику саммари/претензии/уведомления через секции
    # «РЕЖИМ А/Б/В/Г»). Чтобы не вводить админа в заблуждение тремя почти
    # одинаковыми пунктами в AdminModes, мы все четыре сценария маппим
    # обратно на единый mode='document_gen'. Если в будущем потребуется
    # сценарно-специфичный промпт, добавьте отдельную запись и верните
    # маппинг.
    "contract_analysis_base":      "document_gen",
    "claim_drafting_base":         "document_gen",
    "notice_drafting_base":        "document_gen",
    "document_from_contract_base": "document_gen",
}

_UNKNOWN_PROMPT_FALLBACK_MODE = "consult"


class PromptRegistry:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._cache: dict[str, str] = {}

    async def get_prompt(
        self,
        prompt_id: str,
        legacy_mode: str | None = None,
    ) -> str:
        mode = (
            _PROMPT_ID_TO_MODE.get(prompt_id)
            or legacy_mode
            or _UNKNOWN_PROMPT_FALLBACK_MODE
        )

        # 1. Попробовать из БД
        db_instruction = await self._load_from_db(mode)
        if db_instruction:
            logger.debug(
                "[PromptRegistry] prompt_id=%r → mode=%r: loaded from DB (%d chars)",
                prompt_id, mode, len(db_instruction)
            )
            return db_instruction

        # 2. Fallback к llm.py:MODES
        from llm import MODES
        fallback = MODES.get(mode, {}).get("instruction")
        if fallback:
            logger.warning(
                "[PromptRegistry] prompt_id=%r mode=%r: DB miss, using llm.py fallback",
                prompt_id, mode
            )
            return fallback

        logger.error(
            "[PromptRegistry] prompt_id=%r mode=%r: no instruction found (DB + llm.py)",
            prompt_id, mode
        )
        return ""

    async def _load_from_db(self, mode: str) -> str | None:
        try:
            async with aiosqlite.connect(self._db_path, timeout=10.0) as db:
                async with db.execute(
                    "SELECT instruction FROM mode_instructions WHERE mode = ?",
                    (mode,),
                ) as cur:
                    row = await cur.fetchone()
                    if row and row[0]:
                        return row[0]
        except Exception as exc:
            logger.warning("[PromptRegistry] DB error for mode=%r: %s", mode, exc)
        return None
