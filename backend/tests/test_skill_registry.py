"""
Tests: SkillRegistry

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_skill_registry.py -v
"""

import pytest
from skill_registry import _check_activation, _parse_json_list


def test_activation_always():
    ok, reason = _check_activation("always", "any message")
    assert ok is True
    assert "always" in reason


def test_activation_disabled():
    ok, reason = _check_activation("disabled", "any message")
    assert ok is False
    assert "disabled" in reason


def test_activation_keyword_match():
    ok, reason = _check_activation("keyword:реклама|макет", "проверь рекламный макет")
    assert ok is True
    assert "реклама" in reason or "макет" in reason


def test_activation_keyword_no_match():
    ok, reason = _check_activation("keyword:реклама|макет", "договор поставки")
    assert ok is False


def test_activation_none_treated_as_always():
    ok, reason = _check_activation(None, "any message")
    assert ok is True


def test_activation_unknown_condition_treated_as_always():
    ok, reason = _check_activation("some_unknown_condition", "any message")
    assert ok is True


def test_parse_json_list_json():
    result = _parse_json_list('["claim_drafting", "consumer_return_claim"]')
    assert result == ["claim_drafting", "consumer_return_claim"]


def test_parse_json_list_empty():
    assert _parse_json_list(None) == []
    assert _parse_json_list("") == []


def test_parse_json_list_csv_fallback():
    result = _parse_json_list("claim_drafting, consumer_return_claim")
    assert result == ["claim_drafting", "consumer_return_claim"]


# ── Integration test with real DB ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_skills_production_db():
    """Тест на реальной БД — работает только внутри контейнера."""
    import os
    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("database.db не найден")

    from skill_registry import SkillRegistry
    from scenario_registry import ScenarioRegistry
    from schemas_agent import RequestContext

    registry = SkillRegistry(db_path)
    scenario = ScenarioRegistry().get("ad_check_general")
    ctx = RequestContext(
        request_id="test-001",
        user_id="test",
        message="проверь рекламный макет для препарата",
        conversation_id=None,
        legacy_mode="ad_check",
        channel="web",
        external_user_id=None,
        external_chat_id=None,
        external_message_id=None,
        channel_metadata={},
        attachments=[],
        file_context=None,
        raw_file_context=None,
        history_summary=None,
        metadata={},
    )
    skills = await registry.select_skills(scenario, ctx)
    # В ad_check_general ожидаем чек-лист рекламы
    names = [s.name for s in skills]
    assert any("реклам" in n.lower() or "чек-лист" in n.lower() for n in names), \
        f"Ожидался skill с чек-листом рекламы, получены: {names}"


@pytest.mark.asyncio
async def test_mode_all_skills_are_legacy():
    """mode=all skills попадают как legacy и помечаются в trace."""
    import os
    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("database.db не найден")

    from skill_registry import SkillRegistry
    from scenario_registry import ScenarioRegistry
    from schemas_agent import RequestContext
    from agent_trace import AgentTrace

    registry = SkillRegistry(db_path)
    scenario = ScenarioRegistry().get("legal_consultation")
    ctx = RequestContext(
        request_id="test-002",
        user_id="test",
        message="юридический вопрос",
        conversation_id=None,
        legacy_mode="consult",
        channel="web",
        external_user_id=None,
        external_chat_id=None,
        external_message_id=None,
        channel_metadata={},
        attachments=[],
        file_context=None,
        raw_file_context=None,
        history_summary=None,
        metadata={},
    )
    trace = AgentTrace(request_id="test-002")
    skills = await registry.select_skills(scenario, ctx, trace)
    # mode=all skills должны быть в activated
    activated_ids = [a["id"] for a in trace.skills_activated]
    assert len(activated_ids) > 0, "Ожидались активированные skills"
