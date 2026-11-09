import re
from dataclasses import dataclass
from typing import Literal

from dadata_placeholder import is_anonymized_placeholder, text_contains_id_placeholders

DadataMcpIntent = Literal[
    "documents_verify_party_details",
    "documents_fill_document_requisites",
    "documents_verify_notice_address",
    "documents_counterparty_screening",
    "consultation_counterparty_check",
]

_ALLOWED_DOCUMENTS = {
    "documents_verify_party_details",
    "documents_fill_document_requisites",
    "documents_verify_notice_address",
    "documents_counterparty_screening",
}

_ALLOWED_CONSULTATION = {
    "consultation_counterparty_check",
}

# Ключевые слова для определения intent в режиме «Документы»
_INTENT_CLAIM = ["претензи", "исковое", "требован", "взыскани"]
_INTENT_NOTICE = ["уведомлен", "извещен", "направлен", "отказ от договор", "расторжен"]
_INTENT_SCREENING = ["саммари", "анализ договор", "резюм по договор", "краткое содержани"]

# Ключевые слова для режима «Консультация» — проверка контрагента
_CONSULT_CHECK_KEYWORDS = [
    "проверь контрагент", "проверить контрагент",
    "проверь компани", "проверить компани",
    "проверь инн", "проверить инн",
    "проверь огрн", "проверить огрн",
    "проверь огрнип", "проверить огрнип",
    "проверь ип", "проверить ип",
    "контрагент действующ", "контрагент активн",
    "можно работать с", "работать с этой компани",
    "проверь реквизит", "проверить реквизит",
    "проверь реквизиты контрагент", "проверить реквизиты контрагент",
    "проверить по инн", "проверить по огрн",
    "проверить по огрнип",
    "узнать по инн", "узнать по огрн",
    "проверь организаци", "проверить организаци",
    "проверь юрлиц", "проверить юрлиц",
    "проверь фирм", "проверить фирм",
    "проверь ооо", "проверить ооо",
    "проверь пао", "проверить пао",
    "проверь зао", "проверить зао",
    "проверь ао ", "проверить ао ",
    "проверь предприятие", "проверить предприятие",
    "проверь поставщик", "проверить поставщик",
    "проверь подрядчик", "проверить подрядчик",
    "проверь партнер", "проверить партнер",
]

# Паттерны для извлечения идентификаторов из текста
# ОГРНИП: 15 цифр, начинается с 3
_OGRNIP_RE = re.compile(r'(?<!\d)(3\d{14})(?!\d)')
# ОГРН: 13 цифр, начинается с 1 или 5
_OGRN_RE = re.compile(r'(?<!\d)([15]\d{12})(?!\d)')
# ИНН: ровно 10 или 12 цифр
_INN_RE = re.compile(r'(?<!\d)(\d{10}|\d{12})(?!\d)')


@dataclass
class DadataMcpPolicyInput:
    mode: Literal["documents", "consultation"]
    intent: str
    inn: str = ""
    ogrn: str = ""
    ogrnip: str = ""
    contains_anonymized_placeholders: bool = False


@dataclass
class DadataMcpPolicyDecision:
    allowed: bool
    reason: Literal[
        "allowed",
        "no_identifier",
        "anonymized_placeholder",
        "unsupported_mode",
        "unsupported_intent",
        "deanonymization_risk",
    ]


def can_call_dadata_mcp(inp: DadataMcpPolicyInput) -> DadataMcpPolicyDecision:
    if inp.mode == "documents":
        allowed_intents = _ALLOWED_DOCUMENTS
    elif inp.mode == "consultation":
        allowed_intents = _ALLOWED_CONSULTATION
    else:
        return DadataMcpPolicyDecision(allowed=False, reason="unsupported_mode")

    if inp.intent not in allowed_intents:
        return DadataMcpPolicyDecision(allowed=False, reason="unsupported_intent")

    if inp.contains_anonymized_placeholders:
        return DadataMcpPolicyDecision(allowed=False, reason="anonymized_placeholder")

    for val in (inp.inn, inp.ogrn, inp.ogrnip):
        if val and is_anonymized_placeholder(val):
            return DadataMcpPolicyDecision(allowed=False, reason="anonymized_placeholder")

    if not (inp.inn or inp.ogrn or inp.ogrnip):
        return DadataMcpPolicyDecision(allowed=False, reason="no_identifier")

    return DadataMcpPolicyDecision(allowed=True, reason="allowed")


def extract_identifiers(text: str) -> tuple[str, str, str, bool]:
    """Извлекает (inn, ogrn, ogrnip, has_id_placeholders) из текста."""
    has_placeholders = text_contains_id_placeholders(text)

    ogrnip = ""
    ogrn = ""
    inn = ""

    m = _OGRNIP_RE.search(text)
    if m:
        ogrnip = m.group(1)

    m = _OGRN_RE.search(text)
    if m:
        ogrn = m.group(1)

    # ИНН ищем только если найденное число не является частью уже найденного ОГРН/ОГРНИП
    for m in _INN_RE.finditer(text):
        candidate = m.group(1)
        if candidate != ogrn and candidate != ogrnip:
            inn = candidate
            break

    return inn, ogrn, ogrnip, has_placeholders


