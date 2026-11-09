"""
Tests: Router → scenario_id mapping (smoke/regression)

Проверяем эвристики роутера на конкретных фразах.
Эти тесты — smoke/regression, а не representative dataset.

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_router_scenarios.py -v
"""

import pytest
from router_agent import _heuristic_route, RouterDecision, _refine_document_scenario


def test_contract_analysis_from_heuristic():
    d = _heuristic_route("Проверь договор поставки на риски", [])
    assert d is not None
    assert d.mode == "document_gen"
    assert d.scenario_id is not None


def test_claim_drafting_from_heuristic():
    d = _heuristic_route("Подготовь претензию контрагенту за нарушение договора", [])
    assert d is not None
    assert d.mode == "document_gen"
    assert d.scenario_id == "claim_drafting"


def test_ad_check_from_heuristic():
    d = _heuristic_route("Проверь рекламный макет для БАД", [])
    assert d is not None
    assert d.mode == "ad_check"
    assert d.scenario_id == "ad_check_general"


def test_regulation_from_heuristic():
    d = _heuristic_route("Что говорит внутренний регламент об ЭДО?", [])
    assert d is not None
    assert d.mode == "regulation"
    assert d.scenario_id == "regulation_lookup"


def test_consumer_return_heuristic():
    d = _heuristic_route("Как вернуть товар потребителю?", [])
    assert d is not None
    assert d.scenario_id == "consumer_return_claim"


def test_notice_drafting_heuristic():
    d = _heuristic_route("Составь уведомление о нарушении договора", [])
    assert d is not None
    assert d.mode == "document_gen"
    assert d.scenario_id == "notice_drafting"


def test_template_form_navigation_heuristic():
    d = _heuristic_route("Какая типовая форма нужна для соглашения о конфиденциальности?", [])
    assert d is not None
    assert d.mode == "document_gen"
    assert d.scenario_id == "template_form_navigation"


def test_template_form_shablon_synonym():
    d = _heuristic_route("Нужен шаблон договора с лекторами", [])
    assert d is not None
    assert d.scenario_id == "template_form_navigation"


def test_template_form_plural_inflection():
    d = _heuristic_route("Какие типовые формы есть для договора с лекторами?", [])
    assert d is not None
    assert d.scenario_id == "template_form_navigation"


def test_template_form_context_prefix_keeps_user_text():
    from query_rewriter import with_template_form_context
    prefixed = with_template_form_context("нужен договор поставки")
    assert prefixed.endswith("нужен договор поставки")
    assert "типовую форму" in prefixed
    assert "Шаблон и типовая форма — одно и то же." in prefixed
    assert with_template_form_context(prefixed) == prefixed


def test_no_signals_returns_none():
    d = _heuristic_route("помоги мне пожалуйста", [])
    assert d is None  # отдаём в LLM


def test_consult_default_no_signals():
    # Без явных сигналов — heuristic не работает, должен вернуть None
    d = _heuristic_route("Какие риски по статье 450 ГК РФ?", [])
    # consult имеет weak сигнал "статья" — может быть None или consult
    assert d is None or (d.mode == "consult" or d.mode == "document_gen")


def test_router_decision_has_scenario_id():
    from scenario_registry import ScenarioRegistry
    d = RouterDecision(
        mode="consult",
        confidence=0.9,
        reason="test",
    )
    # __post_init__ должен заполнить scenario_id
    assert d.scenario_id == "legal_consultation"


def test_router_decision_ad_check_scenario():
    d = RouterDecision(
        mode="ad_check",
        confidence=0.9,
        reason="test",
        scenario_id="ad_check_general",
    )
    assert d.scenario_id == "ad_check_general"


def test_refine_document_scenario_claim():
    sid, intent = _refine_document_scenario("подготовь претензию контрагенту")
    assert sid == "claim_drafting"
    assert intent == "draft_claim"


def test_refine_document_scenario_consumer_return():
    sid, intent = _refine_document_scenario("возврат товара потребителю")
    assert sid == "consumer_return_claim"


def test_refine_document_scenario_default():
    sid, intent = _refine_document_scenario("договор поставки")
    assert sid == "contract_analysis"
    assert intent == "analyze_contract"


@pytest.mark.asyncio
async def test_known_scenario_hint_forces_scenario():
    from router_agent import route
    decision = await route(
        "Подготовь претензию контрагенту за нарушение договора",
        scenario_hint="regulation_lookup",
    )
    assert decision.scenario_id == "regulation_lookup"
    assert decision.mode == "regulation"


@pytest.mark.asyncio
async def test_unknown_scenario_hint_ignored():
    from router_agent import route
    decision = await route(
        "Подготовь претензию контрагенту за нарушение договора",
        scenario_hint="not_a_scenario",
    )
    assert decision.scenario_id == "claim_drafting"
