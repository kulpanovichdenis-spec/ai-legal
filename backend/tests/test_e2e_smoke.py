"""
E2E smoke tests: 5 основных сценариев через /api/chat

Цель: зафиксировать поведение pipeline и обнаружить регрессии.

Что проверяется для каждого сценария:
  - HTTP 200
  - SSE meta event содержит правильный scenario_id
  - AgentTrace записан в agent_traces (признак orchestrator path)
  - Хотя бы один delta event в ответе

Заметка: раньше проверялось также, что build_system_prompt НЕ вызывался —
эту функцию убрали в Группе 3 рефакторинга, единственный путь сборки промпта
теперь PromptAssembler через Orchestrator.

Тесты с @pytest.mark.xfail описывают поведение ПОСЛЕ спринта и будут
красными, пока соответствующий шаг не реализован.

Мокируются только внешние зависимости:
  - query_llm_stream (LLM вызовы)
  - rag.async_search* (ChromaDB)
  - DaData отключён через отсутствие API-ключей (settings.dadata_enabled == False)

Агентские слои работают реально: Orchestrator, SkillRegistry, MemoryManager.

Запуск:
  docker compose cp backend/tests/test_e2e_smoke.py backend:/app/tests/test_e2e_smoke.py
  docker compose exec backend python -m pytest /app/tests/test_e2e_smoke.py -v
"""

import json
import uuid
import pytest
import pytest_asyncio
import aiosqlite
import httpx
import bcrypt
from unittest.mock import patch, AsyncMock

# Схема тестовой БД больше не дублируется здесь — используется реальная
# система миграций (migrations.apply_pending), чтобы тест не дрейфовал от прода.

# ── Вспомогательные функции ───────────────────────────────────────────────────


def parse_sse(body: bytes) -> list[dict]:
    """Парсит SSE body в список JSON-событий."""
    events = []
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def find_event(events: list[dict], type_: str) -> dict | None:
    return next((e for e in events if e.get("type") == type_), None)


async def _mock_llm_stream(system_prompt, history, mode, *args, **kwargs):
    """Мок LLM: возвращает фиксированный ответ без сетевых вызовов."""
    yield {"type": "delta", "content": "Тестовый ответ."}
    yield {"type": "done"}


# ── Фикстуры ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """Создаёт минимальную SQLite БД: схема + тестовый пользователь + mode_instructions."""
    db_file = str(tmp_path / "smoke.db")
    user_id = str(uuid.uuid4())

    import migrations
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await migrations.apply_pending(db)

        pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
            (user_id, "smokeuser", pw_hash, "user"),
        )

        # mode_instructions нужны PromptRegistry как первичный источник промптов
        from llm import MODES as LLM_MODES
        for mode_id, meta in LLM_MODES.items():
            await db.execute(
                "INSERT OR IGNORE INTO mode_instructions (mode, label, instruction) VALUES (?, ?, ?)",
                (mode_id, meta["label"], meta["instruction"]),
            )

        await db.commit()

    return db_file, user_id


