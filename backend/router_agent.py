"""
Router-агент: по сообщению пользователя выбирает scenario_id (Этап 1)
или один из 4 режимов для обратной совместимости.

Логика:
1. Дешёвые эвристики по тексту и именам вложений. Если сигналы однозначны —
   возвращаем решение без вызова LLM.
2. Иначе — один лёгкий запрос к OpenRouter с JSON-ответом по схеме.
3. Если уверенность низкая или сигналы конфликтуют — LLM возвращает
   clarification (короткий уточняющий вопрос на русском), mode = None.

Этап 1: RouterDecision расширен полями scenario_id, intent, alternatives.
  - scenario_id определяется через ScenarioRegistry.from_legacy_mode(mode)
  - legacy field mode сохранён для совместимости
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from config import settings
from consumer_refund import is_consumer_refund_request

logger = logging.getLogger("legal-assistant")

VALID_MODES = ("consult", "ad_check", "document_gen", "regulation")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DOC_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}

# ── Эвристики ─────────────────────────────────────────────────────────

_AD_KEYWORDS = (
    "реклам", "креатив", "баннер", "промо", "листовк", "макет",
    "рассылк", "email-рассыл", "sms-рассыл", "пост в соц",
    "фз-38", "фз 38", "о рекламе",
)
_AD_PRODUCT_HINTS = ("бад", "лекарствен", "препарат", "косметик", "rx", "отс", "жнвлп")

_REGULATION_KEYWORDS = (
    "регламент", "политик", "положени", "комплаенс", "compliance",
    "антикоррупц", "тендерн", "внутренн", "наш регламент", "наша политика",
    "по эдо", "эцп", "рид", "договорной работ",
)

_DOC_GEN_KEYWORDS = (
    "претензи", "уведомлен", "проанализир", "разбер", "проверь договор",
    "составь", "подготовь договор", "дополнительное соглашен", "доп соглашен",
    "расторжен", "акт сверки", "заявление о зачёт", "юридическ заключен",
    "summary по договор", "резюме по договор", "анализ договор",
    # consumer_return triggers (используем стем без окончания для кириллицы)
    "вернуть товар", "возврат товар", "замена товар", "потребител",
    "зозпп", "закон о защите прав потребител",
    # template navigation triggers. «шаблон» и падежи «типовой формы» —
    # одно и то же: подбор типовой формы.
    "типовая форм", "типовые форм", "типовую форм", "типовой форм", "типовых форм",
    "шаблон", "какая форм", "навигатор",
)

def _file_signals(attachment_names: list[str]) -> dict:
    has_image = False
    has_doc = False
    for n in attachment_names:
        ext = Path(n).suffix.lower()
        if ext in IMAGE_EXTS:
            has_image = True
        elif ext in DOC_EXTS:
            has_doc = True
    return {"has_image": has_image, "has_doc": has_doc}


def _kw_match(text: str, keywords: tuple[str, ...]) -> bool:
    """Подстрочный поиск — слова из словарей не содержат regex-метасимволов.

    Раньше использовался `re.search`, что приводило к двум проблемам:
    точка в `"ст\\."` нужно было экранировать вручную, а если бы админ
    добавил ключевое слово с `+`, `?`, `(` — это сломало бы поиск или
    исказило его смысл. Substring-проверка делает поведение очевидным
    и не требует знания regex от того, кто правит словари.
    """
    return any(k in text for k in keywords)


@dataclass
class RouterDecision:
    mode: Optional[str]
    confidence: float
    reason: str
    clarification: Optional[str] = None
    # Этап 1: новые поля для агентской архитектуры
    scenario_id: Optional[str] = None
    intent_id: Optional[str] = None
    alternatives: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Автоматически выводим scenario_id из mode при отсутствии явного значения
        if self.scenario_id is None and self.mode:
            try:
                from scenario_registry import ScenarioRegistry
                self.scenario_id = ScenarioRegistry.get_instance().from_legacy_mode(
                    self.mode
                ).id
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "confidence": self.confidence,
            "reason": self.reason,
            "clarification": self.clarification,
            "scenario_id": self.scenario_id,
            "intent_id": self.intent_id,
            "alternatives": self.alternatives,
        }


# Этап 1: уточнённые heuristics для scenario_id внутри document_gen группы
_CLAIM_KEYWORDS = ("претензи", "требование", "нарушен", "взыскать", "возврат денег")
_NOTICE_KEYWORDS = ("уведомлен", "извещен", "письмо контрагент")
_CONSUMER_RETURN_KEYWORDS = ("возврат товар", "вернуть товар", "замена товар", "потребител", "зозпп", "закон о защите прав потребител")
_TEMPLATE_KEYWORDS = (
    "типовая форм", "типовые форм", "типовую форм", "типовой форм", "типовых форм",
    "шаблон", "какая форм", "навигатор",
)


def _heuristic_route(message: str, attachment_names: list[str]) -> Optional[RouterDecision]:
    """Дешёвые правила. Возвращает None, если сигналы неоднозначны."""
    text = message.lower()
    files = _file_signals(attachment_names)
    refund_request = is_consumer_refund_request(text)

    ad_signal = _kw_match(text, _AD_KEYWORDS) or (
        files["has_image"] and not refund_request and _kw_match(text, _AD_PRODUCT_HINTS)
    )
    reg_signal = _kw_match(text, _REGULATION_KEYWORDS)
    doc_signal = refund_request or _kw_match(text, _DOC_GEN_KEYWORDS) or (
        files["has_doc"] and _kw_match(text, ("договор", "контракт", "соглашен"))
    )

    signals_count = sum([ad_signal, reg_signal, doc_signal])

    if signals_count == 1:
        if ad_signal:
            return RouterDecision(
                mode="ad_check",
                confidence=0.9,
                reason="распознан запрос на проверку рекламы",
                scenario_id="ad_check_general",
                intent_id="check_ad",
            )
        if reg_signal:
            return RouterDecision(
                mode="regulation",
                confidence=0.9,
                reason="запрос про внутренние регламенты компании",
                scenario_id="regulation_lookup",
                intent_id="lookup_regulation",
            )
        if doc_signal:
            # Этап 1: уточняем scenario внутри document_gen группы
            scenario_id, intent_id = _refine_document_scenario(text)
            return RouterDecision(
                mode="document_gen",
                confidence=0.9,
                reason="запрос на работу с договором/документом",
                scenario_id=scenario_id,
                intent_id=intent_id,
            )

    # Несколько конкурирующих сигналов или ничего конкретного — отдаём в LLM.
    return None


def _refine_document_scenario(text: str) -> tuple[str, str]:
    """Уточнить scenario_id внутри document_gen группы."""
    if is_consumer_refund_request(text) or _kw_match(text, _CONSUMER_RETURN_KEYWORDS):
        return "consumer_return_claim", "consumer_return_or_replacement"
    if _kw_match(text, _TEMPLATE_KEYWORDS):
        return "template_form_navigation", "navigate_template_forms"
    # Уведомление проверяем раньше претензии: "нарушение" может быть в обоих,
    # но явное слово "уведомление" является более конкретным сигналом
    if _kw_match(text, _NOTICE_KEYWORDS):
        return "notice_drafting", "draft_notice"
    if _kw_match(text, _CLAIM_KEYWORDS):
        return "claim_drafting", "draft_claim"
    return "contract_analysis", "analyze_contract"


# ── LLM-классификатор ────────────────────────────────────────────────

_SYSTEM_PROMPT = """Ты — диспетчер запросов в юридическом AI-помощнике. По сообщению пользователя ОБЯЗАТЕЛЬНО выбираешь один из четырёх режимов работы. Уточняющих вопросов ты НЕ задаёшь — если запрос неоднозначен, выбираешь наиболее вероятный режим (по умолчанию consult); уточнение при необходимости задаст уже выбранный режим.

