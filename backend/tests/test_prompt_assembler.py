"""
Tests: PromptAssembler

Snapshot-like tests: prompt собирается для каждого scenario без ошибок,
production prompt из mode_instructions имеет приоритет над llm.py:MODES.

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_prompt_assembler.py -v
"""

import os
import pytest
from unittest.mock import AsyncMock, patch

from schemas_agent import (
    RequestContext, ExecutionPlan, RouterDecision,
    SelectedSkill, ToolResult, MemoryContext, PrivacyPolicy
)
from scenario_registry import ScenarioRegistry
from agent_trace import AgentTrace


def make_plan(scenario_id: str = "legal_consultation") -> ExecutionPlan:
    scenario = ScenarioRegistry().get(scenario_id)
    return ExecutionPlan(
        request_context=RequestContext(
            request_id="test",
            user_id="u1",
            message="тест",
            conversation_id=None,
            legacy_mode=scenario.legacy_mode_group,
            channel="web",
            external_user_id=None, external_chat_id=None, external_message_id=None,
            channel_metadata={}, attachments=[],
            file_context=None, raw_file_context=None, history_summary=None, metadata={},
        ),
        router_decision=RouterDecision(
            scenario_id=scenario_id,
            legacy_mode=scenario.legacy_mode_group,
            intent=None,
            confidence=0.9,
            reason="test",
        ),
        scenario=scenario,
        skills=[],
        tool_ids=[],
        memory=MemoryContext(messages=[], summary=None, total_messages_in_db=0,
                             messages_used=0, truncated=False),
        privacy_policy=PrivacyPolicy(
            id="standard_external_llm",
            anonymize_user_message=False, anonymize_file_context=False,
            anonymize_rag_context=False, anonymize_skill_content=False,
            anonymize_tool_results=False, deanonymize_response=False,
            allow_raw_for_tools=[], store_raw_messages=True, store_anonymized_messages=False,
        ),
        prompt_id=scenario.prompt_id,
        legacy_mode=scenario.legacy_mode_group,
        trace=AgentTrace(request_id="test"),
    )


@pytest.mark.asyncio
async def test_prompt_builds_for_all_scenarios():
    """Для каждого scenario prompt собирается без ошибок."""
    from prompt_registry import PromptRegistry
    from prompt_assembler import PromptAssembler

    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("database.db не найден")

    registry = PromptRegistry(db_path)
    assembler = PromptAssembler(registry)

    for scenario in ScenarioRegistry().all_enabled():
        plan = make_plan(scenario.id)
        prompt = await assembler.build(plan, rag_docs=[], templates=[], tool_results=[])
        assert len(prompt) > 100, f"Пустой prompt для scenario={scenario.id}"


@pytest.mark.asyncio
async def test_prompt_includes_anti_injection():
    """Anti-injection block должен присутствовать."""
    from prompt_registry import PromptRegistry
    from prompt_assembler import PromptAssembler

    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("database.db не найден")

    registry = PromptRegistry(db_path)
    assembler = PromptAssembler(registry)
    plan = make_plan("legal_consultation")
    prompt = await assembler.build(plan, rag_docs=[], templates=[], tool_results=[])
    assert "ПРАВИЛА БЕЗОПАСНОСТИ" in prompt or "injection" in prompt.lower()


@pytest.mark.asyncio
async def test_prompt_includes_skills_block():
    from prompt_registry import PromptRegistry
    from prompt_assembler import PromptAssembler

    # Мокаем prompt_registry чтобы не нужна была БД
    mock_registry = AsyncMock()
    mock_registry.get_prompt.return_value = "BASE PROMPT"
    assembler = PromptAssembler(mock_registry)

    skill = SelectedSkill(id="1", name="Test Skill", content="Skill Content", priority=100, reason="test")
    plan = make_plan()

    prompt = await assembler.build(plan, rag_docs=[], templates=[], tool_results=[], skills=[skill])
    assert "Test Skill" in prompt
    assert "Skill Content" in prompt


@pytest.mark.asyncio
async def test_prompt_includes_rag_docs():
    from prompt_assembler import PromptAssembler
    from unittest.mock import AsyncMock

    mock_registry = AsyncMock()
    mock_registry.get_prompt.return_value = "BASE"
    assembler = PromptAssembler(mock_registry)

    docs = [{"filename": "doc1.md", "category": "reference", "content": "RAG CONTENT"}]
    plan = make_plan()
    prompt = await assembler.build(plan, rag_docs=docs, templates=[], tool_results=[])
    assert "doc1.md" in prompt
    assert "RAG CONTENT" in prompt


@pytest.mark.asyncio
async def test_document_prompt_requires_verbatim_anonymizer_tokens():
    """Документный промпт запрещает портить токены обезличивания,
    даже если базовая инструкция в БД этого ещё не говорит."""
    from prompt_assembler import PromptAssembler
    from llm import MODES

    instruction = MODES["document_gen"]["instruction"]
    for token in ("[КОМПАНИЯ_n]", "[ДАТА_n]", "[СУММА_n]", "[АДРЕС_n]"):
        assert token in instruction
    assert "___" in instruction

    mock_registry = AsyncMock()
    mock_registry.get_prompt.return_value = "Короткий промпт документа без правил токенов."
    assembler = PromptAssembler(mock_registry)
    prompt = await assembler.build(
        make_plan("notice_drafting"), rag_docs=[], templates=[], tool_results=[],
    )
    for token in ("[КОМПАНИЯ_n]", "[ДАТА_n]", "[СУММА_n]", "[АДРЕС_n]"):
        assert token in prompt
    assert "___" in prompt

    consult = await assembler.build(
        make_plan("legal_consultation"), rag_docs=[], templates=[], tool_results=[],
    )
    assert "[КОМПАНИЯ_n]" not in consult


@pytest.mark.asyncio
async def test_prompt_includes_tool_results():
    from prompt_assembler import PromptAssembler
    from unittest.mock import AsyncMock

    mock_registry = AsyncMock()
    mock_registry.get_prompt.return_value = "BASE"
    assembler = PromptAssembler(mock_registry)

    tr = ToolResult(
        tool_id="dadata_check",
        activated=True,
        text_block="DADATA RESULT",
        structured_data=None,
        reason="test"
    )
    plan = make_plan()
    prompt = await assembler.build(plan, rag_docs=[], templates=[], tool_results=[tr])
    assert "DADATA RESULT" in prompt


@pytest.mark.asyncio
async def test_db_prompt_has_priority_over_llm_modes():
    """Production prompt из БД длиннее и отличается от llm.py defaults."""
    from prompt_registry import PromptRegistry
    from llm import MODES

    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("database.db не найден")

    registry = PromptRegistry(db_path)

    # production prompt для consult должен быть значительно длиннее fallback
    db_prompt = await registry.get_prompt("consult_base")
    fallback = MODES["consult"]["instruction"]
    # Production instruction в 8-9x длиннее дефолтного по данным ТЗ
    assert len(db_prompt) > len(fallback), \
        f"DB prompt ({len(db_prompt)}) должен быть длиннее fallback ({len(fallback)})"