@pytest_asyncio.fixture
async def smoke_client(tmp_db):
    """
    Возвращает (httpx.AsyncClient, jwt_token, db_file).

    Orchestrator реальный, но работает с temp DB.
    LLM, RAG замокированы — нет сетевых вызовов.
    DaData отключён (API keys не заданы в тестах).
    """
    db_file, user_id = tmp_db

    from orchestrator import Orchestrator
    from main import app, create_token
    import main as _main
    import rag as _rag

    orch = Orchestrator(db_path=db_file)
    token = create_token(user_id, "user")
    mock_rag = AsyncMock(return_value=[])

    with (
        patch.object(_main, "DB_PATH", db_file),
        patch.object(_main, "_orchestrator", orch),
        patch.object(_main, "query_llm_stream", side_effect=_mock_llm_stream),
        patch.object(_rag, "async_search", mock_rag),
        patch.object(_rag, "async_search_dual", mock_rag),
        patch.object(_rag, "async_search_multi_product", mock_rag),
        patch.object(_rag, "needs_reindex", return_value=False),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, token, db_file


# ── Базовые хелперы для тестов ────────────────────────────────────────────────


async def _chat(client, token, mode: str, message: str) -> tuple[list[dict], dict | None]:
    """POST /api/chat, возвращает (all_events, meta_event)."""
    resp = await client.post(
        "/api/chat",
        data={"message": message, "mode": mode},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (
        f"Ожидали 200, получили {resp.status_code}: {resp.text[:300]}"
    )
    events = parse_sse(resp.content)
    meta = find_event(events, "meta")
    return events, meta


async def _read_trace(db_file: str, conversation_id: str) -> dict | None:
    """Читает AgentTrace из agent_traces по conversation_id."""
    async with aiosqlite.connect(db_file) as db:
        async with db.execute(
            "SELECT trace_json FROM agent_traces WHERE conversation_id = ?",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return json.loads(row[0])
    return None


def _assert_orchestrator_path(events: list[dict], meta: dict | None, trace: dict | None,
                               expected_scenario_id: str) -> None:
    """Общие assertions для всех smoke тестов."""
    # meta event есть
    assert meta is not None, "SSE не содержит meta event"
    assert meta.get("conversation_id"), "meta event не содержит conversation_id"

    # scenario_id правильный
    assert meta.get("scenario_id") == expected_scenario_id, (
        f"Ожидали scenario_id={expected_scenario_id!r}, "
        f"получили: {meta.get('scenario_id')!r}"
    )

    # AgentTrace записан в БД (признак orchestrator path)
    assert trace is not None, (
        "AgentTrace не записан в agent_traces — "
        "это означает, что _plan == None и pipeline пошёл по legacy-ветке"
    )
    assert trace.get("scenario_id") == expected_scenario_id, (
        f"Trace.scenario_id={trace.get('scenario_id')!r} != {expected_scenario_id!r}"
    )

    # Хотя бы один delta event
    deltas = [e for e in events if e.get("type") == "delta"]
    assert len(deltas) > 0, "Нет delta событий в ответе"


# ── Тест 1: legal_consultation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_legal_consultation(smoke_client):
    """
    Сценарий: общий юридический вопрос.
    mode=consult → scenario_id=legal_consultation → orchestrator path.
    """
    client, token, db_file = smoke_client

    # build_system_prompt (legacy путь) удалён из llm.py в Группе 3 рефакторинга
    # — единственный путь сборки промпта это PromptAssembler через Orchestrator.
    # Раньше тут стоял spy, проверявший что он НЕ вызван; сейчас функции нет.
    events, meta = await _chat(client, token, mode="consult",
                               message="что такое оферта по ГК РФ?")

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    _assert_orchestrator_path(events, meta, trace, "legal_consultation")

    # Trace содержит агентские данные
    assert trace.get("legacy_mode") == "consult"
    assert trace.get("privacy_policy_id") is not None


# ── Тест 2: ad_check_general ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_ad_check_general(smoke_client):
    """
    Сценарий: проверка рекламы продукта.
    mode=ad_check → scenario_id=ad_check_general → orchestrator path.
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="ad_check",
        message="проверь рекламный баннер препарата от кашля",
    )

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    _assert_orchestrator_path(events, meta, trace, "ad_check_general")
    assert trace.get("legacy_mode") == "ad_check"


@pytest.mark.asyncio
async def test_smoke_ad_check_product_lookup_selected(smoke_client):
    """
    ToolRegistry.select_tools() уже выбирает product_lookup для ad_check.
    Тест проверяет фазу SELECTION (запись в trace происходит в Orchestrator.build_plan).
    Фаза EXECUTION через ToolRegistry.execute_tools() — проверяется в Шаге 3 спринта.
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="ad_check",
        message="реклама препарата Примерин таблетки",
    )

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    assert trace is not None, "AgentTrace не записан"

    activated_tool_ids = [t["id"] for t in (trace.get("tools_activated") or [])]
    assert "product_lookup" in activated_tool_ids, (
        f"product_lookup не выбран на фазе selection. "
        f"Активированные: {activated_tool_ids}"
    )


@pytest.mark.asyncio
async def test_smoke_ad_check_product_lookup_executed(smoke_client):
    """
    Шаги 3+5 выполнены: product_lookup выполняется через ToolRegistry.execute_tools(),
    логика _lookup_product_documents перенесена в ProductLookupTool (больше нет в main.py).

    Проверяем через AsyncMock на ProductLookupTool.execute — счётчик вызовов.
    """
    from tools.product_lookup_tool import ProductLookupTool
    from schemas_agent import ToolResult
    from unittest.mock import AsyncMock
    client, token, db_file = smoke_client

    mock_execute = AsyncMock(return_value=ToolResult(
        tool_id="product_lookup", activated=False,
        text_block=None, structured_data=None, reason="spy",
    ))

    with patch.object(ProductLookupTool, "execute", mock_execute):
        events, meta = await _chat(
            client, token, mode="ad_check",
            message="реклама препарата Примерин таблетки",
        )

    # ProductLookupTool.execute() должен вызываться через ToolRegistry.execute_tools()
    assert mock_execute.call_count >= 1, (
        f"ProductLookupTool.execute() не вызывался — "
        f"product_lookup не выполняется через ToolRegistry.execute_tools()."
    )


# ── Тест 3: contract_analysis ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_contract_analysis(smoke_client):
    """
    Сценарий: анализ договора.
    mode=document_gen → scenario_id=contract_analysis → orchestrator path.
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="document_gen",
        message="проанализируй договор поставки на предмет рисков",
    )

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    _assert_orchestrator_path(events, meta, trace, "contract_analysis")
    assert trace.get("legacy_mode") == "document_gen"


@pytest.mark.asyncio
async def test_smoke_contract_analysis_prompt_self_contained():
    """
    Промпт contract_analysis в production БД должен быть самодостаточным
    и не ссылаться на несуществующие внешние файлы.
    Критерий 9 спринта: contract_checklist не нужен, prompt не ссылается на него.

    Использует production DB напрямую (не тестовую tmp_db).
    Пропускается если production DB недоступна.
    """
    import os
    from prompt_registry import PromptRegistry

    db_path = os.getenv("DB_PATH", "/app/data/database.db")
    if not os.path.exists(db_path):
        pytest.skip("production database.db не найден — пропускаем")

    registry = PromptRegistry(db_path)
    prompt = await registry.get_prompt("contract_analysis_base")

    assert len(prompt) > 50, (
        "Промпт contract_analysis пустой или слишком короткий в production БД"
    )
    assert "contract_checklist" not in prompt.lower(), (
        "Промпт contract_analysis ссылается на contract_checklist — "
        "загрузите файл через /api/documents/upload или уберите ссылку"
    )


# ── Тест 4: claim_drafting ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_claim_drafting(smoke_client):
    """
    Сценарий: подготовка претензии.
    mode=auto + ключевые слова → heuristic router → scenario_id=claim_drafting.
    Не требует вызова LLM для routing (хeuristic match).
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="auto",
        message="составь претензию к контрагенту о нарушении договора поставки",
    )

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    _assert_orchestrator_path(events, meta, trace, "claim_drafting")
    assert trace.get("legacy_mode") == "document_gen"


@pytest.mark.asyncio
async def test_smoke_claim_drafting_no_dadata_result(smoke_client):
    """
    DaData выбирается selection-фазой для document_gen сценариев,
    но при отключённом DaData (нет API ключей) реальная проверка не выполняется.
    Признак: нет counterparty_check события в SSE ответе.
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="auto",
        message="составь претензию к ООО 'Поставщик' за нарушение сроков",
    )

    # counterparty_check event появляется только при успешном запросе к DaData API
    counterparty_event = find_event(events, "counterparty_check")
    assert counterparty_event is None, (
        "counterparty_check event появился, хотя DaData API ключи не заданы. "
        "Это неожиданно — значит settings.dadata_enabled=True в тесте."
    )


# ── Тест 5: regulation_lookup ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_regulation_lookup(smoke_client):
    """
    Сценарий: поиск по внутренним регламентам.
    mode=regulation → scenario_id=regulation_lookup → orchestrator path.
    Для regulation tools не должны активироваться (allowed_tool_ids=[]).
    """
    client, token, db_file = smoke_client

    events, meta = await _chat(
        client, token, mode="regulation",
        message="что говорит наш регламент о командировочных расходах",
    )

    trace = await _read_trace(db_file, meta["conversation_id"]) if meta else None
    _assert_orchestrator_path(events, meta, trace, "regulation_lookup")
    assert trace.get("legacy_mode") == "regulation"

    # Для regulation_lookup нет allowed tools — trace должен быть чистым
    assert trace.get("tools_activated") == [], (
        f"В regulation_lookup не должно быть активных tools: "
        f"{trace.get('tools_activated')}"
    )