Режимы:
- "consult" — общие юридические вопросы по законодательству РФ (ГК, ЗК, ФЗ), вопросы про права, штрафы, нормы, толкование статей; проверка контрагента по ИНН/ОГРН; поиск реквизитов компании; навигация по корпоративным ресурсам. Используется по умолчанию, если запрос не подходит под другие режимы.
- "ad_check" — проверка рекламных материалов (креативов, баннеров, листовок, рассылок) лекарственных средств, БАД и косметики на соответствие ФЗ-38 «О рекламе». Признаки: текст или изображение рекламы, упоминание препарата/БАД/косметики, площадки (интернет, email, SMS, печать).
- "document_gen" — работа с конкретным договором или подготовка юридически значимых документов: анализ договора, претензия, уведомление об отказе/непродлении/расторжении, заявление о зачёте, дополнительное соглашение, подбор типовой формы договора. Обычно сопровождается прикреплённым договором.
- "regulation" — поиск ответа во внутренних регламентах компании (антикоррупционная политика, комплаенс, положение о договорной работе, коммерческая политика, ЭДО, РИД, тендерная политика и т. п.). Признак: пользователь спрашивает «как у нас принято», «что говорит наш регламент», «можно ли по нашей политике».

Правила решения:
1. Если запрос явно подходит под один режим — верни его с confidence ≥ 0.8.
2. Если запрос неоднозначен или слишком общий — выбери наиболее вероятный режим (при сомнении — "consult") с confidence 0.4–0.7. НИКОГДА не возвращай mode=null.
3. Поле reason — короткое (≤ 12 слов) объяснение, почему выбран режим. Например: «вижу прикреплённый договор», «вопрос про штраф по ГК РФ», «проверка контрагента по ИНН».

