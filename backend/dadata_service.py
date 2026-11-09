"""
DaData/MCP логика для проверки контрагентов.

Вынесена из main.py для устранения циклических зависимостей с tools/dadata_tool.py.
Импортируется из tools/dadata_tool.py.execute() напрямую — без прогона через main.
"""

from __future__ import annotations

import logging

from config import settings
from llm import query_llm_json
import dadata_mcp_client as _dadata_client
from dadata_policy import (
    can_call_dadata_mcp,
    detect_documents_intent,
    dialog_requests_counterparty_check,
    extract_identifiers,
    message_is_only_counterparty_id,
    should_check_counterparty_consult,
    DadataMcpPolicyInput,
)
from dadata_comparator import compare_with_contract
from dadata_normalizer import (
    normalize_for_documents,
    normalize_for_consultation,
    normalize_skipped_documents,
    normalize_skipped_consultation,
    normalize_error_documents,
    normalize_error_consultation,
    to_card as dadata_to_card,
)
from dadata_audit import log_dadata_check

logger = logging.getLogger("legal-assistant")

_LEGAL_ASSESSMENT_DISCLAIMER = (
    "Оценка носит информационный характер и основана на открытых данных реестра. "
    "Для принятия юридически значимых решений рекомендуется привлечение квалифицированного юриста."
)

_LEGAL_ASSESSMENT_SYSTEM = """Ты — юридический аналитик. Оцени контрагента по данным ЕГРЮЛ/ЕГРИП и сформируй краткий структурированный вывод.

Ответь строго в формате JSON (без markdown, без пояснений вне JSON):
{
  "verdict": "ok" | "warning" | "danger",
  "riskLevel": "low" | "medium" | "high",
  "risks": ["риск 1", "риск 2"],
  "recommendations": ["рекомендация 1", "рекомендация 2"]
}

Правила:
- "ok" / "low" — компания действующая, нарушений нет, возраст > 1 года
- "warning" / "medium" — недостоверный адрес, учредители с нарушениями, возраст < 1 года, мало сотрудников
- "danger" / "high" — компания ликвидирована / банкрот / в процессе реорганизации, или недостоверные сведения ФНС
- risks — до 4 пунктов, кратко, на русском
- recommendations — до 3 пунктов, кратко, на русском
- Если рисков нет: risks = [], recommendations = ["Явных оснований для отказа в сотрудничестве не выявлено по данным реестра"]"""


async def _get_legal_assessment(card: dict) -> dict | None:
    """Запрашивает у LLM юридическую оценку контрагента по данным карточки."""
    header = card.get("header", {})
    km = card.get("keyMetrics", {})
    limitations = card.get("limitations", [])
    fin = card.get("financials", {})

    ctx = [
        f"Контрагент: {header.get('fullName', '—')}",
        f"Статус: {header.get('status', '—')}",
    ]
    if km.get("ageYears") is not None:
        ctx.append(f"Возраст организации: {km['ageYears']} лет")
    if km.get("charterCapital"):
        ctx.append(f"Уставный капитал: {km['charterCapital']:.0f} руб.")
    if km.get("headcount") is not None:
        ctx.append(f"Сотрудников: {km['headcount']}")
    if limitations:
        ctx.append("Нарушения/ограничения: " + "; ".join(limitations))
    if fin.get("year") and fin.get("revenue") is not None:
        ctx.append(f"Выручка ({fin['year']} г.): {fin['revenue']:.0f} руб.")
    if fin.get("debt") is not None:
        ctx.append(f"Недоимки по налогам: {fin['debt']:.0f} руб.")

    result = await query_llm_json(
        system_prompt=_LEGAL_ASSESSMENT_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(ctx)}],
        model=settings.model_for_mode("consult"),
        timeout=20.0,
    )
    if not result:
        return None
    if result.get("verdict") not in ("ok", "warning", "danger"):
        return None
    if result.get("riskLevel") not in ("low", "medium", "high"):
        result["riskLevel"] = "medium"
    result.setdefault("risks", [])
    result.setdefault("recommendations", [])
    result["disclaimer"] = _LEGAL_ASSESSMENT_DISCLAIMER
    return result


