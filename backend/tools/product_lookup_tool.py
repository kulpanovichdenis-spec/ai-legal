"""
ProductLookupTool — детерминированный поиск регдокументов продукта
(листки-вкладыши, СГР, декларации соответствия) по имени файла.

Allowed scenarios:
  ad_check_general
"""

from __future__ import annotations

import logging
import re

import aiosqlite

from schemas_agent import RequestContext, ScenarioConfig, ToolResult
from tools.base import BaseTool

logger = logging.getLogger("legal-assistant")

_ALLOWED_SCENARIO_IDS = {"ad_check_general"}

# Сигналы наличия продукта для проверки (для should_activate)
_PRODUCT_KEYWORDS = (
    "лс", "бад", "препарат", "лекарств", "косметик", "крем", "таблет",
    "капсул", "раствор", "мазь", "спрей", "вкладыш", "инструкц",
)

# Служебные слова в именах файлов категории leaflet — не являются именами продуктов
_LEAFLET_DOC_TYPES = {
    "листок", "листки", "вкладыш", "вкладыши", "сгр", "охлп",
    "декларация", "декларации", "соответствия",
}

# Формы выпуска — для дизамбигуации препаратов с несколькими формами
_FORM_KEYWORDS = (
    "раствор", "таблетк", "суппозитори", "лиофилизат", "крем", "капсул",
    "сироп", "мазь", "спрей", "капл", "гель", "порошок", "ампул",
)


async def _lookup_product_documents(db: aiosqlite.Connection, message: str) -> list[dict]:
    """Детерминированный поиск регдокументов продукта по имени файла.

    Все листки-вкладыши, СГР по БАД и декларации соответствия лежат в
    category='leaflet' и в original_filename содержат имя продукта (для ЛС —
    также форму выпуска). Это надёжнее семантического поиска, который плохо
    работает с короткими шаблонными документами и редкими брендами.

    Алгоритм:
      1. Извлечь из имени файла продуктовые токены (заглавные слова ≥5 симв.,
         кроме служебных «листок», «вкладыш», «СГР», «декларация»).
      2. Сматчить префикс продуктового токена с сообщением (грубо обходит
         русскую морфологию: «Примерин» → «примерина»).
      3. При нескольких формах выпуска одного препарата — выбрать ту,
         что упомянута в сообщении (раствор/таблетки/суппозитории/…).
         Если форма не указана — вернуть все формы.

    Возвращает полный текст документа: СГР/декларации 1–4 КБ, вкладыши
    12–40 КБ — безопасно по контекст-бюджету и снимает зависимость от
    качества эмбеддинговой модели для самой критичной сверки.
    """
    rows = await db.execute_fetchall(
        "SELECT id, original_filename, category, content FROM documents "
        "WHERE category = 'leaflet'"
    )
    if not rows:
        return []

    msg_lower = message.lower()
    groups: dict[str, list[dict]] = {}

    for r in rows:
        fname = r["original_filename"]
        base = re.sub(r"\.[a-z0-9]+$", "", fname, flags=re.IGNORECASE)
        tokens = re.findall(r"\b[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]{4,}\b", base)
        product_tokens = [t for t in tokens if t.lower() not in _LEAFLET_DOC_TYPES]
        if not product_tokens:
            continue
        main = product_tokens[0]
        prefix = main.lower()[:5]
        if prefix and prefix in msg_lower:
            groups.setdefault(main.lower(), []).append({
                "filename": fname,
                "category": r["category"],
                "content": r["content"],
            })

    result: list[dict] = []
    for docs in groups.values():
        if len(docs) == 1:
            result.append(docs[0])
            continue
        form_matched = [
            d for d in docs
            if any(fw in d["filename"].lower() and fw in msg_lower for fw in _FORM_KEYWORDS)
        ]
        result.extend(form_matched if form_matched else docs)

    return result


class ProductLookupTool(BaseTool):
    id = "product_lookup"
    phase = "pre_rag"  # запускаем до RAG: результат меняет запрос/документы

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def should_activate(
        self,
        ctx: RequestContext,
        scenario: ScenarioConfig,
    ) -> tuple[bool, str]:
        if scenario.id not in _ALLOWED_SCENARIO_IDS:
            return False, f"scenario {scenario.id!r} not in allowed list"

        combined = ((ctx.message or "") + " " + (ctx.file_context or "")).lower()

        for kw in _PRODUCT_KEYWORDS:
            if kw in combined:
                return True, f"keyword match: {kw!r}"

        if ctx.attachments:
            return True, "есть прикреплённые файлы в ad_check контексте"

        return False, "нет сигналов продукта для product_lookup"

    async def execute(
        self,
        ctx: RequestContext,
        scenario: ScenarioConfig,
    ) -> ToolResult:
        try:
            search_text = (ctx.message or "") + " " + (ctx.file_context or "")

            async with aiosqlite.connect(self._db_path, timeout=10.0) as db:
                db.row_factory = aiosqlite.Row
                product_docs = await _lookup_product_documents(db, search_text)

            if not product_docs:
                return ToolResult(
                    tool_id=self.id,
                    activated=False,
                    text_block=None,
                    structured_data=None,
                    reason="регдокументы продукта не найдены",
                )

            return ToolResult(
                tool_id=self.id,
                activated=True,
                text_block=None,  # product_docs встраиваются в RAG напрямую
                structured_data={"product_docs": product_docs},
                reason=f"найдено {len(product_docs)} регдокумент(ов) продукта",
            )

        except Exception as exc:
            logger.error("[ProductLookupTool] execute error: %s", exc, exc_info=True)
            return ToolResult(
                tool_id=self.id,
                activated=False,
                text_block=None,
                structured_data=None,
                reason="ошибка product_lookup tool",
                error=str(exc)[:500],
            )