Отвечай СТРОГО JSON-объектом без markdown-обёртки и комментариев:
{"mode": "consult|ad_check|document_gen|regulation", "confidence": 0.0-1.0, "reason": "..."}
"""


def _continuation_override(current_mode: str) -> str:
    """Блок-приоритет для продолжения диалога. Дописывается в КОНЕЦ system
    prompt (наибольший вес у модели) — удерживает режим, когда короткое
    уточнение содержит слова из другого режима."""
    label = _MODE_LABELS.get(current_mode, current_mode)
    return f"""

ОСОБОЕ ПРАВИЛО ДЛЯ ПРОДОЛЖЕНИЯ ДИАЛОГА (имеет приоритет над правилами выше).
Сейчас идёт продолжение начатого диалога. Текущий режим — "{label}".
По умолчанию следующее сообщение пользователя — это уточнение или дополнение к уже начатой задаче, и режим СОХРАНЯЕТСЯ (верни mode="{current_mode}").
Меняй режим ТОЛЬКО если пользователь явным действием начал ДРУГУЮ задачу: явная просьба «проверь эту рекламу», прислан договор со словами «проанализируй договор», прямой вопрос «что говорит наш регламент».
Короткое сообщение без явной новой просьбы (название предмета, ответ на твой вопрос, уточнение деталей) — это продолжение текущей задачи. Режим НЕ меняй, даже если в сообщении есть слова из другого режима.

