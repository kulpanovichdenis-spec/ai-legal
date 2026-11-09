"""
FallbackPolicy — явные правила деградации при ошибках router / LLM (Этап 1).

Правила:
1. Если RouterDecision содержит clarification — вернуть clarification (mode=None).
2. Если scenario_id есть и confidence ≥ MIN_CONFIDENCE — использовать его.
3. Если confidence < MIN_CONFIDENCE — может добавить clarification.
4. При ошибке router / LLM classifier — fallback scenario 'legal_consultation'.
5. Все fallback-события записываются в trace.
"""

from __future__ import annotations

import logging

from schemas_agent import RouterDecision, Intent

logger = logging.getLogger("legal-assistant")

MIN_CONFIDENCE = 0.5
_FALLBACK_SCENARIO = "legal_consultation"
_FALLBACK_MODE = "consult"


class FallbackPolicy:
    def resolve(
        self,
        decision: RouterDecision | None,
        error: Exception | None = None,
    ) -> RouterDecision:
        if error is not None:
            logger.warning(
                "[FallbackPolicy] router error: %s — fallback to %s", error, _FALLBACK_SCENARIO
            )
            return RouterDecision(
                scenario_id=_FALLBACK_SCENARIO,
                legacy_mode=_FALLBACK_MODE,
                intent=None,
                confidence=0.2,
                reason="ошибка роутера, использую консультацию",
                clarification=None,
                alternatives=[],
            )

        if decision is None:
            logger.warning("[FallbackPolicy] no decision — fallback to %s", _FALLBACK_SCENARIO)
            return RouterDecision(
                scenario_id=_FALLBACK_SCENARIO,
                legacy_mode=_FALLBACK_MODE,
                intent=None,
                confidence=0.2,
                reason="решение роутера отсутствует, использую консультацию",
                clarification=None,
                alternatives=[],
            )

        # Если уже есть clarification — возвращаем как есть (сценарий не нужен).
        # Clarification ставит сам router только в одном случае: LLM-классификатор
        # явно вернул mode=null. Это явное намерение задать пользователю вопрос.
        if decision.clarification:
            return decision

        # Если scenario_id пустой — тихий fallback на консультацию.
        # НЕ добавляем clarification: при сбое роутера лучше попытаться ответить
        # как на общую консультацию, чем фрустрировать пользователя уточняющим
        # вопросом, которого он не ожидал.
        if not decision.scenario_id:
            decision.scenario_id = _FALLBACK_SCENARIO
            decision.legacy_mode = _FALLBACK_MODE
            logger.info(
                "[FallbackPolicy] no scenario_id — silent fallback to %s",
                _FALLBACK_SCENARIO,
            )
            return decision

        # Низкий confidence от router больше не приводит к clarification:
        # router сам решает, нужно ли уточнение, и заполняет .clarification.
        # Здесь только логируем для наблюдаемости.
        if decision.confidence < MIN_CONFIDENCE:
            logger.info(
                "[FallbackPolicy] low confidence=%.2f for scenario=%s (proceeding without clarification)",
                decision.confidence, decision.scenario_id,
            )

        return decision
