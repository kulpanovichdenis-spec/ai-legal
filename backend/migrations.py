"""
Единая система миграций SQLite.

Раньше схема создавалась и мигрировала в трёх местах:
  - main.py startup: CREATE TABLE IF NOT EXISTS + два набора ALTER в try/except
  - skill_registry.py: upgrade_skills_table — дублировал ALTER для skills
Без таблицы версий миграции выполнялись на каждом старте, а ошибки глушились
голым `except: pass`.

Теперь:
  - `_schema_version` хранит id применённых миграций
  - MIGRATIONS — упорядоченный список (id, callable)
  - apply_pending() применяет только новые миграции, каждую в своей транзакции
  - миграции идемпотентны (для ALTER — проверка существования колонки),
    поэтому первый запуск на «старой» БД с уже полной схемой просто
    зафиксирует версии, ничего не ломая.

Миграции делают ТОЛЬКО DDL. Сидинг данных (mode_instructions, admin/dashboard)
остаётся в startup отдельно.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger("legal-assistant")


# ── Хелперы идемпотентности ───────────────────────────────────────────

async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return {row[1] for row in await cur.fetchall()}


async def _add_column_if_missing(
    db: aiosqlite.Connection, table: str, column: str, ddl: str,
) -> None:
    """ALTER TABLE ADD COLUMN, если колонки ещё нет (SQLite не знает IF NOT EXISTS)."""
    if column not in await _columns(db, table):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        logger.info("[MIGRATION] %s.%s добавлена", table, column)


# ── Миграции ──────────────────────────────────────────────────────────

async def m001_initial_schema(db: aiosqlite.Connection) -> None:
    """Базовые таблицы. CREATE TABLE IF NOT EXISTS — безопасно на существующей БД."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            mode TEXT NOT NULL DEFAULT 'consult',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'regulation',
            mode TEXT NOT NULL DEFAULT 'all',
            description TEXT,
            content TEXT,
            uploaded_by TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'all',
            is_active INTEGER DEFAULT 1,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT PRIMARY KEY,
            revoked_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mode_instructions (
            mode TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            instruction TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            action TEXT NOT NULL,
            target TEXT,
            details TEXT,
            ip TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_stats (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            mode TEXT NOT NULL,
            sub_category TEXT,
            skill_names TEXT,
            time_minutes INTEGER NOT NULL DEFAULT 30,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)


async def m002_skills_documents_mode(db: aiosqlite.Connection) -> None:
    """Колонки mode/time_category для старых БД, где их ещё нет."""
    await _add_column_if_missing(
        db, "skills", "mode", "mode TEXT NOT NULL DEFAULT 'all'")
    await _add_column_if_missing(
        db, "documents", "mode", "mode TEXT NOT NULL DEFAULT 'all'")
    await _add_column_if_missing(
        db, "skills", "time_category", "time_category TEXT DEFAULT NULL")


async def m003_agentic_columns(db: aiosqlite.Connection) -> None:
    """Колонки агентской архитектуры (Этап 1) для skills и conversations."""
    await _add_column_if_missing(
        db, "skills", "scenario_ids", "scenario_ids TEXT DEFAULT NULL")
    await _add_column_if_missing(
        db, "skills", "activation_condition", "activation_condition TEXT DEFAULT 'always'")
    await _add_column_if_missing(
        db, "skills", "priority", "priority INTEGER DEFAULT 100")
    await _add_column_if_missing(
        db, "skills", "required", "required INTEGER DEFAULT 0")
    await _add_column_if_missing(
        db, "skills", "exclusive_with", "exclusive_with TEXT DEFAULT NULL")
    await _add_column_if_missing(
        db, "skills", "depends_on", "depends_on TEXT DEFAULT NULL")
    await _add_column_if_missing(
        db, "conversations", "scenario_id", "scenario_id TEXT DEFAULT NULL")


async def m004_agentic_tables(db: aiosqlite.Connection) -> None:
    """Таблицы agent_traces и channel_sessions."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            conversation_id TEXT,
            user_id TEXT,
            scenario_id TEXT,
            legacy_mode TEXT,
            trace_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS channel_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            external_chat_id TEXT,
            internal_user_id TEXT,
            conversation_id TEXT,
            scenario_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(channel, external_user_id, external_chat_id)
        );
    """)


