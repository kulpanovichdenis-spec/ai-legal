"""
Tests: MemoryManager

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_memory_manager.py -v
"""

import os
import pytest
import pytest_asyncio
import aiosqlite


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()
    return db_path


@pytest.mark.asyncio
async def test_empty_conversation_id():
    from memory_manager import MemoryManager
    mm = MemoryManager("/irrelevant.db")
    ctx = await mm.load(None, max_messages=10)
    assert ctx.messages == []
    assert ctx.total_messages_in_db == 0
    assert ctx.truncated is False


@pytest.mark.asyncio
async def test_nonexistent_conversation():
    from memory_manager import MemoryManager
    import tempfile
    mm = MemoryManager(str(tempfile.mktemp(suffix=".db")))
    ctx = await mm.load("nonexistent-conv", max_messages=10)
    assert ctx.messages == []
    assert ctx.messages_used == 0


@pytest.mark.asyncio
async def test_loads_last_n_messages(tmp_db):
    async with aiosqlite.connect(tmp_db) as db:
        for i in range(10):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(i), "conv1", "user" if i % 2 == 0 else "assistant", f"message {i}",
                 f"2026-01-01 00:00:{i:02d}")
            )
        await db.commit()

    from memory_manager import MemoryManager
    mm = MemoryManager(tmp_db)
    ctx = await mm.load("conv1", max_messages=5)
    assert ctx.messages_used == 5
    assert ctx.total_messages_in_db == 10
    assert ctx.truncated is True
    assert len(ctx.messages) == 5
    # Последние 5 сообщений (индексы 5-9)
    assert ctx.messages[-1]["content"] == "message 9"


@pytest.mark.asyncio
async def test_no_truncation_when_all_fit(tmp_db):
    async with aiosqlite.connect(tmp_db) as db:
        for i in range(3):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(i), "conv2", "user", f"msg {i}", f"2026-01-01 00:00:{i:02d}")
            )
        await db.commit()

    from memory_manager import MemoryManager
    mm = MemoryManager(tmp_db)
    ctx = await mm.load("conv2", max_messages=50)
    assert ctx.truncated is False
    assert ctx.messages_used == 3


@pytest.mark.asyncio
async def test_truncated_sets_summary(tmp_db):
    """При обрезке истории summary должен содержать информативный текст."""
    async with aiosqlite.connect(tmp_db) as db:
        for i in range(15):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(i), "conv4", "user" if i % 2 == 0 else "assistant", f"msg {i}",
                 f"2026-01-01 00:00:{i:02d}")
            )
        await db.commit()

    from memory_manager import MemoryManager
    mm = MemoryManager(tmp_db)
    ctx = await mm.load("conv4", max_messages=5)

    assert ctx.truncated is True
    assert ctx.summary is not None, "summary должен быть заполнен при truncated=True"
    assert "15" in ctx.summary, "summary должен содержать общее кол-во сообщений"
    assert "5" in ctx.summary, "summary должен содержать кол-во используемых сообщений"


@pytest.mark.asyncio
async def test_no_summary_when_not_truncated(tmp_db):
    """При полной загрузке истории summary должен быть None."""
    async with aiosqlite.connect(tmp_db) as db:
        for i in range(3):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(i), "conv5", "user", f"msg {i}", f"2026-01-01 00:00:0{i}")
            )
        await db.commit()

    from memory_manager import MemoryManager
    mm = MemoryManager(tmp_db)
    ctx = await mm.load("conv5", max_messages=50)

    assert ctx.truncated is False
    assert ctx.summary is None, "summary должен быть None когда история не обрезана"


@pytest.mark.asyncio
async def test_messages_in_chronological_order(tmp_db):
    async with aiosqlite.connect(tmp_db) as db:
        for i in range(5):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(i), "conv3", "user", f"msg {i}", f"2026-01-01 00:00:0{i}")
            )
        await db.commit()

    from memory_manager import MemoryManager
    mm = MemoryManager(tmp_db)
    ctx = await mm.load("conv3", max_messages=10)
    # Порядок должен быть хронологический (old → new)
    contents = [m["content"] for m in ctx.messages]
    assert contents == [f"msg {i}" for i in range(5)]