def _default_legal_assessment(card: dict) -> dict:
    """Заглушка при недоступности LLM для юридической оценки."""
    verdict = card.get("summary", {}).get("verdict", "ok")
    risk_map = {"ok": "low", "warning": "medium", "danger": "high"}
    return {
        "verdict": verdict,
        "riskLevel": risk_map.get(verdict, "low"),
        "risks": [],
        "recommendations": ["Проверьте контрагента вручную по данным карточки выше."],
        "disclaimer": _LEGAL_ASSESSMENT_DISCLAIMER,
    }


async def _run_dadata_check(
    mode: str,
    message: str,
    raw_text: str,
    correlation_id: str,
    history: list[dict] | None = None,
) -> tuple[str, dict | None]:
    """Запускает проверку контрагента через DaData/MCP.

    Возвращает (llm_block, card_or_None).
    llm_block — текст для добавления в system prompt.
    card_or_None — структурированный JSON для фронта (только при успешном найденном контрагенте).
    raw_text — оригинальный текст до анонимизации (message + содержимое файлов).
    history — предыдущие реплики диалога: голый ИНН/ОГРН в consult считается
    проверкой, если номер попросили раньше, а не в этой же строке.
    """
    if not settings.dadata_enabled:
        return "", None

    if mode == "document_gen":
        intent = detect_documents_intent(message)
        is_documents = True
    elif mode == "consult":
        followup_id = (
            message_is_only_counterparty_id(message)
            and dialog_requests_counterparty_check(history, message)
        )
        if not should_check_counterparty_consult(message) and not followup_id:
            return "", None
        intent = "consultation_counterparty_check"
        is_documents = False
    else:
        return "", None

    inn, ogrn, ogrnip, has_placeholders = extract_identifiers(raw_text)

    input_types = (
        (["inn"] if inn else [])
        + (["ogrn"] if ogrn else [])
        + (["ogrnip"] if ogrnip else [])
    )

    policy = can_call_dadata_mcp(DadataMcpPolicyInput(
        mode="documents" if is_documents else "consultation",
        intent=intent,
        inn=inn,
        ogrn=ogrn,
        ogrnip=ogrnip,
        contains_anonymized_placeholders=has_placeholders,
    ))

    if not policy.allowed:
        log_dadata_check(intent, input_types, "skipped", policy.reason, correlation_id)
        if is_documents:
            return normalize_skipped_documents(policy.reason), None
        if policy.reason == "no_identifier":
            return normalize_skipped_consultation(policy.reason), None
        return "", None

    try:
        result = await _dadata_client.find_company(inn=inn, ogrn=ogrn, ogrnip=ogrnip)
    except Exception as e:
        logger.error(f"[DADATA_MCP] unexpected error: {e}")
        log_dadata_check(intent, input_types, "error", "unexpected_error", correlation_id)
        if is_documents:
            return normalize_error_documents(), None
        return normalize_error_consultation(), None

    if result.error in ("timeout", "http_error", "connection_error", "parse_error", "rpc_error"):
        log_dadata_check(intent, input_types, "error", result.error, correlation_id)
        if is_documents:
            return normalize_error_documents(), None
        return normalize_error_consultation(), None

    status = "success" if result.found else "not_found"
    log_dadata_check(intent, input_types, status, "", correlation_id)

    counterparty_card = dadata_to_card(result) if result.found else None

    query_type = "ИНН" if inn else ("ОГРН" if ogrn else "ОГРНИП")

    if is_documents:
        comparison = compare_with_contract(result) if result.found else None
        llm_block = normalize_for_documents(result, intent, comparison)
    else:
        llm_block = normalize_for_consultation(result, query_type)

    if not counterparty_card:
        return llm_block, None

    legal_assessment = None
    degraded = False
    for _ in range(2):
        legal_assessment = await _get_legal_assessment(counterparty_card)
        if legal_assessment:
            break
    if not legal_assessment:
        degraded = True
        legal_assessment = _default_legal_assessment(counterparty_card)

    check_response = {
        "schemaVersion": 1,
        "blocks": [
            {"type": "counterparty", "data": counterparty_card},
            {"type": "legalAssessment", "data": legal_assessment},
        ],
        "meta": {"degraded": degraded},
    }
    return llm_block, check_response
