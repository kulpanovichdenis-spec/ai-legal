"""
Answer critic: пост-проверка чернового ответа LLM на обоснованность
выгруженными RAG-фрагментами.

Применяется ТОЛЬКО в режиме ad_check, где цена галлюцинации высокая
(уверенная ссылка на «нарушение ст. 24 ФЗ-38», которой нет в выгрузке,
может привести бизнес к ошибочному решению о доработке креатива).

Делает ОДИН лёгкий LLM-вызов (router_model).
Возвращает структуру:
  - has_issues: bool
  - issues: list[str] — короткие формулировки конкретных необоснованных
                       утверждений (≤4 пунктов, ≤200 символов каждый).

Если LLM недоступна или вернула мусор — возвращает has_issues=False
(критик не блокирует ответ; в худшем случае получим прежнее поведение).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger("legal-assistant")

_MAX_ANSWER_CHARS = 6000
_MAX_RAG_CHARS = 8000
_MAX_ISSUES = 4


@dataclass
class CritiqueResult:
    has_issues: bool
    issues: list[str]


_SYSTEM_PROMPT = """Ты — критик ответов юридического бота в режиме «Проверка рекламы». Тебе дают:
  1. Вопрос пользователя.
  2. Выгруженные нормативные фрагменты (база знаний, доступная боту).
  3. Черновой ответ бота.

Твоя задача — найти в черновике УТВЕРЖДЕНИЯ, которые НЕ подтверждаются выгруженными фрагментами:
- ссылки на конкретные статьи/пункты ФЗ-38, КоАП, ФЗ-61, ФЗ-323 и т.п., текст которых отсутствует в выгрузке;
- упоминания запретов, разрешений, требований, источник которых не виден в выгрузке;
- вывод «нарушает» / «не нарушает», если основания не следуют из выгруженных норм.

НЕ считай проблемой:
- общие фактические сведения (не требующие нормативной ссылки);
- ответ «недостаточно данных» или «требуется уточнение»;
- ссылку на статью, чей текст ВИДЕН в выгрузке хотя бы частично.

Если необоснованных утверждений нет — верни has_issues=false, issues=[].
Если есть — верни до 4 коротких пунктов (по 1–2 предложения каждый, на русском), где КОНКРЕТНО указывай: «утверждение X не подтверждается выгруженными нормами».

Отвечай СТРОГО JSON-объектом без markdown:
{"has_issues": true|false, "issues": ["...", "..."]}
"""


async def critique_ad_check(
    user_message: str,
    rag_docs: list[dict],
    draft_answer: str,
) -> CritiqueResult:
    """Запустить critic. При любых ошибках возвращает has_issues=False."""
    answer = (draft_answer or "").strip()
    if len(answer) < 100:
        return CritiqueResult(False, [])

    model = settings.router_model or settings.openrouter_model
    if not model:
        return CritiqueResult(False, [])

    rag_block = _build_rag_block(rag_docs)
    if not rag_block:
        # Без выгрузки критика смысла нет — нечего сверять.
        return CritiqueResult(False, [])

    url, api_key, extra_headers = settings.api_for_model(model)
    user_prompt = (
        "ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n"
        f"{user_message[:1500]}\n\n"
        "ВЫГРУЖЕННЫЕ НОРМАТИВНЫЕ ФРАГМЕНТЫ:\n"
        f"{rag_block}\n\n"
        "ЧЕРНОВОЙ ОТВЕТ БОТА:\n"
        f"{answer[:_MAX_ANSWER_CHARS]}"
    )

    try:
        async with httpx.AsyncClient(timeout=float(settings.router_timeout)) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **extra_headers,
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("[CRITIC] LLM call failed: %s — пропускаю проверку", e)
        return CritiqueResult(False, [])

    parsed = _parse(raw)
    if not parsed:
        logger.warning("[CRITIC] невалидный ответ: %r", raw[:200])
        return CritiqueResult(False, [])

    has_issues = bool(parsed.get("has_issues"))
    issues_raw = parsed.get("issues") or []
    issues = []
    if isinstance(issues_raw, list):
        for item in issues_raw[:_MAX_ISSUES]:
            s = str(item).strip()
            if s:
                issues.append(s[:300])

    if has_issues and not issues:
        # Модель сказала «issues есть», но список пустой — не доверяем.
        has_issues = False

    logger.info(
        "[CRITIC] mode=ad_check has_issues=%s n=%d", has_issues, len(issues),
    )
    return CritiqueResult(has_issues, issues)


def _parse(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _build_rag_block(rag_docs: list[dict]) -> str:
    """Собрать компактный блок выгруженных фрагментов с указанием источника.

    Урезаем общий объём до _MAX_RAG_CHARS — критик не должен сам читать
    мегабайт; ему достаточно посмотреть, есть ли нужные нормы в выборке."""
    if not rag_docs:
        return ""
    parts = []
    total = 0
    for d in rag_docs:
        content = (d.get("content") or "").strip()
        if not content:
            continue
        filename = d.get("filename") or d.get("source") or "источник"
        entry = f"— [{filename}] {content}"
        if total + len(entry) > _MAX_RAG_CHARS:
            entry = entry[: _MAX_RAG_CHARS - total]
            parts.append(entry + " …(обрезано)")
            break
        total += len(entry)
        parts.append(entry)
    return "\n\n".join(parts)