def detect_documents_intent(message: str) -> str:
    msg = message.lower()
    if any(kw in msg for kw in _INTENT_CLAIM):
        return "documents_fill_document_requisites"
    if any(kw in msg for kw in _INTENT_NOTICE):
        return "documents_verify_notice_address"
    if any(kw in msg for kw in _INTENT_SCREENING):
        return "documents_counterparty_screening"
    return "documents_verify_party_details"


def should_check_counterparty_consult(message: str) -> bool:
    msg = message.lower()
    return any(kw in msg for kw in _CONSULT_CHECK_KEYWORDS)


# Сообщение — сам идентификатор: пробелы и пунктуация по краям допустимы,
# любой другой текст — нет.
_ID_EDGE_CHARS = " \t\n\r\u00a0.,;:!?\"'«»“”„‟()[]{}<>—–-‐‑/\\_"
_RECENT_DIALOG_MESSAGES = 8
_ASSISTANT_ASK_RE = re.compile(
    r"(укажите|пришлите|напишите|сообщите|назовите|пришли|нужен|нужны|необходим)"
    r".{0,80}(инн|огрн|огрнип)",
    re.IGNORECASE | re.DOTALL,
)

# Карточка контрагента в чате — только для консультации. В документных
# сценариях реквизиты DaData остаются в промпте, но событие клиенту не уходит.
_CARD_HIDDEN_SCENARIOS = frozenset({
    "contract_analysis",
    "claim_drafting",
    "notice_drafting",
    "document_from_contract",
    "consumer_return_claim",
})


def message_is_only_counterparty_id(message: str) -> bool:
    """True, если всё сообщение — один ИНН, ОГРН или ОГРНИП."""
    text = (message or "").strip(_ID_EDGE_CHARS)
    if not text.isdigit():
        return False
    n = len(text)
    if n in (10, 12):
        return True
    if n == 13 and text[0] in "15":
        return True
    if n == 15 and text[0] == "3":
        return True
    return False


def assistant_asked_for_counterparty_id(text: str) -> bool:
    """True, если реплика ассистента просит прислать ИНН/ОГРН/ОГРНИП."""
    msg = text or ""
    if _ASSISTANT_ASK_RE.search(msg):
        return True
    low = msg.lower()
    return (
        "для проверки" in low
        and re.search(r"инн|огрн|огрнип", low) is not None
        and len(low) <= 500
    )


def dialog_requests_counterparty_check(
    history: list[dict] | None,
    current_message: str,
) -> bool:
    """Недавний диалог уже просил проверить контрагента или ассистент
    только что запросил номер."""
    if not history:
        return False
    prior = list(history)
    current = (current_message or "").strip()
    if (
        prior
        and prior[-1].get("role") == "user"
        and (prior[-1].get("content") or "").strip() == current
    ):
        prior = prior[:-1]
    recent = prior[-_RECENT_DIALOG_MESSAGES:]
    for msg in recent:
        if msg.get("role") == "user" and should_check_counterparty_consult(msg.get("content") or ""):
            return True
    for msg in reversed(recent):
        if msg.get("role") == "assistant":
            return assistant_asked_for_counterparty_id(msg.get("content") or "")
    return False


def should_emit_counterparty_card(scenario_id: str | None) -> bool:
    return (scenario_id or "") not in _CARD_HIDDEN_SCENARIOS


def client_counterparty_event(
    scenario_id: str | None,
    check_result: dict | None,
) -> dict | None:
    """SSE-событие карточки. None — клиенту карточку не показываем."""
    if not check_result or not should_emit_counterparty_card(scenario_id):
        return None
    return {"type": "counterparty_check", "data": check_result}


# Признаки дополнительного запроса сверх самой проверки контрагента: вопрос
# или поручение помимо «проверь контрагента X». Если они есть — текстовый ответ
# LLM осмысленен и подавлять его нельзя. Список намеренно про запросы/вопросы
# (глаголы-поручения, вопросительные конструкции), при сомнении сохраняем текст.
_EXTRA_ASK_MARKERS = [
    "?", " ли ",
    "состав", "подготов", "напиши", "сформиру", "разработай", "оформи",
    "оцени", "проанализир", "сравни", "рассчита",
    "скажи", "подскажи", "ответь", "разъясни", "поясни", "объясни", "расскажи",
    "как быть", "что делать", "что будет", "почему", "зачем",
    "какие риск", "какой риск", "чем грозит", "чем рискуем",
    "претензи", "уведомлен",
]


def is_bare_counterparty_check(message: str) -> bool:
    """True, если сообщение — «голая» проверка контрагента: запрос проверки +
    идентификатор/название, без дополнительного вопроса или поручения.

    Для таких запросов карточка контрагента самодостаточна, и текстовый ответ
    LLM лишь дублировал бы её (имя, статус, уровень риска уже на карточке).
    Консервативно: при любом признаке дополнительного запроса — False
    (текст сохраняется, как раньше)."""
    msg = (message or "").strip().lower()
    if not should_check_counterparty_consult(msg):
        return False
    inn, ogrn, ogrnip, _ = extract_identifiers(message)
    if not (inn or ogrn or ogrnip):
        return False
    # Длинное сообщение почти наверняка несёт что-то сверх самой проверки.
    if len(msg) > 160:
        return False
    if any(marker in msg for marker in _EXTRA_ASK_MARKERS):
        return False
    return True
