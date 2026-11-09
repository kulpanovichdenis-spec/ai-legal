"""
Tests: ToolRegistry

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_tool_registry.py -v
"""

import pytest
from schemas_agent import RequestContext, ScenarioConfig
from scenario_registry import ScenarioRegistry
from tools.dadata_tool import DaDataTool, _ALLOWED_SCENARIO_IDS as DADATA_ALLOWED
from tools.product_lookup_tool import ProductLookupTool, _ALLOWED_SCENARIO_IDS as PRODUCT_ALLOWED


def make_ctx(message: str = "тест", scenario_id: str = "legal_consultation") -> RequestContext:
    return RequestContext(
        request_id="test",
        user_id="u1",
        message=message,
        conversation_id=None,
        legacy_mode=None,
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


# ── DaDataTool ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dadata_tool_not_in_ad_check():
    tool = DaDataTool()
    scenario = ScenarioRegistry().get("ad_check_general")
    ctx = make_ctx()
    ok, reason = await tool.should_activate(ctx, scenario)
    assert ok is False, f"DaData не должен активироваться в ad_check: {reason}"


@pytest.mark.asyncio
async def test_dadata_tool_with_inn_in_message():
    tool = DaDataTool()
    scenario = ScenarioRegistry().get("legal_consultation")
    ctx = make_ctx(message="проверь контрагента с ИНН 7709709675")
    ok, reason = await tool.should_activate(ctx, scenario)
    assert ok is True, f"DaData должен активироваться при наличии ИНН: {reason}"


@pytest.mark.asyncio
async def test_dadata_tool_in_allowed_scenarios():
    tool = DaDataTool()
    registry = ScenarioRegistry()
    for sid in DADATA_ALLOWED:
        scenario = registry.get(sid)
        ctx = make_ctx(message="договор с ООО Тест ИНН 7709709675")
        ok, reason = await tool.should_activate(ctx, scenario)
        # При наличии ИНН должен активироваться в allowed scenarios
        assert ok is True, f"scenario={sid}: {reason}"


# ── ProductLookupTool ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_product_lookup_not_in_consult():
    tool = ProductLookupTool(db_path="/app/data/database.db")
    scenario = ScenarioRegistry().get("legal_consultation")
    ctx = make_ctx(message="вопрос по ГК РФ")
    ok, reason = await tool.should_activate(ctx, scenario)
    assert ok is False


@pytest.mark.asyncio
async def test_product_lookup_in_ad_check_with_product_keyword():
    tool = ProductLookupTool(db_path="/tmp/test.db")
    scenario = ScenarioRegistry().get("ad_check_general")
    ctx = make_ctx(message="реклама препарата от кашля")
    ok, reason = await tool.should_activate(ctx, scenario)
    assert ok is True, f"Ожидали активацию: {reason}"


# ── ToolRegistry error isolation ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_error_does_not_break_pipeline():
    """tool error должен вернуть ToolResult(error=...), не бросать исключение."""
    import os
    from tool_registry import ToolRegistry
    db_path = "/tmp/nonexistent_db_xyz.db"
    registry = ToolRegistry(db_path)

    scenario = ScenarioRegistry().get("ad_check_general")
    ctx = make_ctx(message="реклама препарата", scenario_id="ad_check_general")
    ctx.message = "реклама препарата БАД"

    tools = await registry.select_tools(scenario, ctx)
    results = await registry.execute_tools(tools, ctx, scenario)
    # Все results должны быть ToolResult, без исключений
    for r in results:
        assert hasattr(r, "tool_id")
        assert hasattr(r, "activated")