async def m007_projects(db: aiosqlite.Connection) -> None:
    """Таблица публичных проектов «Презентация для бизнеса».

    Каждая запись — одна 5-слайдовая презентация результата юр.работы.
    Публикуется через команду «опубликовать» в чате; редактируется и
    управляется через AdminProjects (admin-only).

    slug — URL-фрагмент для публичной страницы /projects/{slug};
    UNIQUE для предотвращения коллизий при автогенерации.
    """
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            slide_task TEXT,
            slide_complexity TEXT,
            slide_action TEXT,
            slide_effect TEXT,
            slide_next TEXT,
            author_id TEXT,
            author_name TEXT,
            conversation_id TEXT,
            status TEXT NOT NULL DEFAULT 'published',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
        CREATE INDEX IF NOT EXISTS idx_projects_conv ON projects(conversation_id);
    """)


async def m006_documents_effective_date(db: aiosqlite.Connection) -> None:
    """Поле даты вступления в силу для документов (регламентов / нормативки).

    Используется как tie-breaker в RAG diversity: при конкуренции за слот
    из одного и того же файла предпочитаем более свежую версию. Чтобы
    эффект проявился, в корпус должны быть загружены пересекающиеся версии
    одного документа с заполненной effective_date.
    """
    await _add_column_if_missing(
        db, "documents", "effective_date", "effective_date TEXT DEFAULT NULL")


async def m008_messages_check_result(db: aiosqlite.Connection) -> None:
    """Карточка проверки контрагента (DaData), привязанная к сообщению.

    Раньше карточка отдавалась только SSE-событием и терялась при перезагрузке
    диалога. Храним её JSON рядом с ответом ассистента, чтобы при повторном
    открытии диалога карточка отрисовывалась так же, как в live-режиме."""
    await _add_column_if_missing(
        db, "messages", "check_result", "check_result TEXT DEFAULT NULL")


async def m005_conversation_files(db: aiosqlite.Connection) -> None:
    """Документы, загруженные в диалог.

    Хранит извлечённый текст прикреплённых файлов, привязанный к диалогу,
    чтобы переподставлять документы в контекст LLM на последующих сообщениях
    (а не только в том сообщении, где файл загрузили)."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_files (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_conversation_files_conv
            ON conversation_files(conversation_id);
    """)


# Упорядоченный список миграций. Добавлять новые — только в конец.
MIGRATIONS: list[tuple[str, callable]] = [
    ("001_initial_schema", m001_initial_schema),
    ("002_skills_documents_mode", m002_skills_documents_mode),
    ("003_agentic_columns", m003_agentic_columns),
    ("004_agentic_tables", m004_agentic_tables),
    ("005_conversation_files", m005_conversation_files),
    ("006_documents_effective_date", m006_documents_effective_date),
    ("007_projects", m007_projects),
    ("008_messages_check_result", m008_messages_check_result),
]


async def apply_pending(db: aiosqlite.Connection) -> None:
    """Применить все ещё не применённые миграции по порядку.

    Каждая миграция фиксируется отдельной транзакцией. При ошибке —
    rollback и проброс исключения (приложение не должно стартовать
    с частично применённой схемой).
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            id TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    async with db.execute("SELECT id FROM _schema_version") as cur:
        applied = {row[0] for row in await cur.fetchall()}

    pending = [(mid, fn) for mid, fn in MIGRATIONS if mid not in applied]
    if not pending:
        logger.info("[MIGRATION] схема актуальна, миграций не требуется")
        return

    for migration_id, fn in pending:
        try:
            await fn(db)
            await db.execute(
                "INSERT INTO _schema_version (id) VALUES (?)", (migration_id,)
            )
            await db.commit()
            logger.info("[MIGRATION] применена %s", migration_id)
        except Exception:
            await db.rollback()
            logger.critical("[MIGRATION] ошибка в %s", migration_id, exc_info=True)
            raise
