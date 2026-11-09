"""
Query rewriter: превращает сообщение пользователя в 2–4 поисковых формулировки
с юридической терминологией, чтобы закрыть разрыв между разговорной лексикой
пользователя и формальным языком нормативки/регламентов.

Делает ОДИН быстрый LLM-вызов (router_model) перед RAG.
Результат — список запросов:
  - первый: широкая/тематическая формулировка (high-level);
  - остальные: узкие, с конкретными терминами/статьями/сущностями (low-level).

Если LLM недоступна или вернула мусор — fallback к исходному сообщению
(старое поведение однозапросного RAG).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("legal-assistant")

# Минимальная длина сообщения, для которой имеет смысл переписывать.
# Очень короткие фразы («да», «продолжай», «ок») трактуются как продолжение
# предыдущего ответа — переписывать нечего, отдаём исходник.
_MIN_LEN_FOR_REWRITE = 8

# Максимум поисковых запросов в выдаче (бережём латентность).
_MAX_QUERIES = 4


_SYSTEM_PROMPT = """Ты — модуль переписывания запросов для юридической RAG-системы организации. Получаешь сообщение пользователя и возвращаешь 2–4 поисковые формулировки на русском языке, оптимизированные для поиска по нормативным актам РФ и внутренним регламентам компании.

Стратегия:
- Запрос №1 — широкая тематическая формулировка с ключевыми терминами области (что регулируется, какая отрасль права). Используй формальную юридическую лексику.
- Запросы №2–4 — узкие конкретные формулировки: точные термины («противопоказания», «возрастные ограничения», «маркировка интернет-рекламы»), номера статей и законов, наименования продуктов/документов, ключевые сущности из запроса.

Правила:
- Каждая формулировка — 4–15 слов, без вопросительных знаков.
- НЕ копируй сообщение пользователя дословно — используй язык нормативки.
- НЕ выдумывай факты (статьи, продукты, номера) — переформулируй только то, что есть в запросе.
- Если запрос явно тривиальный (приветствие, «спасибо», «помоги»), верни массив с одним элементом — исходным сообщением.

Отвечай СТРОГО JSON-объектом без markdown-обёртки:
{"queries": ["...", "..."]}
"""


TEMPLATE_FORM_USER_PREFIX = (
    "Ищу типовую форму договора. Шаблон и типовая форма — одно и то же. "
)


def with_template_form_context(message: str) -> str:
    """Префикс для сценария подбора формы: агент видит синоним, даже если
    пользователь написал только вид договора."""
    text = message or ""
    if "Шаблон и типовая форма — одно и то же." in text:
        return text
    return TEMPLATE_FORM_USER_PREFIX + text


def _scenario_hint(scenario_id: Optional[str]) -> str:
    """Лёгкая подсказка контекста для лучшего переписывания."""
    hints = {
        "legal_consultation": "Контекст: общая юридическая консультация (ГК РФ, договорное, корпоративное право).",
        "regulation_lookup": "Контекст: поиск во внутренних регламентах компании (договорная работа, комплаенс, ЭДО, тендеры, антикоррупция).",
        "ad_check_general": "Контекст: проверка рекламы ЛС/БАД/косметики на соответствие ФЗ-38 «О рекламе».",
        "contract_analysis": "Контекст: анализ договора, оценка рисков, правовая квалификация условий.",
        "claim_drafting": "Контекст: подготовка претензии по нарушению обязательств.",
        "notice_drafting": "Контекст: подготовка уведомления (отказ, расторжение, изменение условий).",
        "document_from_contract": "Контекст: подготовка документа на основе договора.",
        "template_form_navigation": (
            "Контекст: пользователь ищет типовую форму договора. "
            "«Шаблон» и «типовая форма» — одно и то же, ищи оба варианта."
        ),
        "consumer_return_claim": "Контекст: возврат/замена товара потребителю, ЗОЗПП.",
    }
    return hints.get(scenario_id or "", "")


async def rewrite_for_search(
    message: str,
    scenario_id: Optional[str] = None,
) -> list[str]:
    """Возвращает 2–4 поисковых формулировки. Fallback — список с одним
    элементом (исходное сообщение)."""
    msg = (message or "").strip()
    if len(msg) < _MIN_LEN_FOR_REWRITE:
        return [msg] if msg else []

    model = settings.router_model or settings.openrouter_model
    if not model:
        return [msg]

    url, api_key, extra_headers = settings.api_for_model(model)
    hint = _scenario_hint(scenario_id)
    user_prompt = (
        (hint + "\n\n" if hint else "")
        + "Сообщение пользователя:\n"
        + msg[:1500]
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
                    "max_tokens": 300,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("[QUERY_REWRITER] LLM call failed: %s — fallback к исходному", e)
        return [msg]

    queries = _parse_queries(raw)
    if not queries:
        logger.warning("[QUERY_REWRITER] пустой/невалидный ответ: %r — fallback", raw[:200])
        return [msg]

    queries = _dedupe_and_limit(queries)
    logger.info(
        "[QUERY_REWRITER] %d formulations: %s",
        len(queries), " | ".join(q[:60] for q in queries),
    )
    return queries


def _parse_queries(raw: str) -> list[str]:
    """Распарсить JSON-ответ LLM, вытащить queries."""
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
    if not isinstance(parsed, dict):
        return []
    queries = parsed.get("queries")
    if not isinstance(queries, list):
        return []
    return [str(q).strip() for q in queries if str(q).strip()]


def _dedupe_and_limit(queries: list[str]) -> list[str]:
    """Убрать пустые/повторяющиеся, ограничить _MAX_QUERIES."""
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(q)
        if len(result) >= _MAX_QUERIES:
            break
    return result