Пример: в диалоге шёл подбор типовой формы договора, пользователь пишет «креативная концепция на визуал для препарата». Это НАЗВАНИЕ ПРЕДМЕТА будущего договора (уточнение) — режим подбора документа сохраняется. Это НЕ запрос на проверку рекламы: запрос на проверку рекламы всегда содержит явную просьбу проверить или оценить готовый рекламный материал."""


_MODE_LABELS = {
    "consult": "Юридическая консультация",
    "ad_check": "Проверка рекламы",
    "document_gen": "Работа с договором / документом",
    "regulation": "Поиск по внутренним регламентам",
}


def _build_user_prompt(
    message: str,
    attachment_names: list[str],
    history_tail: list[dict],
    current_mode: Optional[str] = None,
    heuristic_hint: Optional[str] = None,
) -> str:
    parts = []
    if current_mode:
        label = _MODE_LABELS.get(current_mode, current_mode)
        parts.append(
            f"КОНТЕКСТ: это продолжение уже идущего диалога. Текущий режим — "
            f"\"{label}\".\n"
            "Правило выбора режима в продолжении диалога:\n"
            "- Если новое сообщение лишь уточняет, дополняет или конкретизирует "
            "предыдущий запрос (например, называет предмет договора, отвечает на "
            "твой вопрос) — СОХРАНИ текущий режим, даже если в короткой фразе "
            "есть слова из другого режима.\n"
            "- Если пользователь явным действием начал ДРУГУЮ задачу (просит "
            "проверить рекламный материал, прислал договор на анализ, спрашивает "
            "про внутренний регламент) — ПЕРЕКЛЮЧИ режим.\n"
            "Ориентир: есть ли в сообщении новое самостоятельное намерение/"
            "действие — или это лишь уточнение предмета прошлого запроса."
        )
        if heuristic_hint:
            hint_label = _MODE_LABELS.get(heuristic_hint, heuristic_hint)
            parts.append(
                f"Подсказка: по ключевым словам сообщение похоже на режим "
                f"\"{hint_label}\" — но реши сам с учётом контекста выше."
            )
        parts.append("")
    if history_tail:
        parts.append("Последние реплики диалога (для контекста):")
        for h in history_tail[-4:]:
            role = "Пользователь" if h.get("role") == "user" else "Ассистент"
            content = (h.get("content") or "")[:300]
            parts.append(f"- {role}: {content}")
        parts.append("")
    if attachment_names:
        parts.append(f"Прикреплённые файлы: {', '.join(attachment_names)}")
    parts.append("Новое сообщение пользователя:")
    parts.append(message[:2000])
    return "\n".join(parts)


async def _llm_route(
    message: str,
    attachment_names: list[str],
    history_tail: list[dict],
    current_mode: Optional[str] = None,
    heuristic_hint: Optional[str] = None,
) -> RouterDecision:
    model = settings.router_model or settings.openrouter_model
    url, api_key, extra_headers = settings.api_for_model(model)
    user_prompt = _build_user_prompt(
        message, attachment_names, history_tail, current_mode, heuristic_hint
    )
    system_prompt = _SYSTEM_PROMPT
    if current_mode:
        system_prompt += _continuation_override(current_mode)

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
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"[ROUTER] LLM call failed: {e}", exc_info=True)
        return RouterDecision(
            mode="consult",
            confidence=0.3,
            reason="не удалось определить режим, использую консультацию",
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.warning(f"[ROUTER] non-JSON response: {raw[:200]!r}")
            return RouterDecision(
                mode="consult",
                confidence=0.3,
                reason="ответ роутера не распознан, использую консультацию",
            )
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return RouterDecision(
                mode="consult",
                confidence=0.3,
                reason="ответ роутера не распознан, использую консультацию",
            )

    mode = parsed.get("mode")
    # Роутер обязан выбрать режим. Любое null/пустое/неизвестное → consult
    # (если запрос реально требует уточнения, его задаст уже активный режим).
    if mode in (None, "null", "") or mode not in VALID_MODES:
        if mode not in (None, "null", ""):
            logger.warning(f"[ROUTER] unknown mode {mode!r}, falling back to consult")
        mode = "consult"

    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reason = str(parsed.get("reason") or "")[:120]
    clarification = None  # Роутер больше не задаёт уточнений — это делает активный режим.

    # Этап 1: scenario_id определяется автоматически в __post_init__ через from_legacy_mode.
    # Уточнение document-сценария идёт по исходному сообщению пользователя.
    # Раньше парсили reason+clarification от LLM — это давало нестабильность:
    # подсценарий зависел от того, какими словами LLM сформулировала свой reason
    # («вижу запрос на претензию» попадало в _CLAIM_KEYWORDS, иная формулировка —
    # в дефолтный contract_analysis). Надёжнее работать по тексту пользователя.
    scenario_id = None
    intent_id = None
    if mode == "document_gen":
        scenario_id, intent_id = _refine_document_scenario(message.lower())
    elif mode == "ad_check":
        intent_id = "check_ad"
    elif mode == "regulation":
        intent_id = "lookup_regulation"
    elif mode == "consult":
        intent_id = "ask_legal_question"

    return RouterDecision(
        mode=mode,
        confidence=confidence,
        reason=reason or "выбрано на основе текста запроса",
        clarification=clarification,
        scenario_id=scenario_id,  # None → __post_init__ заполнит через from_legacy_mode
        intent_id=intent_id,
    )


# ── Публичный API ────────────────────────────────────────────────────

async def route(
    message: str,
    attachment_names: Optional[list[str]] = None,
    history_tail: Optional[list[dict]] = None,
    scenario_hint: Optional[str] = None,  # Этап 1: подсказка от Bitrix24 команды
    current_scenario: Optional[str] = None,  # сценарий уже идущего диалога
) -> RouterDecision:
    attachment_names = attachment_names or []
    history_tail = history_tail or []

    # Режим текущего диалога (для якоря в LLM-роутере). Выводим из сценария.
    current_mode: Optional[str] = None
    if current_scenario:
        try:
            from scenario_registry import ScenarioRegistry
            current_mode = ScenarioRegistry.get_instance().get(
                current_scenario
            ).legacy_mode_group
        except Exception:
            current_mode = None

    if not settings.router_enabled:
        return RouterDecision(
            mode="consult",
            confidence=1.0,
            reason="роутер выключен, использую консультацию",
        )

    # Подсказка сценария (кнопка сайдбара или команда Bitrix24).
    # Неизвестный id не перехватывает маршрутизацию и не роняет запрос.
    if scenario_hint:
        try:
            from scenario_registry import ScenarioRegistry
            registry = ScenarioRegistry.get_instance()
            if scenario_hint in registry.scenario_ids():
                scenario = registry.get(scenario_hint)
                logger.info(
                    "[ROUTER] scenario_hint=%r → mode=%s", scenario_hint, scenario.legacy_mode_group
                )
                return RouterDecision(
                    mode=scenario.legacy_mode_group,
                    confidence=0.95,
                    reason="сценарий указан явно",
                    scenario_id=scenario_hint,
                )
        except Exception:
            pass

    heuristic = _heuristic_route(message, attachment_names)
    heuristic_hint: Optional[str] = None
    if heuristic is not None:
        # В продолжающемся диалоге короткая фраза-уточнение не должна
        # перекидывать в другой сценарий по одному ключевому слову
        # (напр. «креативная концепция» → «креатив» → ad_check, хотя
        # пользователь продолжает подбор договора). Если эвристика
        # расходится с текущим сценарием — не коротим, отдаём решение
        # LLM-роутеру, который видит историю и якорь текущего режима.
        if (
            current_scenario
            and heuristic.scenario_id != current_scenario
            and not is_consumer_refund_request(message)
        ):
            logger.info(
                "[ROUTER] heuristic scenario=%s ≠ текущий %s в продолжении "
                "диалога — передаю в LLM-роутер",
                heuristic.scenario_id, current_scenario,
            )
            heuristic_hint = heuristic.mode
        else:
            logger.info(
                f"[ROUTER] heuristic mode={heuristic.mode} "
                f"scenario={heuristic.scenario_id} reason={heuristic.reason!r}"
            )
            return heuristic

    decision = await _llm_route(
        message, attachment_names, history_tail, current_mode, heuristic_hint
    )

    # Продолжение диалога в том же режиме: LLM подтвердила текущую группу
    # режима — значит пользователь не сменил задачу. Сохраняем исходный
    # подсценарий, не давая _refine_document_scenario «сползти» на дефолтный
    # contract_analysis (иначе теряются скиллы — напр. Навигатор по формам).
    if (
        current_scenario
        and current_mode
        and decision.mode == current_mode
        and decision.scenario_id != current_scenario
    ):
        logger.info(
            "[ROUTER] продолжение в режиме %s — сохраняю сценарий %s "
            "(LLM предложила %s)",
            current_mode, current_scenario, decision.scenario_id,
        )
        decision.scenario_id = current_scenario

    logger.info(
        f"[ROUTER] llm mode={decision.mode} scenario={decision.scenario_id} "
        f"conf={decision.confidence:.2f} "
        f"reason={decision.reason!r} clarif={'yes' if decision.clarification else 'no'}"
    )
    return decision
