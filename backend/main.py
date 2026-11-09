"""
Legal AI Assistant — FastAPI Backend
Inhouse юридический консультант с OpenRouter API

Security fixes applied (see Security Audit):
  #1  — Admin endpoints require admin role via require_admin dependency
  #2  — Registration always forces role='user', only admins can create admins
  #3  — Chat requires authentication via require_auth dependency
  #4  — bcrypt instead of SHA-256 for password hashing
  #5  — Path traversal protection: sanitize filenames, restrict to DOCUMENTS_DIR
  #6  — CORS restricted to configured origins
  #7  — IDOR fix: conversation access checks ownership
  #8  — JWT TTL reduced to 8 hours, token blacklist table added
  #9  — Rate limiting via slowapi
  #10 — (handled in llm.py) prompt injection mitigation
  #11 — (handled in docker-compose.yml / reverse proxy) HTTPS note
  #12 — API errors sanitized, no raw tracebacks to users
  #13 — Docker non-root user, file upload size limit (30MB), audit log
"""

import os
import re
import time
import uuid
import json
import asyncio
import logging
import secrets
import tempfile
import aiosqlite
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

import bcrypt
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, field_validator
import jwt  # PyJWT
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import settings
import migrations
from llm import MODES as LLM_MODES, query_llm, query_llm_stream, query_llm_json
from documents import extract_text, is_extraction_error
from anonymizer import dlp_scan
from dadata_policy import client_counterparty_event, is_bare_counterparty_check
import rag as rag_module
import router_agent
from starter_content_loader import seed_skills
import presentation_publisher
from orchestrator import Orchestrator
from channels.web_adapter import WebAdapter
from scenario_registry import ScenarioRegistry
from schemas_agent import RequestContext, RouterDecision as AgentRouterDecision, Intent
from bitrix24.auth import verify_webhook_token
from bitrix24.client import Bitrix24Client
from channels.bitrix24_adapter import Bitrix24Adapter

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("legal-assistant")

# ── App & Rate Limiter (#9) ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения (современная замена @app.on_event).

    Код до `yield` выполняется при старте, после — при остановке.
    Тело инициализации вынесено в _startup() ниже по модулю; имена
    резолвятся в момент вызова, поэтому порядок определения не важен.
    """
    await _startup()
    yield
    # Отдельных действий при остановке нет: фоновые задачи
    # (_revoked_tokens_cleanup_loop, _initial_reindex) завершаются
    # вместе с процессом.

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Legal AI Assistant", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS (#6): restrict origins ──────────────────────────────────────
# Авторизация — Bearer-токен в заголовке Authorization (localStorage), не cookie.
# Поэтому allow_credentials=False: credentials-режим управляет куками/браузерной
# HTTP-аутентификацией, а не вручную выставленным заголовком Authorization
# (он разрешён через allow_headers). Bearer продолжает работать.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Constants ─────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 MB (#13)
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".jpg", ".jpeg", ".png", ".xlsx", ".xls", ".jsonl"}
JWT_TTL_HOURS = 8  # (#8) reduced from 7 days

# ── Режимы ────────────────────────────────────────────────────────────
# Единый источник истины по наборам режимов (раньше дублировались литералами
# в нескольких местах). Базовый набор — контентные сценарии, под которые есть
# документы и промпты; остальные наборы выводятся из него.
CORE_MODES = ("consult", "ad_check", "document_gen", "regulation")
# Видимые админу режимы в UI (+ «Презентация»). Регулярный пользователь видит
# только virtual-режим «auto», в котором сценарий определяет роутер.
_VISIBLE_MODES = CORE_MODES + ("presentation",)
# Допустимые значения mode на входе /api/chat (видимые + virtual «auto»).
CHAT_INPUT_MODES = frozenset(_VISIBLE_MODES) | {"auto"}
# Режимы-теги для документов и скиллов: базовые + wildcard «all» (без presentation —
# презентационных документов в RAG нет).
VALID_DOC_MODES = frozenset(CORE_MODES) | {"all"}

# ── Database ──────────────────────────────────────────────────────────

DB_PATH = settings.db_path

# Глобальный Orchestrator singleton (инициализируется в startup)
_orchestrator: "Orchestrator | None" = None

async def get_db():
    db = await aiosqlite.connect(DB_PATH, timeout=30.0)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")
    await db.execute("PRAGMA synchronous=NORMAL")
    try:
        yield db
    finally:
        await db.close()

async def _startup():
    """Инициализация приложения. Вызывается из lifespan-обработчика."""
    if not os.getenv("TESTING"):
        settings.validate_for_production()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA synchronous=NORMAL")

        # Схема БД управляется единой системой миграций (migrations.py).
        await migrations.apply_pending(db)

        # Seed only a new instance. Existing administrator edits survive restart.
        has_instructions = await db.execute_fetchall("SELECT 1 FROM mode_instructions LIMIT 1")
        for mode_id, meta in LLM_MODES.items():
            await db.execute(
                "INSERT OR IGNORE INTO mode_instructions (mode, label, instruction) VALUES (?, ?, ?)",
                (mode_id, meta["label"], meta["instruction"]),
            )
        if not has_instructions:
            await seed_skills(db)

        # Пароли admin/dashboard выставляются только при первом создании.
        # Перезаписывать их на каждом старте нельзя: это откатывало бы любые
        # ручные смены пароля и фиксировало бы пароль из .env как единственный
        # источник истины (если .env скомпрометирован — admin захвачен).
        admin = await db.execute_fetchall(
            "SELECT id FROM users WHERE username = 'admin'"
        )
        if not admin:
            admin_hash = await hash_password(settings.admin_password)
            await db.execute(
                "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), "admin", admin_hash, "admin")
            )
            logger.info("Создан пользователь admin с паролем из ADMIN_PASSWORD")

        dashboard_row = await db.execute_fetchall(
            "SELECT id FROM users WHERE username = 'dashboard'"
        )
        if not dashboard_row:
            dashboard_hash = await hash_password(settings.dashboard_password)
            await db.execute(
                "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), "dashboard", dashboard_hash, "dashboard")
            )
            logger.info("Создан пользователь dashboard с паролем из DASHBOARD_PASSWORD")

        await db.commit()
    logger.info("Database initialized")

    # Инициализация Orchestrator singleton
    global _orchestrator
    _orchestrator = Orchestrator(db_path=str(DB_PATH))
    logger.info("[Orchestrator] initialized")

    # Фоновая чистка blacklist JWT: токены живут JWT_TTL_HOURS часов,
    # хранить более старые записи бессмысленно — токен и так истёк.
    asyncio.create_task(_revoked_tokens_cleanup_loop())

    # Переиндексация ChromaDB запускается фоновой таской: при пустой коллекции
    # на холодном старте проход по сотням документов с эмбеддингами может
    # занимать минуты. Раньше это блокировало startup() → healthcheck не
    # отвечал → контейнер висел в «starting» и держал nginx (depends_on:
    # service_healthy) на старте до 20 минут.
    # Во время реиндексации /api/chat работает с пустым RAG — отвечает на
    # основе чистого LLM + skills. После завершения коллекция становится
    # доступной. Регулярные рестарты, когда индекс на диске уже есть,
    # быстро проходят: needs_reindex() = False.
    asyncio.create_task(_initial_reindex())

# ── Background cleanup tasks ─────────────────────────────────────────

async def _initial_reindex() -> None:
    """Холодная переиндексация ChromaDB, если коллекция пуста или несовместима.

    Запускается фоном из startup, чтобы не блокировать готовность приложения.
    На время реиндексации /api/chat отвечает с пустым RAG.
    Несовместимость (needs_reindex при непустой коллекции) возникает при смене
    rag_embed_model — тогда коллекция дропается и строится заново.
    """
    try:
        if not rag_module.needs_reindex(settings.chroma_path):
            return
        # Дроп очищает и Chroma, и BM25; на пустой коллекции безвреден.
        await rag_module.async_drop_collection(settings.chroma_path)
        async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=30000")
            rows = await db.execute_fetchall(
                "SELECT id, content, mode, category, original_filename FROM documents"
            )
        if not rows:
            return
        logger.info(f"[REINDEX] фоновая переиндексация {len(rows)} документов запущена")
        indexed = 0
        for d in rows:
            if d["content"]:
                try:
                    await rag_module.async_index_document(
                        d["id"], d["content"], d["mode"],
                        d["category"], d["original_filename"],
                        settings.chroma_path,
                    )
                    indexed += 1
                except Exception as exc:
                    logger.error(
                        f"[REINDEX] ошибка индексации {d['original_filename']}: {exc}"
                    )
        logger.info(f"[REINDEX] фоновая переиндексация завершена: {indexed}/{len(rows)}")
    except Exception as exc:
        logger.error(f"[REINDEX] фоновая переиндексация упала: {exc}", exc_info=True)


async def _revoked_tokens_cleanup_loop() -> None:
    """Раз в час удаляет записи blacklist старше JWT TTL.

    Без чистки таблица растёт неограниченно (запись на каждый logout)
    и проверка blacklist на каждом запросе постепенно деградирует.
    """
    while True:
        try:
            async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
                await db.execute("PRAGMA busy_timeout=30000")
                cur = await db.execute(
                    "DELETE FROM revoked_tokens "
                    "WHERE revoked_at < datetime('now', ?)",
                    (f"-{JWT_TTL_HOURS} hours",),
                )
                deleted = cur.rowcount
                await db.commit()
                if deleted:
                    logger.info(
                        f"[CLEANUP] revoked_tokens: удалено {deleted} истёкших записей"
                    )
        except Exception as exc:
            logger.warning(f"[CLEANUP] revoked_tokens error: {exc}")
        await asyncio.sleep(3600)


# ── Audit log (#13) ──────────────────────────────────────────────────

async def audit(db, user_id: str, action: str, target: str = "", details: str = "", ip: str = ""):
    """Записывает событие в audit_log и коммитит транзакцию.

    Коммитим внутри, чтобы запись аудита нельзя было «потерять» из-за
    пропуска `await db.commit()` в эндпоинте. Если у вызывающего была
    активная транзакция с другими изменениями, они также будут
    закоммичены — это допустимо, т.к. audit() вызывается в конце
    обработки действия, когда основной INSERT/UPDATE уже выполнен.
    """
    await db.execute(
        "INSERT INTO audit_log (id, user_id, action, target, details, ip) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, action, target, details, ip)
    )
    await db.commit()

# ── Password hashing with bcrypt (#4) ────────────────────────────────
# Bcrypt с дефолтным cost-фактором тратит ~250 мс CPU. В однопоточном event-loop
# даже несколько параллельных логинов могут заметно задержать обработку других
# запросов. Поэтому публичные функции — async, тяжёлая работа уходит в пул потоков.

def _hash_password_blocking(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_password_blocking(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_password_blocking, password)

async def verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(_verify_password_blocking, password, hashed)

# ── JWT with JTI for revocation (#8) ─────────────────────────────────

def create_token(user_id: str, role: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "jti": secrets.token_hex(16),
            "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_TTL_HOURS),
            "iat": datetime.now(timezone.utc),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Недействительный или просроченный токен")

# ── Auth dependencies (#1, #3) ───────────────────────────────────────

async def require_auth(authorization: str = Header(default=""), db=Depends(get_db)) -> dict:
    """Extract and validate JWT. Returns payload."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Требуется авторизация")
    token = authorization[7:]
    payload = decode_token(token)

    # Check revoked (#8)
    jti = payload.get("jti")
    if jti:
        revoked = await db.execute_fetchall(
            "SELECT jti FROM revoked_tokens WHERE jti = ?", (jti,)
        )
        if revoked:
            raise HTTPException(401, "Токен отозван. Войдите заново.")

    return payload

async def require_admin(payload: dict = Depends(require_auth)) -> dict:
    """Require admin role (#1)."""
    if payload.get("role") != "admin":
        raise HTTPException(403, "Доступ запрещён. Требуется роль администратора.")
    return payload

async def require_dashboard(payload: dict = Depends(require_auth)) -> dict:
    """Require dashboard or admin role."""
    if payload.get("role") not in ("dashboard", "admin"):
        raise HTTPException(403, "Доступ запрещён. Требуется роль dashboard.")
    return payload

# ── Dashboard: time savings classification ───────────────────────────

_MODE_MINUTES: dict[str, int] = {
    "consult": 30,
    "ad_check": 30,
    "regulation": 30,
}

_SUB_MINUTES: dict[str, int] = {
    "summary": 120,
    "claim": 90,
    "forms": 15,
    "requisites": 10,
}

_SUB_KEYWORDS: dict[str, list[str]] = {
    "summary": ["саммари", "summary", "резюме по договор", "краткое содержание договор", "краткий анализ договор"],
    "forms": ["типовую форму", "типовой договор", "типовые формы", "шаблон договора", "форму договора", "типовых форм"],
    "requisites": ["реквизит", "инн контрагент", "кпп контрагент", "огрн", "реквизиты контрагент", "документы контрагент"],
}

def _extract_product_names(text: str) -> list[str]:
    """Извлекает названия продуктов (препаратов, БАД, косметики) из текста запроса.

    Два прохода:
    1. Слова с ® или ™ — наиболее надёжный признак торгового наименования.
    2. Заглавные слова (≥4 символов), которым предшествует строчная буква или
       пунктуация — т.е. не начало предложения и не начало строки.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        clean = name.strip()
        if len(clean) >= 4 and clean.lower() not in seen:
            seen.add(clean.lower())
            names.append(clean)

    for m in re.finditer(r'([А-ЯЁA-Zа-яёa-z][А-ЯЁA-Zа-яёa-z0-9-]*)[®™]', text):
        _add(m.group(1))

    for m in re.finditer(r'(?<=[а-яёa-z,;:«»])\s+([А-ЯЁ][а-яёА-ЯЁ0-9-]{3,})', text):
        _add(m.group(1))

    return names


def _compute_chat_stat(mode: str, question: str, skill_categories: list[str]) -> tuple[str | None, int]:
    if mode != "document_gen":
        return None, _MODE_MINUTES.get(mode, 30)
    q = question.lower()
    for sub_cat, keywords in _SUB_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return sub_cat, _SUB_MINUTES[sub_cat]
    for cat in ("summary", "forms", "requisites"):
        if cat in skill_categories:
            return cat, _SUB_MINUTES[cat]
    return "claim", 90

# ── File validation (#5) ─────────────────────────────────────────────

def sanitize_filename(filename: str) -> str:
    """Prevent path traversal (#5)."""
    name = Path(filename).name
    name = re.sub(r'[/\\:\x00]', '', name)
    if not name or name.startswith('.'):
        name = f"upload_{uuid.uuid4().hex[:8]}{Path(filename).suffix}"
    return name

def validate_file_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Недопустимый формат файла. Разрешены: {', '.join(ALLOWED_EXTENSIONS)}")

def parse_and_validate_modes(mode_str: str) -> str:
    """Принимает строку режимов (одиночный или через запятую), возвращает нормализованную строку."""
    modes = [m.strip() for m in mode_str.split(",") if m.strip()]
    if not modes:
        return "all"
    if "all" in modes:
        return "all"
    invalid = [m for m in modes if m not in VALID_DOC_MODES]
    if invalid:
        raise HTTPException(400, f"Недопустимые режимы: {', '.join(invalid)}")
    return ",".join(dict.fromkeys(modes))

# ── Auth endpoints ────────────────────────────────────────────────────

def _validate_username(v: str, *, field_label: str = "Логин") -> str:
    """Общая валидация логина для всех auth-эндпоинтов.

    Запрещает: пустые значения, длиннее 64, разделители путей, последовательность
    «..», NUL-байт, управляющие символы (включая newline/tab). Алфавитные
    символы (любого языка) разрешены.
    """
    v = v.strip()
    if not v or len(v) > 64:
        raise ValueError(f"{field_label} должен быть от 1 до 64 символов")
    if "\x00" in v or "/" in v or "\\" in v or ".." in v:
        raise ValueError(f"{field_label} содержит недопустимые символы")
    if any(ord(c) < 32 or ord(c) == 0x7f for c in v):
        raise ValueError(f"{field_label} содержит управляющие символы")
    return v


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_clean(cls, v):
        return _validate_username(v, field_label="Логин")

class UserLoginRequest(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def username_clean(cls, v):
        return _validate_username(v, field_label="Имя")

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest, db=Depends(get_db)):
    row = await db.execute_fetchall(
        "SELECT id, password_hash, role FROM users WHERE username = ?",
        (req.username,)
    )
    if not row or not await verify_password(req.password, row[0]["password_hash"]):
        logger.warning(f"Failed login attempt: {req.username}")
        raise HTTPException(401, "Неверный логин или пароль")
    user = row[0]
    logger.info(f"User logged in: {req.username}")
    await audit(db, user["id"], "login", ip=get_remote_address(request))
    await db.commit()
    return {
        "token": create_token(user["id"], user["role"]),
        "role": user["role"],
        "username": req.username,
    }

@app.post("/api/auth/user-login")
@limiter.limit("20/minute")
async def user_login(request: Request, req: UserLoginRequest, db=Depends(get_db)):
    """Вход без пароля — только для роли user."""
    row = await db.execute_fetchall(
        "SELECT id, role FROM users WHERE username = ?",
        (req.username,)
    )
    if row:
        user = row[0]
        if user["role"] in ("admin", "dashboard"):
            raise HTTPException(403, "Этот аккаунт требует входа с паролем")
        user_id = user["id"]
    else:
        user_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
            (user_id, req.username, "NO_PASSWORD", "user"),
        )
        logger.info(f"Создан пользователь без пароля: {req.username}")
    await audit(db, user_id, "user_login", ip=get_remote_address(request))
    await db.commit()
    return {
        "token": create_token(user_id, "user"),
        "role": "user",
        "username": req.username,
    }

# (#2) Registration always forces role='user'
@app.post("/api/auth/register")
@limiter.limit("5/minute")
async def register_user(request: Request, req: LoginRequest, db=Depends(get_db)):
    if not settings.allow_self_registration:
        raise HTTPException(403, "Самостоятельная регистрация отключена. Обратитесь к администратору.")
    existing = await db.execute_fetchall(
        "SELECT id FROM users WHERE username = ?", (req.username,)
    )
    if existing:
        raise HTTPException(400, "Пользователь уже существует")
    user_id = str(uuid.uuid4())
    pw_hash = await hash_password(req.password)
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (user_id, req.username, pw_hash, "user"),  # ALWAYS 'user'
    )
    await audit(db, user_id, "register", ip=get_remote_address(request))
    await db.commit()
    logger.info(f"New user registered: {req.username}")
    return {"token": create_token(user_id, "user"), "role": "user", "username": req.username}

@app.post("/api/auth/logout")
async def logout(payload: dict = Depends(require_auth), db=Depends(get_db)):
    """Revoke current token (#8)."""
    jti = payload.get("jti")
    if jti:
        await db.execute("INSERT OR IGNORE INTO revoked_tokens (jti) VALUES (?)", (jti,))
        await db.commit()
    return {"ok": True}

# ── Chat / LLM (#3: auth required) ───────────────────────────────────

CHAT_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".jpg", ".jpeg", ".png"}

# ПДн физических лиц, которые запрещены в файлах для режима ad_check.
# Реквизиты юрлиц (ИНН_ЮЛ, ОГРН, КПП, КОМПАНИЯ и др.) допустимы в рекламных материалах.
#
# ФИО намеренно НЕ блокируется: в рекламных материалах ЛС/БАД регулярно
# встречаются фамилии авторов публикаций в библиографических ссылках
# («Иванов И.И., 2023, JAMA») — это публичная информация, блокировка
# на ней рушит легитимные кейсы. Фронтенд покажет DLP-предупреждение
# (включая count ФИО), но не блокирует загрузку. Строгие идентификаторы
# (паспорт, СНИЛС, ВУ, ИНН физлица) в нормальных креативах не встречаются —
# по ним блокировка остаётся.
_AD_CHECK_BLOCKED_PII: frozenset[str] = frozenset({
    "ПАСПОРТ", # паспортные данные
    "СНИЛС",   # страховой номер ИЛС
    "ВОД_УД",  # водительское удостоверение
    "ИНН_ФЛ",  # ИНН физлица (12 цифр)
})

# Режимы, где документы диалога переподставляются в контекст последующих
# сообщений (Вариант A). Для «Регламенты» не нужно — там работа не с
# пользовательскими файлами, а с базой регламентов.
_DOC_MEMORY_MODES: frozenset[str] = frozenset({"document_gen", "consult", "ad_check"})

# Суммарный бюджет объёма документов, уходящих в контекст основной LLM:
# вложения текущего сообщения (file_context) + ранее загруженные документы
# диалога (_prev_docs_block). Один файл ограничен 80k символов, но без общего
# лимита пять файлов (≈400k) плюс прежние документы (до 160k) переполняют окно
# модели → жёсткая ошибка API (HTTP 400) вместо ответа. ~240k символов ≈ 60k
# токенов на документы, оставляя место под system prompt, RAG и историю.
_MAX_LLM_DOC_CHARS = 240_000

# Предпочтительный лимит на блок ранее загруженных документов: даже при большом
# свободном бюджете прежние документы не должны вытеснять вложения текущего
# сообщения и RAG. Реальный лимит — min(этого значения, остатка общего бюджета).
_MAX_CONVERSATION_DOCS_CHARS = 160_000


def _build_prev_docs_block(docs: list, budget: int = _MAX_CONVERSATION_DOCS_CHARS) -> str:
    """Собирает блок «ранее загруженные документы диалога» с лимитом объёма.

    docs — строки conversation_files (filename, content), в хронологическом
    порядке. Заполняем от старых к новым: исходный договор (загруженный
    первым) приоритетнее поздних вспомогательных файлов.

    budget — максимум символов на блок; вызывающий код передаёт остаток общего
    бюджета контекста (после вложений текущего сообщения), чтобы их сумма не
    переполнила окно модели.
    """
    if not docs or budget <= 0:
        return ""
    parts = ["\n\n[РАНЕЕ ЗАГРУЖЕННЫЕ ДОКУМЕНТЫ ДИАЛОГА]"]
    total = 0
    omitted = 0
    for d in docs:
        entry = f"\n\n[Документ: {d['filename']}]\n{d['content'] or ''}"
        if total + len(entry) > budget:
            omitted += 1
            continue
        total += len(entry)
        parts.append(entry)
    if omitted:
        parts.append(
            f"\n\n[Ещё {omitted} ранее загруженных документов не показаны "
            "из-за лимита объёма контекста.]"
        )
    return "".join(parts)


def _message_row_to_dict(row) -> dict:
    """Строка messages → dict для API. check_result хранится JSON-строкой —
    отдаём фронту распарсенным объектом (или None)."""
    m = dict(row)
    raw = m.pop("check_result", None)
    m["check_result"] = json.loads(raw) if raw else None
    return m


@app.post("/api/chat")
@limiter.limit("30/minute")
async def chat(
    request: Request,
    message: str = Form(...),
    conversation_id: Optional[str] = Form(None),
    mode: str = Form("consult"),
    scenario_hint: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
    payload: dict = Depends(require_auth),
    db=Depends(get_db),
):
    message = message.strip()
    if not message:
        raise HTTPException(400, "Сообщение не может быть пустым")
    if len(message) > 16000:
        raise HTTPException(400, "Сообщение слишком длинное (макс. 16000 символов)")
    if mode not in CHAT_INPUT_MODES:
        raise HTTPException(400, "Недопустимый режим")
    # «Презентация для бизнеса» — admin-only режим. Регулярному пользователю
    # он не виден в UI; здесь — серверная защита на случай прямого вызова API.
    if mode == "presentation" and payload.get("role") != "admin":
        raise HTTPException(403, "Режим доступен только администратору")

    user_id = payload["sub"]

    # ── Publish short-circuit (режим «Презентация для бизнеса») ──────────
    # Когда юрист в чате «Презентация» пишет «опубликовать» (или варианты),
    # мы не запускаем LLM-пайплайн: вместо этого парсим markdown из
    # предыдущего ответа ассистента, INSERT/UPDATE projects, и отвечаем
    # короткой подтверждающей репликой со ссылкой на /projects/{slug}.
    if (
        mode == "presentation"
        and conversation_id
        and presentation_publisher.is_publish_intent(message)
    ):
        if not settings.public_projects_enabled:
            raise HTTPException(403, "Публикация проектов отключена настройками экземпляра")
        owned_conversation = await db.execute_fetchall(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if not owned_conversation:
            raise HTTPException(404, "Диалог не найден")
        # Тянем последний ответ ассистента из истории диалога.
        async with db.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'assistant' "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(
                400,
                "В этом диалоге нет ответа ассистента — нечего публиковать.",
            )
        parsed = presentation_publisher.parse_presentation(row["content"])
        # Имя автора для отображения в карточке — берём username.
        async with db.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,),
        ) as ucur:
            urow = await ucur.fetchone()
        author_name = (urow["username"] if urow else "") or "Юридическая служба"
        try:
            result = await presentation_publisher.publish_presentation(
                db, conversation_id, user_id, author_name, parsed,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Сохраним пользовательскую реплику + сгенерированный ответ-подтверждение
        # как обычные сообщения чата (чтобы юрист видел результат в истории).
        msg_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            (msg_id, conversation_id, "user", message),
        )
        verb = "обновлён" if result.get("updated") else "опубликован"
        reply = (
            f"Проект «{result['title']}» {verb}. "
            f"Карточка доступна по ссылке: /projects/{result['slug']}.\n\n"
            "Управление публикациями — раздел «Проекты на дашборде» в админ-панели."
        )
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, "assistant", reply),
        )
        await db.commit()

        local_reply = reply
        local_conv = conversation_id

        async def publish_stream():
            meta = {"type": "meta", "conversation_id": local_conv, "mode": "presentation"}
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'delta', 'content': local_reply}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(publish_stream(), media_type="text/event-stream")

    # ── Router-агент ──────────────────────────────────────────────────
    # mode="auto" — роутер запускается на КАЖДОМ сообщении, а не только на
    # первом. Это позволяет пользователю менять тему внутри диалога
    # («что ты можешь?» → «проверь рекламу»): каждое сообщение получает
    # подходящий сценарий. Раньше режим фиксировался по первому сообщению,
    # и диалог застревал в нём навсегда.
    routing_info: dict | None = None
    clarification_text: str | None = None
    _active_scenario_id: str | None = None  # Этап 1: scenario_id для meta event
    # Оригинальное решение роутера. Сохраняем целиком, чтобы передать в
    # Orchestrator с настоящим confidence/reason/intent, а не фейковым
    # confidence=1.0 (что отключало FallbackPolicy).
    _router_decision: router_agent.RouterDecision | None = None
    if mode == "auto":
        attachment_names = [f.filename for f in files if f and f.filename]
        # Для существующего диалога подтягиваем хвост истории — роутеру нужен
        # контекст, чтобы понять короткие реплики («да», «проверь и это»).
        history_tail: list[dict] = []
        current_scenario: str | None = None
        if conversation_id:
            _ht_rows = await db.execute_fetchall(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT 6",
                (conversation_id,),
            )
            history_tail = [
                {"role": r["role"], "content": r["content"]}
                for r in reversed(_ht_rows)
            ]
            # Сценарий уже идущего диалога — якорь для роутера: короткая
            # реплика-уточнение не должна перекидывать в другой сценарий.
            _conv_row = await db.execute_fetchall(
                "SELECT scenario_id FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            if _conv_row:
                current_scenario = _conv_row[0]["scenario_id"]
        # Подсказка с кнопки сайдбара — только в auto и только для известного сценария.
        # Явный режим админа и неизвестный id не переопределяют роутер.
        known_hint: str | None = None
        raw_hint = (scenario_hint or "").strip()
        if raw_hint and raw_hint in ScenarioRegistry.get_instance().scenario_ids():
            known_hint = raw_hint
        decision = await router_agent.route(
            message,
            attachment_names,
            history_tail,
            scenario_hint=known_hint,
            current_scenario=current_scenario,
        )
        _router_decision = decision
        _active_scenario_id = decision.scenario_id
        routing_info = {
            "routed_mode": decision.mode,
            "routing_reason": decision.reason,
            "routing_confidence": decision.confidence,
            "scenario_id": decision.scenario_id,
        }
        if decision.clarification and decision.mode is None:
            clarification_text = decision.clarification
            # Для clarification используем consult как «нейтральный» владелец
            # диалога — пайплайн не запускается, это просто слот для записи.
            mode = "consult"
        else:
            mode = decision.mode or "consult"
    else:
        # Явный legacy mode → определить scenario_id через registry
        if mode in CORE_MODES:
            try:
                _active_scenario_id = ScenarioRegistry.get_instance().from_legacy_mode(mode).id
            except Exception:
                pass

    # ── Clarification short-circuit ───────────────────────────────────
    # Если роутер просит уточнение — не читаем файлы, не дёргаем RAG,
    # анонимайзер и основной LLM. Сохраняем сообщение пользователя +
    # ответ-уточнение и закрываем стрим. Уточнение может прийти как на
    # новом диалоге, так и на существующем (неоднозначное сообщение
    # в середине диалога) — поэтому переиспользуем conversation_id.
    if clarification_text:
        if conversation_id:
            owner = await db.execute_fetchall(
                "SELECT user_id FROM conversations WHERE id = ?", (conversation_id,)
            )
            if not owner or owner[0]["user_id"] != user_id:
                raise HTTPException(403, "Нет доступа к этому диалогу")
            conv_id = conversation_id
        else:
            conv_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO conversations (id, user_id, title, mode, scenario_id) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, message[:80], mode, _active_scenario_id),
            )
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conv_id, "user", message),
        )
        await db.commit()

        clarification_final = clarification_text
        local_routing_info = routing_info

        async def clarification_stream():
            meta = {"type": "meta", "conversation_id": conv_id, "mode": mode}
            if local_routing_info is not None:
                meta.update(local_routing_info)
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'delta', 'content': clarification_final}, ensure_ascii=False)}\n\n"

            try:
                async with aiosqlite.connect(DB_PATH, timeout=30.0) as wdb:
                    await wdb.execute("PRAGMA busy_timeout=30000")
                    await wdb.execute(
                        "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4()), conv_id, "assistant", clarification_final),
                    )
                    await wdb.execute(
                        "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                        (conv_id,),
                    )
                    await wdb.commit()
            except Exception as e:
                logger.error(f"[CHAT_STREAM] clarification persist error: {e}", exc_info=True)

            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            clarification_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    file_context = ""
    file_names = []
    active_files = [f for f in files if f and f.filename]
    if len(active_files) > 5:
        raise HTTPException(400, "Максимум 5 файлов за один запрос")

    _pm = _orchestrator.get_privacy_manager()
    _policy = _pm.get_policy_for_legacy_mode(mode)
    anonymizer = _pm.create_anonymizer(_policy)
    logger.info(f"[CHAT] mode={mode} files={len(active_files)} anonymize={_policy.anonymize_file_context}")

    raw_texts_for_dadata: list[str] = []   # обрезанный текст (хранение в conversation_files)
    full_texts_for_dadata: list[str] = []  # полный текст до обрезки (для DaData — реквизиты в конце)
    _file_ctx_used = 0                      # суммарный объём file_context (бюджет контекста LLM)
    _file_ctx_omitted = 0                   # сколько файлов не уместилось в общий бюджет

    for file in active_files:
        ext = Path(file.filename).suffix.lower()
        if ext not in CHAT_ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Недопустимый формат: {file.filename}. Разрешены: {', '.join(CHAT_ALLOWED_EXTENSIONS)}")
        content_bytes = await file.read()
        if len(content_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"Файл {file.filename} слишком большой. Максимум: {MAX_UPLOAD_BYTES // (1024*1024)} МБ")
        safe_name = sanitize_filename(file.filename)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content_bytes)
            tmp_path = Path(tmp.name)
        t0 = time.monotonic()
        try:
            # extract_text синхронный, тяжёлый (PDF/OCR могут занимать секунды).
            # Выносим в пул потоков, чтобы не блокировать event loop.
            # full_ocr для ad_check: бренд/лого на рекламных креативах часто
            # сделан векторной графикой и невидим для текстового слоя PDF.
            file_text = await asyncio.to_thread(
                extract_text, tmp_path, safe_name, mode == "ad_check"
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        logger.info(
            f"[EXTRACT] file={safe_name} bytes={len(content_bytes)} "
            f"chars={len(file_text)} elapsed={time.monotonic()-t0:.1f}s"
        )
        # Полный извлечённый текст до обрезки. Нужен DaData: реквизиты сторон
        # (ИНН/ОГРН) обычно в КОНЦЕ договора и иначе срезались бы лимитом 80k —
        # тогда проверка контрагента не запускалась. raw_text используется
        # только для regex-извлечения идентификаторов, целиком в LLM не уходит.
        raw_full_text = file_text

        # (Фикс A11) Длинные документы обрезаются с явным предупреждением,
        # чтобы пользователь видел, что часть текста не уйдёт в анализ.
        if len(file_text) > 80000:
            logger.warning(
                f"[TRUNCATE] file={safe_name} chars={len(file_text)} -> 80000"
            )
            file_text = (
                file_text[:80000]
                + "\n\n[ВНИМАНИЕ: файл был обрезан до 80 000 символов. "
                "Часть содержимого не была передана в анализ.]"
            )
        # Risk B2: в режиме ad_check запрещены документы с ПДн физлиц.
        # Рекламные материалы не анонимизируются (потеряется контекст продукта),
        # поэтому единственная защита — блокировка на входе.
        if mode == "ad_check":
            _pii = dlp_scan(file_text)
            _blocked = _AD_CHECK_BLOCKED_PII & _pii.keys()
            if _blocked:
                raise HTTPException(
                    422,
                    f"Файл «{safe_name}» содержит персональные данные физических лиц "
                    f"({', '.join(sorted(_blocked))}). "
                    "В режиме проверки рекламы загрузка документов с ПДн физлиц запрещена. "
                    "Допустимы только рекламные материалы и реквизиты юридических лиц."
                )

        raw_texts_for_dadata.append(file_text)  # обрезанный — для хранения/переподстановки
        full_texts_for_dadata.append(raw_full_text)  # полный — для DaData (реквизиты в конце)

        before_len = len(file_text)
        file_text = _pm.anonymize_file_context(file_text, _policy, anonymizer)
        if anonymizer is not None and len(file_text) != before_len:
            logger.info(
                f"[ANONYMIZE] file={safe_name} "
                f"chars_before={before_len} chars_after={len(file_text)} "
                f"placeholders={len(anonymizer.mapping())}"
            )
        # Общий бюджет контекста: помимо per-file лимита 80k ограничиваем сумму
        # объёмов вложений, иначе 5 файлов (≈400k) + ранее загруженные документы
        # переполняют окно модели → HTTP 400 вместо ответа. raw/full тексты для
        # DaData и хранения не урезаются — лимит только на инъекцию в LLM.
        entry = f"\n\n[Прикреплённый файл: {safe_name}]\n{file_text}"
        _remaining = _MAX_LLM_DOC_CHARS - _file_ctx_used
        if _remaining <= 0:
            _file_ctx_omitted += 1
        else:
            if len(entry) > _remaining:
                entry = (
                    entry[:_remaining]
                    + "\n\n[ВНИМАНИЕ: вложение обрезано из-за суммарного лимита "
                    "объёма документов в одном запросе.]"
                )
            file_context += entry
            _file_ctx_used += len(entry)
        file_names.append(safe_name)
    if _file_ctx_omitted:
        file_context += (
            f"\n\n[ВНИМАНИЕ: {_file_ctx_omitted} вложений не переданы в анализ "
            "из-за суммарного лимита объёма документов. Отправьте их отдельным запросом.]"
        )
    file_label = (" [файлы: " + ", ".join(file_names) + "]") if file_names else ""

    from query_rewriter import TEMPLATE_FORM_USER_PREFIX
    if (
        _active_scenario_id == "template_form_navigation"
        and message.startswith(TEMPLATE_FORM_USER_PREFIX)
    ):
        message = message[len(TEMPLATE_FORM_USER_PREFIX):]

    conv_id = conversation_id or str(uuid.uuid4())
    if conversation_id:
        # (#7) IDOR fix: verify ownership
        owner = await db.execute_fetchall(
            "SELECT user_id FROM conversations WHERE id = ?", (conv_id,)
        )
        if not owner or owner[0]["user_id"] != user_id:
            raise HTTPException(403, "Нет доступа к этому диалогу")
        # Перемаршрутизация: если режим определён роутером заново — обновляем
        # mode/scenario_id диалога, чтобы бейдж в интерфейсе и последующая
        # обработка отражали актуальный сценарий.
        if _router_decision is not None:
            await db.execute(
                "UPDATE conversations SET mode = ?, scenario_id = ? WHERE id = ?",
                (mode, _active_scenario_id, conv_id),
            )
    else:
        await db.execute(
            "INSERT INTO conversations (id, user_id, title, mode, scenario_id) VALUES (?, ?, ?, ?, ?)",
            (conv_id, user_id, message[:80], mode, _active_scenario_id),
        )

    stored_message = message + file_label
    await db.execute(
        "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), conv_id, "user", stored_message),
    )

    # ── Документы диалога (Вариант A) ─────────────────────────────────────────
    # Загружаем ранее сохранённые документы ДО сохранения текущих — иначе
    # текущие задвоятся (попадут и в file_context, и в блок «ранее загруженные»).
    _prev_conversation_docs: list = []
    if mode in _DOC_MEMORY_MODES:
        _prev_conversation_docs = await db.execute_fetchall(
            "SELECT filename, content FROM conversation_files "
            "WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,),
        )
    # Сохраняем документы текущего сообщения, чтобы они были доступны на
    # последующих сообщениях диалога. Храним сырой (до анонимизации) текст —
    # анонимизация применяется при переподстановке, как и к истории.
    for _fname, _ftext in zip(file_names, raw_texts_for_dadata):
        await db.execute(
            "INSERT INTO conversation_files (id, conversation_id, filename, content) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conv_id, _fname, _ftext),
        )

    # Коммитим строку диалога и пользовательское сообщение ДО старта стрима.
    # Без этого dependency-соединение `db` закроется в finally без коммита,
    # и DEFERRED-транзакция aiosqlite откатится — терялись и сама запись
    # о диалоге, и сообщение пользователя; а попытка фронта подтянуть
    # /conversations/{id}/messages сразу после meta-события возвращала 403
    # (строка диалога ещё не видна другим соединениям) и логаутила юзера.
    await db.commit()

    # Нормализация scenario_id: для случаев когда routing не определил сценарий
    # (mode="auto" + существующий диалог с NULL scenario_id в БД).
    if not _active_scenario_id:
        try:
            _active_scenario_id = ScenarioRegistry.get_instance().from_legacy_mode(mode).id
        except Exception:
            _active_scenario_id = "legal_consultation"

    # Сырой контекст для DaData (поиск ИНН/ОГРН контрагента). Помимо файлов
    # текущего сообщения включаем ранее загруженные документы диалога: иначе
    # «сделай саммари» следующим сообщением (без повторного вложения договора)
    # не находит контрагента и проверка не запускается.
    _dadata_raw_parts = list(full_texts_for_dadata)
    _dadata_raw_parts += [d["content"] for d in _prev_conversation_docs if d["content"]]
    _dadata_raw_context = " ".join(_dadata_raw_parts) if _dadata_raw_parts else None

    # ── Orchestrator: build_plan — обязательный путь ──────────────────────────
    # Orchestrator является единственным центром обработки запроса.
    # FallbackPolicy внутри build_plan гарантирует план даже при ошибке роутинга.
    # Клик «типовые формы»: в сохранённом сообщении остаётся текст пользователя,
    # а агент (скилл, поиск, LLM) видит, что ищут типовую форму = шаблон.
    from query_rewriter import with_template_form_context
    _agent_message = (
        with_template_form_context(message)
        if _active_scenario_id == "template_form_navigation"
        else message
    )
    _ctx_bp = RequestContext(
        request_id=conv_id,
        conversation_id=conv_id,
        user_id=user_id,
        message=_agent_message,
        legacy_mode=mode,
        channel="web",
        external_user_id=None,
        external_chat_id=None,
        external_message_id=None,
        channel_metadata={},
        attachments=file_names,
        file_context=file_context or None,
        raw_file_context=_dadata_raw_context,
        history_summary=None,
        metadata={},
    )
    # Конвертация router_agent.RouterDecision → schemas_agent.RouterDecision.
    # Если router сработал — пробрасываем настоящие confidence, reason, intent;
    # если режим выбран явно (admin кликнул режим / продолжение диалога) —
    # confidence=1.0 уместен, потому что решение пользователя/БД точно известно.
    if _router_decision is not None:
        _intent = (
            Intent(id=_router_decision.intent_id, confidence=_router_decision.confidence)
            if _router_decision.intent_id else None
        )
        _pre_decision = AgentRouterDecision(
            scenario_id=_active_scenario_id,
            legacy_mode=mode,
            intent=_intent,
            confidence=_router_decision.confidence,
            reason=_router_decision.reason,
            clarification=None,  # clarification обрабатывается выше short-circuit-ом
            alternatives=list(_router_decision.alternatives or []),
        )
    else:
        _pre_decision = AgentRouterDecision(
            scenario_id=_active_scenario_id,
            legacy_mode=mode,
            intent=None,
            confidence=1.0,
            reason="режим выбран пользователем явно",
            clarification=None,
            alternatives=[],
        )

    _plan = None
    if _orchestrator:
        try:
            _plan = await _orchestrator.build_plan(_ctx_bp, router_decision=_pre_decision)
            logger.info(
                "[CHAT] plan: scenario=%s prompt=%s skills=%d history=%d",
                _plan.scenario.id, _plan.prompt_id, len(_plan.skills),
                len(_plan.memory.messages),
            )
        except Exception as _bp_err:
            logger.error("[CHAT] build_plan error: %s", _bp_err, exc_info=True)

    if _plan is None:
        # Критическая ошибка — Orchestrator не инициализирован или упал с необработанным исключением.
        # Штатной legacy-ветки больше нет: Orchestrator является обязательным путём.
        logger.critical(
            "[CHAT] _plan is None — Orchestrator недоступен (mode=%s scenario=%s)",
            mode, _active_scenario_id,
        )
        raise HTTPException(503, "Сервис временно недоступен. Попробуйте позже.")

    # ── История диалога (из MemoryManager через plan) ─────────────────────────
    # MemoryManager загрузил историю с учётом max_history_messages сценария.
    history = [dict(m) for m in _plan.memory.messages]
    if (
        _active_scenario_id == "template_form_navigation"
        and history
        and history[-1].get("role") == "user"
    ):
        history[-1]["content"] = with_template_form_context(history[-1]["content"])

    # Inject file content into last user message for LLM context.
    # _prev_docs_block — документы, загруженные в этот диалог ранее (Вариант A):
    # переподставляем их, чтобы работа с договором продолжалась на последующих
    # сообщениях, а не только в момент загрузки файла. Сырой текст здесь —
    # анонимизация применяется ниже общим проходом по history.
    # Остаток общего бюджета контекста после вложений текущего сообщения —
    # чтобы file_context + prev_docs вместе не переполнили окно модели.
    _prev_docs_budget = min(
        _MAX_CONVERSATION_DOCS_CHARS, _MAX_LLM_DOC_CHARS - len(file_context)
    )
    _prev_docs_block = _build_prev_docs_block(_prev_conversation_docs, _prev_docs_budget)
    _injected_docs = _prev_docs_block + file_context
    if _injected_docs and history:
        history[-1]["content"] = history[-1]["content"] + _injected_docs
    if _prev_conversation_docs:
        logger.info(
            "[DOC_MEMORY] conv=%s переподставлено документов диалога: %d",
            conv_id, len(_prev_conversation_docs),
        )

    # Анонимизация: применяем ко всем сообщениям истории перед отправкой в LLM.
    # Ответ LLM затем деанонимизируется. В БД сообщения хранятся в исходном виде.
    if anonymizer is not None:
        for m in history:
            m["content"] = anonymizer.anonymize(m["content"])

    # ── Инструменты: pre-RAG фаза (product_lookup влияет на RAG) ────────────────
    # Инструменты с phase="pre_rag" должны выполниться ДО RAG, т.к. их результат
    # меняет запросы и участвует в дедупликации. Все остальные (phase="prompt") —
    # после RAG. Разделение по фазе — атрибут BaseTool.phase, не hardcoded в main.
    _pre_rag_results = await _orchestrator.execute_tools_by_phase(_plan, "pre_rag")

    top_k = settings.top_k_for_mode(mode)
    product_docs: list[dict] = []
    for _r in _pre_rag_results:
        if _r.tool_id == "product_lookup" and _r.activated and _r.structured_data:
            product_docs = _r.structured_data.get("product_docs", [])
            logger.info(
                "[PRODUCT_LOOKUP] mode=%s matched=%d files=%s",
                mode, len(product_docs), [d["filename"] for d in product_docs],
            )
            break

    if mode == "ad_check":
        _rag_t0 = time.monotonic()
        product_names = _extract_product_names(message)
        if len(product_names) >= 2:
            # Несколько продуктов: отдельный поиск для каждого + общий нормативный.
            # Это предотвращает вытеснение листков-вкладышей нормативными документами
            # при конкуренции за слоты top_k.
            rag_docs = await rag_module.async_search_multi_product(
                message, product_names, mode, top_k, settings.chroma_path
            )
        else:
            # Один продукт или нет распознанных имён: двойной поиск (прежнее поведение).
            words = message.split()
            claim_query = " ".join(words[1:]) if len(words) > 1 else ""
            rag_docs = await rag_module.async_search_dual(
                message, claim_query, mode, top_k, settings.chroma_path
            )
        logger.info(
            f"[RAG] mode={mode} top_k={top_k} docs={len(rag_docs)} "
            f"elapsed={time.monotonic()-_rag_t0:.1f}s"
        )

        # Дедуп: фрагменты документов, подтянутых целиком через product_lookup,
        # из RAG-выдачи убираем — они уже есть в полном тексте.
        if product_docs:
            product_filenames = {d["filename"] for d in product_docs}
            rag_docs = [d for d in rag_docs if d["filename"] not in product_filenames]
            # Полные регдокументы продукта ставим первыми, чтобы LLM использовала
            # их как приоритетный источник для сверки заявлений в рекламе.
            rag_docs = product_docs + rag_docs
    else:
        # Query rewriting: переписываем сообщение пользователя в 2–4
        # поисковые формулировки с юридической терминологией. Закрывает
        # разрыв между разговорной лексикой («можно ли давать ребёнку»)
        # и формальным языком нормативки («противопоказания по возрасту»).
        # Fallback внутри rewrite_for_search → исходное сообщение.
        from query_rewriter import rewrite_for_search
        _rag_t0 = time.monotonic()
        # Приватность (A1): query rewriter — отдельный внешний LLM-вызов. Сообщение
        # пользователя может содержать ПДн/реквизиты, вписанные прямо в текст.
        # Перед отправкой во внешнюю модель применяем ту же анонимизацию, что и к
        # истории (file_context анонимизирован выше по политике сценария).
        # anonymizer переиспользует существующий маппинг — плейсхолдеры
        # консистентны с основным запросом. Если политика без анонимизации
        # (ad_check/regulation), anonymizer is None — поведение прежнее.
        _search_message = (
            with_template_form_context(message)
            if _active_scenario_id == "template_form_navigation"
            else message
        )
        _msg_for_search = (
            anonymizer.anonymize(_search_message) if anonymizer is not None else _search_message
        )
        rewritten = await rewrite_for_search(
            _msg_for_search + file_context, scenario_id=_active_scenario_id
        )
        rag_docs = await rag_module.async_search_multi_query(
            rewritten, mode, top_k, settings.chroma_path
        )
        # Multi-hop: подтягиваем чанки, на которые ссылаются уже найденные
        # («ст. 24 ФЗ-38», «п. 4.2 Положения о договорной работе»).
        # Regex-based extractor + повторный поиск по фразе ссылки.
        if rag_docs:
            extra = await rag_module.async_followup_by_references(
                rag_docs, mode, settings.chroma_path,
            )
            if extra:
                rag_docs = rag_docs + extra
        logger.info(
            "[RAG] mode=%s top_k=%d queries=%d docs=%d elapsed=%.1fs",
            mode, top_k, len(rewritten), len(rag_docs), time.monotonic() - _rag_t0,
        )

    # (Фикс C3) Структурированный аудит-лог ПДн — счётчики по типу без значений.
    if anonymizer is not None:
        pii_stats = anonymizer.stats()
        if pii_stats:
            logger.info(f"[PII_STATS] mode={mode} user={user_id} stats={pii_stats}")

    template_rows = await db.execute_fetchall(
        "SELECT id, original_filename FROM documents WHERE category = 'contract' "
        "ORDER BY original_filename"
    )
    templates_payload = [
        {"id": r["id"], "original_filename": r["original_filename"]} for r in template_rows
    ]

    # ── Инструменты: prompt фаза (dadata и другие с text_block) ─────────────────
    # Выполняются ПОСЛЕ RAG и ПЕРЕД PromptAssembler — text_block попадает
    # в блок «РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ» системного промпта.
    _prompt_tool_results = await _orchestrator.execute_tools_by_phase(_plan, "prompt")
    _all_tool_results = _pre_rag_results + _prompt_tool_results

    # ── PromptAssembler: сборка system prompt ────────────────────────────────
    system_prompt = await _orchestrator.get_prompt_assembler().build(
        _plan, rag_docs, templates_payload, tool_results=_all_tool_results, skills=list(_plan.skills),
    )

    # Извлекаем check_result для SSE из результатов dadata инструмента
    check_result: dict | None = None
    for _tr in _prompt_tool_results:
        if _tr.tool_id == "dadata_check" and _tr.activated and _tr.structured_data:
            check_result = _tr.structured_data
            break

    # Если история диалога была обрезана — добавляем уведомление в промпт,
    # чтобы LLM знала об ограниченном контексте.
    if _plan.memory.truncated and _plan.memory.summary:
        system_prompt += f"\n\n{_plan.memory.summary}"

    skill_categories: list[str] = []
    skill_names_json = json.dumps([s.name for s in _plan.skills], ensure_ascii=False)
    sub_category, time_minutes = _compute_chat_stat(mode, message, skill_categories)

    _plan.trace.rag_docs_count = len(rag_docs)
    _plan.trace.llm_model = settings.model_for_mode(mode)

    _local_check_result = check_result

    # Используем scenario_id из плана — он точнее _active_scenario_id
    # (FallbackPolicy могла изменить сценарий при ошибке роутера).
    _local_scenario_id = _plan.scenario.id

    # В документных сценариях карточка в чат не уходит: реквизиты уже в промпте.
    # «Голая» проверка в consult по-прежнему показывает карточку и не дублирует
    # её текстовым ответом LLM.
    _card_event = client_counterparty_event(_local_scenario_id, _local_check_result)
    _suppress_text = _card_event is not None and is_bare_counterparty_check(message)
    if _suppress_text:
        logger.info("[CHAT] голая проверка контрагента — текстовый ответ LLM подавлен")

    async def event_stream():
        # Сначала отдаём conversation_id (+ результат роутинга, если был),
        # чтобы фронт сразу мог его сохранить и показать бейдж режима.
        meta = {
            "type": "meta",
            "conversation_id": conv_id,
            "mode": mode,
            "scenario_id": _local_scenario_id,
        }
        if routing_info is not None:
            meta.update(routing_info)
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"

        if _card_event is not None:
            yield f"data: {json.dumps(_card_event, ensure_ascii=False)}\n\n"

        response_parts: list[str] = []
        current_part = ""
        had_error = False
        _llm_t0 = time.monotonic()
        _first_chunk_logged = False

        # Буфер деанонимизации: плейсхолдеры вида [ФИО_3] могут прийти
        # разорванными между чанками. Буфер удерживает хвост, пока не
        # увидит закрывающую скобку.
        _deanon_buf = _pm.create_response_buffer(_policy, anonymizer)

        def _emit(text: str) -> str:
            """Прогоняет text через буфер, возвращает безопасную часть."""
            return _deanon_buf.process(text)

        async def reply_events():
            if not _suppress_text:
                async for event in query_llm_stream(system_prompt, history, mode):
                    yield event

        try:
            async for ev in reply_events():
                ev_type = ev.get("type")

                if ev_type == "delta":
                    raw_delta = ev.get("content", "")
                    if not _first_chunk_logged:
                        logger.info(
                            f"[LLM] mode={mode} time_to_first_chunk={time.monotonic()-_llm_t0:.1f}s"
                        )
                        _first_chunk_logged = True
                    delta = _emit(raw_delta)
                    if delta:
                        current_part += delta
                        yield f"data: {json.dumps({'type': 'delta', 'content': delta}, ensure_ascii=False)}\n\n"

                elif ev_type == "part_end":
                    # Перед концом части сливаем буфер — следующая часть
                    # начнётся с новой строки, незакрытых плейсхолдеров быть не должно.
                    tail = _deanon_buf.flush()
                    if tail:
                        current_part += tail
                        yield f"data: {json.dumps({'type': 'delta', 'content': tail}, ensure_ascii=False)}\n\n"
                    response_parts.append(current_part)
                    current_part = ""
                    yield f"data: {json.dumps({'type': 'part_end'}, ensure_ascii=False)}\n\n"

                elif ev_type == "error":
                    had_error = True
                    msg = ev.get("message", "⚠️ Ошибка")
                    # Перед сообщением об ошибке сначала сольём буфер: возможно
                    # в нём лежит хвост корректного ответа.
                    tail = _deanon_buf.flush()
                    if tail:
                        current_part += tail
                        yield f"data: {json.dumps({'type': 'delta', 'content': tail}, ensure_ascii=False)}\n\n"
                    msg = _pm.deanonymize_response_delta(msg, _policy, anonymizer)
                    sep = "\n\n" if current_part else ""
                    current_part += sep + msg
                    payload = json.dumps({"type": "delta", "content": sep + msg}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    break

                elif ev_type == "done":
                    break
        except Exception as e:
            logger.error(f"[CHAT_STREAM] unexpected: {e}", exc_info=True)
            had_error = True
            err_msg = "⚠️ Ошибка при обработке запроса. Обратитесь к администратору."
            # Слить буфер перед ошибкой — там может быть последний кусок ответа.
            tail = _deanon_buf.flush()
            if tail:
                current_part += tail
                yield f"data: {json.dumps({'type': 'delta', 'content': tail}, ensure_ascii=False)}\n\n"
            current_part += ("\n\n" if current_part else "") + err_msg
            yield f"data: {json.dumps({'type': 'delta', 'content': err_msg}, ensure_ascii=False)}\n\n"

        # Финальный слив буфера на штатном завершении.
        tail = _deanon_buf.flush()
        if tail:
            current_part += tail
            yield f"data: {json.dumps({'type': 'delta', 'content': tail}, ensure_ascii=False)}\n\n"

        if current_part:
            response_parts.append(current_part)

        # Голая проверка контрагента: текста нет, но нужна строка-ответ ассистента,
        # к которой привяжется карточка (check_result) для персиста и перезагрузки.
        if _suppress_text and not response_parts:
            response_parts.append("")

        # ── Critic (ad_check) — пост-проверка обоснованности утверждений ──
        # Доп. LLM-вызов после стрима. Если в черновике есть утверждения,
        # не подтверждённые выгруженной нормативкой, — дописываем блок
        # «⚠️ Замечания проверки». При ошибке/таймауте критик молча
        # пропускается (had_error не выставляем).
        if mode == "ad_check" and not had_error and response_parts:
            try:
                from answer_critic import critique_ad_check
                _critic_t0 = time.monotonic()
                full_answer = "\n\n".join(response_parts)
                critique = await critique_ad_check(
                    user_message=message, rag_docs=rag_docs, draft_answer=full_answer,
                )
                logger.info(
                    "[CRITIC] elapsed=%.1fs", time.monotonic() - _critic_t0,
                )
                if critique.has_issues:
                    bullets = "\n".join(f"- {x}" for x in critique.issues)
                    warning = (
                        "\n\n---\n"
                        "⚠️ **Замечания автоматической проверки**\n\n"
                        "В ответе обнаружены утверждения, которые не подтверждаются "
                        "загруженной нормативной базой. Перед использованием "
                        "сверьте с первоисточником:\n\n"
                        f"{bullets}"
                    )
                    yield (
                        f"data: {json.dumps({'type': 'delta', 'content': warning}, ensure_ascii=False)}\n\n"
                    )
                    # Дописываем в последний part, чтобы предупреждение
                    # сохранилось в БД вместе с ответом.
                    response_parts[-1] = response_parts[-1] + warning
            except Exception as e:
                logger.warning("[CRITIC] post-stream check failed: %s", e)

        # Persist — открываем отдельное соединение, т.к. dependency-сессия `db`
        # уже закрыта к моменту окончания стрима.
        try:
            async with aiosqlite.connect(DB_PATH, timeout=30.0) as wdb:
                await wdb.execute("PRAGMA busy_timeout=30000")
                # Карточку проверки контрагента сохраняем рядом с первым
                # сообщением ответа, чтобы при перезагрузке диалога она
                # отрисовывалась так же, как в live-режиме.
                _check_json = (
                    json.dumps(_card_event["data"], ensure_ascii=False)
                    if _card_event is not None else None
                )
                for _idx, part in enumerate(response_parts):
                    await wdb.execute(
                        "INSERT INTO messages (id, conversation_id, role, content, check_result) VALUES (?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), conv_id, "assistant", part,
                         _check_json if _idx == 0 else None),
                    )
                await wdb.execute(
                    "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conv_id,),
                )
                if not had_error:
                    await wdb.execute(
                        "INSERT INTO chat_stats (id, user_id, mode, sub_category, skill_names, time_minutes) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            user_id,
                            mode,
                            sub_category,
                            skill_names_json,
                            time_minutes,
                        ),
                    )
                await wdb.execute(
                    "INSERT INTO agent_traces (request_id, conversation_id, user_id, scenario_id, legacy_mode, trace_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _plan.trace.request_id,
                        conv_id,
                        str(user_id),
                        _plan.trace.scenario_id,
                        _plan.trace.legacy_mode,
                        json.dumps(_plan.trace.to_log(), ensure_ascii=False),
                    ),
                )
                await wdb.commit()
        except Exception as e:
            logger.error(f"[CHAT_STREAM] persist error: {e}", exc_info=True)

        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

# ── Conversations (#7: ownership) ────────────────────────────────────

@app.get("/api/conversations")
async def list_conversations(payload: dict = Depends(require_auth), db=Depends(get_db)):
    rows = await db.execute_fetchall(
        "SELECT id, title, mode, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 50",
        (payload["sub"],),
    )
    return [dict(r) for r in rows]

@app.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, payload: dict = Depends(require_auth), db=Depends(get_db)):
    # (#7) IDOR fix
    owner = await db.execute_fetchall(
        "SELECT user_id FROM conversations WHERE id = ?", (conv_id,)
    )
    if not owner or owner[0]["user_id"] != payload["sub"]:
        raise HTTPException(403, "Нет доступа к этому диалогу")

    rows = await db.execute_fetchall(
        "SELECT role, content, check_result, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    )
    return [_message_row_to_dict(r) for r in rows]

@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str, payload: dict = Depends(require_auth), db=Depends(get_db)):
    owner = await db.execute_fetchall(
        "SELECT user_id FROM conversations WHERE id = ?", (conv_id,)
    )
    if not owner or owner[0]["user_id"] != payload["sub"]:
        raise HTTPException(403, "Нет доступа к этому диалогу")
    await db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    await db.execute("DELETE FROM conversation_files WHERE conversation_id = ?", (conv_id,))
    await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    await db.commit()
    return {"status": "deleted"}

@app.patch("/api/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: dict, payload: dict = Depends(require_auth), db=Depends(get_db)):
    owner = await db.execute_fetchall(
        "SELECT user_id FROM conversations WHERE id = ?", (conv_id,)
    )
    if not owner or owner[0]["user_id"] != payload["sub"]:
        raise HTTPException(403, "Нет доступа к этому диалогу")
    title = (body.get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(400, "Пустой заголовок")
    await db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))
    await db.commit()
    return {"status": "ok", "title": title}

# ── Bitrix24: создание задачи из чата ─────────────────────────────────

@app.get("/api/bitrix/task-config")
async def bitrix_task_config(payload: dict = Depends(require_auth)):
    """Доступна ли кнопка «Задача Битрикс» (настроены вебхук и ответственный)."""
    return {"enabled": settings.bitrix24_task_enabled}

@app.post("/api/conversations/{conv_id}/bitrix-task")
@limiter.limit("10/minute")
async def create_bitrix_task(
    conv_id: str,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    payload: dict = Depends(require_auth),
    db=Depends(get_db),
):
    """Создать задачу в Битрикс24 по диалогу с вложением Word-файла истории чата.

    Ответственный и соисполнители берутся из конфигурации (оба юриста).
    """
    if not settings.bitrix24_task_enabled:
        raise HTTPException(503, "Интеграция с Битрикс24 не настроена")

    # IDOR: задачу можно создать только по своему диалогу
    owner = await db.execute_fetchall(
        "SELECT user_id FROM conversations WHERE id = ?", (conv_id,)
    )
    if not owner or owner[0]["user_id"] != payload["sub"]:
        raise HTTPException(403, "Нет доступа к этому диалогу")

    title = (title or "").strip()[:250]
    if not title:
        raise HTTPException(400, "Пустой заголовок задачи")

    # Постановщика (ФИО автора обращения) пользователь указывает прямо в
    # описании — поле в форме предзаполнено его логином и редактируемо.
    description = (description or "").strip()

    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Файл слишком большой. Максимум: {MAX_UPLOAD_BYTES // (1024*1024)} МБ")

    safe_name = sanitize_filename(file.filename or "chat.docx")

    client = Bitrix24Client(webhook_url=settings.bitrix24_webhook_url)

    file_id = await client.upload_file(
        safe_name, content_bytes, storage_id=settings.bitrix24_disk_storage_id
    )

    resp = await client.create_task(
        title=title,
        description=description or "",
        responsible_id=settings.bitrix24_task_responsible_id,
        accomplice_ids=settings.bitrix24_task_accomplice_id_list,
        file_ids=[file_id] if file_id else None,
    )

    result = resp.get("result") or {}
    task = result.get("task") or {}
    task_id = task.get("id") or task.get("ID")
    if not task_id:
        logger.error("[BitrixTask] создание задачи не удалось: %s", resp)
        raise HTTPException(502, "Не удалось создать задачу в Битрикс24")

    return {
        "task_id": task_id,
        "url": client.task_url(task_id, settings.bitrix24_task_responsible_id),
        "file_attached": bool(file_id),
    }

# ── Documents (admin only #1) ─────────────────────────────────────────

@app.get("/api/documents")
async def list_documents(payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall(
        "SELECT id, filename, original_filename, category, mode, description, created_at FROM documents ORDER BY created_at DESC"
    )
    return [dict(r) for r in rows]

@app.get("/api/documents/by-mode/{mode}")
async def list_documents_by_mode(mode: str, payload: dict = Depends(require_auth), db=Depends(get_db)):
    # Правый сайдбар чата отображает только внутренние регламенты.
    # Документы других категорий (шаблоны, листки-вкладыши, справочные
    # материалы) сюда не попадают независимо от их mode.
    rows = await db.execute_fetchall(
        "SELECT original_filename, mode FROM documents WHERE category = 'regulation' "
        "ORDER BY original_filename"
    )
    result = []
    for r in rows:
        doc_modes = [m.strip() for m in r["mode"].split(",")]
        if "all" in doc_modes or mode in doc_modes:
            result.append(r["original_filename"])
    return result

@app.post("/api/documents/upload")
@limiter.limit("20/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("regulation"),
    mode: str = Form("all"),
    description: str = Form(""),
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    mode = parse_and_validate_modes(mode)
    validate_file_extension(file.filename)

    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Файл слишком большой. Максимум: {MAX_UPLOAD_BYTES // (1024*1024)} МБ")

    safe_original = sanitize_filename(file.filename)
    ext = Path(safe_original).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"

    save_dir = Path(settings.documents_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / stored_name

    if not str(save_path.resolve()).startswith(str(save_dir.resolve())):
        raise HTTPException(400, "Недопустимое имя файла")

    save_path.write_bytes(content_bytes)
    # Извлечение текста — синхронная CPU-/IO-bound операция (PDF parsing,
    # OCR). Выносим в пул, чтобы не блокировать event loop FastAPI.
    text = await asyncio.to_thread(extract_text, save_path, safe_original)

    # Если извлечение упало — не сохраняем «битый» документ в БД и не индексируем
    # error-маркер в RAG. Файл уже записан, но содержимое не годится для поиска.
    if is_extraction_error(text):
        save_path.unlink(missing_ok=True)
        logger.warning(
            f"[UPLOAD] extraction failed for {safe_original}: {text[:200]}"
        )
        raise HTTPException(
            422,
            f"Не удалось извлечь текст из файла «{safe_original}»: {text}",
        )

    # (Фикс D5) DLP-сканер: проверяем документ на наличие ПДн перед индексацией в RAG.
    # Возвращаем администратору счётчики по типам — значения не раскрываются.
    pdn_markers = dlp_scan(text)
    if pdn_markers:
        logger.warning(
            f"[DLP] file={safe_original} user={payload['sub']} pdn_markers={pdn_markers}"
        )

    doc_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO documents (id, filename, original_filename, category, mode, description, content, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, stored_name, safe_original, category, mode, description, text, payload["sub"]),
    )
    await audit(
        db, payload["sub"], "upload_document", target=safe_original,
        details=f"pdn_markers={pdn_markers}" if pdn_markers else "",
        ip=get_remote_address(request),
    )
    await db.commit()
    logger.info(f"Document uploaded: {safe_original} by {payload['sub']}")

    # Шаблоны документов (category='contract') не индексируются в RAG —
    # они используются как объекты для скачивания, а не как контекст.
    if category != "contract":
        await rag_module.async_index_document(
            doc_id, text, mode, category, safe_original, settings.chroma_path
        )

    return {"id": doc_id, "filename": safe_original, "chars": len(text), "pdn_markers": pdn_markers}

class UpdateDocModeRequest(BaseModel):
    mode: str

@app.patch("/api/documents/{doc_id}/mode")
async def update_document_mode(
    doc_id: str,
    body: UpdateDocModeRequest,
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    mode = parse_and_validate_modes(body.mode)
    rows = await db.execute_fetchall(
        "SELECT content, category, original_filename FROM documents WHERE id = ?", (doc_id,)
    )
    if not rows:
        raise HTTPException(404, "Документ не найден")
    doc = rows[0]
    await db.execute("UPDATE documents SET mode = ? WHERE id = ?", (mode, doc_id))
    await audit(db, payload["sub"], "update_document_mode", target=doc_id, details=f"mode={mode}")
    await db.commit()
    if doc["content"] and doc["category"] != "contract":
        await rag_module.async_index_document(
            doc_id, doc["content"], mode, doc["category"], doc["original_filename"], settings.chroma_path
        )
    return {"ok": True, "mode": mode}

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall("SELECT filename FROM documents WHERE id = ?", (doc_id,))
    if rows:
        filepath = Path(settings.documents_dir) / rows[0]["filename"]
        if filepath.exists():
            filepath.unlink()
    await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    await audit(db, payload["sub"], "delete_document", target=doc_id)
    await db.commit()
    await rag_module.async_delete_document(doc_id, settings.chroma_path)
    return {"ok": True}


# ── Templates (доступны всем авторизованным пользователям) ───────────

@app.get("/api/templates")
async def list_templates(payload: dict = Depends(require_auth), db=Depends(get_db)):
    """Список доступных для скачивания шаблонов (category='contract')."""
    rows = await db.execute_fetchall(
        "SELECT id, original_filename FROM documents WHERE category = 'contract' "
        "ORDER BY original_filename"
    )
    return [{"id": r["id"], "original_filename": r["original_filename"]} for r in rows]


@app.get("/api/templates/{doc_id}/download")
async def download_template(
    doc_id: str,
    payload: dict = Depends(require_auth),
    db=Depends(get_db),
):
    rows = await db.execute_fetchall(
        "SELECT filename, original_filename, category FROM documents WHERE id = ?",
        (doc_id,),
    )
    if not rows:
        raise HTTPException(404, "Шаблон не найден")
    doc = rows[0]
    if doc["category"] != "contract":
        raise HTTPException(403, "Документ не является шаблоном для скачивания")

    docs_dir = Path(settings.documents_dir).resolve()
    file_path = (docs_dir / doc["filename"]).resolve()
    if not str(file_path).startswith(str(docs_dir)) or not file_path.exists():
        raise HTTPException(404, "Файл шаблона недоступен")

    return FileResponse(
        path=str(file_path),
        filename=doc["original_filename"],
        media_type="application/octet-stream",
    )


@app.post("/api/admin/reindex")
async def reindex_documents(
    request: Request,
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Сброс RAG-коллекции и полная переиндексация всех документов из БД.

    Нужно после смены схемы чанкинга (section-aware индексация для крупных
    markdown-файлов): существующие чанки в Chroma переиндексируются заново.
    """
    await rag_module.async_drop_collection(settings.chroma_path)

    rows = await db.execute_fetchall(
        "SELECT id, content, mode, category, original_filename FROM documents"
    )
    indexed = 0
    for d in rows:
        if d["content"] and d["category"] != "contract":
            await rag_module.async_index_document(
                d["id"], d["content"], d["mode"],
                d["category"], d["original_filename"],
                settings.chroma_path,
            )
            indexed += 1

    await audit(
        db, payload["sub"], "reindex_documents",
        details=f"docs={indexed}", ip=get_remote_address(request),
    )
    await db.commit()
    return {"ok": True, "indexed": indexed}


def _read_sys_file(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


@app.get("/api/admin/sysinfo")
async def system_info(payload: dict = Depends(require_admin)):
    """Диагностика ресурсов, доступных контейнеру: CPU, RAM, cgroup-лимиты, диск, GPU.

    Хост может отдавать контейнеру меньше, чем видно в /proc (cgroup-лимиты),
    поэтому показываем и то и другое.
    """
    import shutil

    info: dict = {
        "cpu_count": os.cpu_count(),
        "loadavg_1_5_15": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }

    memory_mb: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                memory_mb[key] = int(rest.split()[0]) // 1024
    except OSError:
        pass
    info["memory_mb"] = memory_mb or None

    # cgroup v2: "max" = без лимита; cpu.max формата "<quota> <period>"
    info["cgroup"] = {
        "memory_max": _read_sys_file("/sys/fs/cgroup/memory.max"),
        "memory_current": _read_sys_file("/sys/fs/cgroup/memory.current"),
        "cpu_max": _read_sys_file("/sys/fs/cgroup/cpu.max"),
    }

    rss_kb: int | None = None
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
                break
    except OSError:
        pass
    info["process_rss_mb"] = rss_kb // 1024 if rss_kb else None

    try:
        du = shutil.disk_usage(settings.documents_dir)
        info["disk_data_gb"] = {
            "total": round(du.total / 1024**3, 1),
            "used": round(du.used / 1024**3, 1),
            "free": round(du.free / 1024**3, 1),
        }
    except OSError:
        info["disk_data_gb"] = None

    info["gpu"] = {
        "nvidia_device": Path("/dev/nvidia0").exists(),
        "nvidia_smi": shutil.which("nvidia-smi") is not None,
    }

    return info


# ── Skill file parser ────────────────────────────────────────────────

def parse_skill_file(raw: str, filename: str) -> dict:
    """Парсит .skill файл с опциональным заголовком.

    Формат:
        name: Название скилла
        mode: ad_check
        ---
        Текст инструкции...

    Если заголовка нет — имя берётся из имени файла без расширения.

    Устойчив к: UTF-8 BOM, ведущим пробелам/пустым строкам, разному регистру
    ключей заголовка (Name:, NAME:, name:).
    """
    name = Path(filename).stem
    mode = "all"

    # Снимаем BOM и ведущие whitespace перед проверкой заголовка.
    stripped = raw.lstrip("﻿").lstrip()
    content = stripped.strip()

    if stripped[:5].lower() == "name:" or stripped[:5].lower() == "mode:":
        parts = stripped.split("---", 1)
        header, body = (parts[0], parts[1].strip()) if len(parts) == 2 else (parts[0], "")
        for line in header.splitlines():
            line = line.strip()
            lower = line.lower()
            if lower.startswith("name:"):
                name = line[5:].strip()
            elif lower.startswith("mode:"):
                mode = line[5:].strip().lower()
        content = body if body else content

    if mode not in VALID_DOC_MODES:
        mode = "all"
    if not content:
        raise ValueError("Файл скилла не содержит текста инструкции")

    return {"name": name, "mode": mode, "content": content}

# ── Skills (admin only #1) ────────────────────────────────────────────

VALID_TIME_CATEGORIES = {"", "requisites", "forms", "summary"}


def _parse_scenario_ids(val: str) -> str | None:
    """Валидирует и возвращает JSON-строку массива scenario_ids или None."""
    if not val:
        return None
    try:
        ids = json.loads(val)
        if isinstance(ids, list) and all(isinstance(x, str) for x in ids):
            return json.dumps(ids, ensure_ascii=False) if ids else None
    except Exception:
        pass
    return None


def _parse_activation_condition(val: str) -> str:
    """Валидирует activation_condition. Допустимые значения: always, disabled, keyword:..."""
    if val in ("always", "disabled"):
        return val
    if val.startswith("keyword:") and len(val) > len("keyword:"):
        return val
    return "always"


@app.get("/api/skills")
async def list_skills(payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall(
        "SELECT id, name, content, mode, time_category, is_active, updated_at, "
        "scenario_ids, activation_condition, priority FROM skills ORDER BY name"
    )
    return [dict(r) for r in rows]

@app.post("/api/skills")
async def create_skill(
    name: str = Form(...), content: str = Form(...), mode: str = Form("all"),
    time_category: str = Form(""),
    scenario_ids: str = Form(""),
    activation_condition: str = Form("always"),
    priority: int = Form(100),
    payload: dict = Depends(require_admin), db=Depends(get_db),
):
    if time_category not in VALID_TIME_CATEGORIES:
        time_category = ""
    scenario_ids_clean = _parse_scenario_ids(scenario_ids)
    activation_condition_clean = _parse_activation_condition(activation_condition)
    skill_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO skills (id, name, content, mode, time_category, "
        "scenario_ids, activation_condition, priority, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (skill_id, name, content, mode, time_category or None,
         scenario_ids_clean, activation_condition_clean, priority, payload["sub"]),
    )
    await audit(db, payload["sub"], "create_skill", target=name)
    await db.commit()
    return {"id": skill_id}

@app.put("/api/skills/{skill_id}")
async def update_skill(
    skill_id: str, name: str = Form(...), content: str = Form(...), mode: str = Form("all"),
    time_category: str = Form(""),
    scenario_ids: str = Form(""),
    activation_condition: str = Form("always"),
    priority: int = Form(100),
    payload: dict = Depends(require_admin), db=Depends(get_db),
):
    if time_category not in VALID_TIME_CATEGORIES:
        time_category = ""
    scenario_ids_clean = _parse_scenario_ids(scenario_ids)
    activation_condition_clean = _parse_activation_condition(activation_condition)
    await db.execute(
        "UPDATE skills SET name=?, content=?, mode=?, time_category=?, "
        "scenario_ids=?, activation_condition=?, priority=?, "
        "updated_by=?, updated_at=datetime('now') WHERE id=?",
        (name, content, mode, time_category or None,
         scenario_ids_clean, activation_condition_clean, priority,
         payload["sub"], skill_id),
    )
    await audit(db, payload["sub"], "update_skill", target=name)
    await db.commit()
    return {"ok": True}

@app.post("/api/skills/upload")
@limiter.limit("20/minute")
async def upload_skill_file(
    request: Request,
    file: UploadFile = File(...),
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    if not file.filename.endswith(".skill"):
        raise HTTPException(400, "Поддерживается только формат .skill")
    content_bytes = await file.read()
    if len(content_bytes) > 512 * 1024:
        raise HTTPException(400, "Файл скилла слишком большой (макс. 512 КБ)")
    try:
        raw = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Файл должен быть в кодировке UTF-8")
    try:
        parsed = parse_skill_file(raw, sanitize_filename(file.filename))
    except ValueError as e:
        raise HTTPException(400, str(e))
    skill_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO skills (id, name, content, mode, updated_by) VALUES (?, ?, ?, ?, ?)",
        (skill_id, parsed["name"], parsed["content"], parsed["mode"], payload["sub"]),
    )
    await audit(db, payload["sub"], "upload_skill", target=parsed["name"], ip=get_remote_address(request))
    await db.commit()
    logger.info(f"Skill uploaded from file: {parsed['name']} mode={parsed['mode']}")
    return {"id": skill_id, "name": parsed["name"], "mode": parsed["mode"]}

@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str, payload: dict = Depends(require_admin), db=Depends(get_db)):
    await db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    await audit(db, payload["sub"], "delete_skill", target=skill_id)
    await db.commit()
    return {"ok": True}

# ── Mode Instructions (admin only) ───────────────────────────────────

# AdminModes UI показывает только _VISIBLE_MODES (определён в начале файла).
# Под-сценарии document_gen (contract_analysis / claim_drafting / notice_drafting)
# — легаси Stage 1: их строки в mode_instructions на ~70% дублируют document_gen,
# поэтому скрыты из UI. Базовый document_gen-промпт ведёт под-сценарии через
# секции «РЕЖИМ А/Б/В/Г» — admin редактирует одну точку.


@app.get("/api/admin/modes")
async def list_modes(payload: dict = Depends(require_admin), db=Depends(get_db)):
    placeholders = ",".join("?" * len(_VISIBLE_MODES))
    rows = await db.execute_fetchall(
        f"SELECT mode, label, instruction, updated_at FROM mode_instructions "
        f"WHERE mode IN ({placeholders}) ORDER BY mode",
        _VISIBLE_MODES,
    )
    return [dict(r) for r in rows]

@app.put("/api/admin/modes/{mode_id}")
async def update_mode_instruction(
    mode_id: str,
    instruction: str = Form(...),
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    rows = await db.execute_fetchall(
        "SELECT mode FROM mode_instructions WHERE mode = ?", (mode_id,)
    )
    if not rows:
        raise HTTPException(404, "Режим не найден")
    await db.execute(
        "UPDATE mode_instructions SET instruction = ?, updated_by = ?, updated_at = datetime('now') WHERE mode = ?",
        (instruction, payload["sub"], mode_id),
    )
    await audit(db, payload["sub"], "update_mode_instruction", target=mode_id)
    await db.commit()
    return {"ok": True}

# ── Users (admin only) ───────────────────────────────────────────────

@app.get("/api/users")
async def list_users(payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at"
    )
    return [dict(r) for r in rows]

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str, payload: dict = Depends(require_admin), db=Depends(get_db)):
    if user_id == payload["sub"]:
        raise HTTPException(400, "Нельзя удалить собственный аккаунт")
    target = await db.execute_fetchall("SELECT role FROM users WHERE id = ?", (user_id,))
    if target and target[0]["role"] == "dashboard":
        raise HTTPException(400, "Нельзя удалить пользователя с ролью dashboard")
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await audit(db, payload["sub"], "delete_user", target=user_id)
    await db.commit()
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Пароль должен быть не менее 8 символов")
        return v

@app.patch("/api/users/{user_id}/password")
async def change_dashboard_password(
    user_id: str,
    body: ChangePasswordRequest,
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    target = await db.execute_fetchall("SELECT role, username FROM users WHERE id = ?", (user_id,))
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if target[0]["role"] != "dashboard":
        raise HTTPException(400, "Смена пароля доступна только для роли dashboard")
    new_hash = await hash_password(body.new_password)
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    await audit(db, payload["sub"], "change_password", target=target[0]["username"])
    await db.commit()
    return {"ok": True}

# ── Admin: all chats ─────────────────────────────────────────────────

@app.get("/api/admin/chats")
async def admin_list_chats(payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall("""
        SELECT c.id, c.title, c.mode, c.created_at, c.updated_at,
               u.username, u.id as user_id
        FROM conversations c
        JOIN users u ON c.user_id = u.id
        ORDER BY c.updated_at DESC
        LIMIT 200
    """)
    return [dict(r) for r in rows]

@app.get("/api/admin/chats/{conv_id}/messages")
async def admin_get_chat_messages(conv_id: str, payload: dict = Depends(require_admin), db=Depends(get_db)):
    rows = await db.execute_fetchall(
        "SELECT role, content, check_result, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    )
    return [_message_row_to_dict(r) for r in rows]

# ── Dashboard stats ───────────────────────────────────────────────────

_MODE_LABELS = {
    "consult": "Консультация",
    "presentation": "Презентация для бизнеса",
    "ad_check": "Проверка рекламы",
    "document_gen": "Документы",
    "regulation": "Регламенты",
}

_SUB_LABELS = {
    "summary": "Саммари по договору",
    "claim": "Претензии / письма / ДС",
    "forms": "Типовые формы",
    "requisites": "Реквизиты / документы",
}

_TREND_BY_MONTH_THRESHOLD = 60   # точек/дней — выше переходим на помесячный тренд


# ── Projects — публичный дашборд + админская управляющая панель ────────────────

def _project_row_to_public(r: dict) -> dict:
    """Сериализация записи projects для публичного API (без author_id)."""
    return {
        "id": r["id"],
        "slug": r["slug"],
        "title": r["title"],
        "slide_task": r["slide_task"] or "",
        "slide_complexity": r["slide_complexity"] or "",
        "slide_action": r["slide_action"] or "",
        "slide_effect": r["slide_effect"] or "",
        "slide_next": r["slide_next"] or "",
        "author_name": r["author_name"] or "",
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@app.get("/api/public/projects")
async def public_list_projects(db=Depends(get_db)):
    """Публичный список опубликованных проектов (без авторизации)."""
    if not settings.public_projects_enabled:
        raise HTTPException(404, "Публикация проектов отключена")
    rows = await db.execute_fetchall(
        "SELECT id, slug, title, slide_task, slide_complexity, slide_action, "
        "slide_effect, slide_next, author_name, created_at, updated_at "
        "FROM projects WHERE status = 'published' ORDER BY datetime(created_at) DESC"
    )
    return [_project_row_to_public(dict(r)) for r in rows]


@app.get("/api/public/projects/{slug}")
async def public_get_project(slug: str, db=Depends(get_db)):
    """Публичный доступ к одному проекту по slug."""
    if not settings.public_projects_enabled:
        raise HTTPException(404, "Публикация проектов отключена")
    rows = await db.execute_fetchall(
        "SELECT id, slug, title, slide_task, slide_complexity, slide_action, "
        "slide_effect, slide_next, author_name, created_at, updated_at "
        "FROM projects WHERE slug = ? AND status = 'published'",
        (slug,),
    )
    if not rows:
        raise HTTPException(404, "Проект не найден")
    return _project_row_to_public(dict(rows[0]))


@app.get("/api/admin/projects")
async def admin_list_projects(payload: dict = Depends(require_admin), db=Depends(get_db)):
    """Полный список проектов (включая archived/draft) — только для админа."""
    rows = await db.execute_fetchall(
        "SELECT id, slug, title, slide_task, slide_complexity, slide_action, "
        "slide_effect, slide_next, author_id, author_name, conversation_id, "
        "status, created_at, updated_at "
        "FROM projects ORDER BY datetime(created_at) DESC"
    )
    return [dict(r) for r in rows]


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    slide_task: Optional[str] = None
    slide_complexity: Optional[str] = None
    slide_action: Optional[str] = None
    slide_effect: Optional[str] = None
    slide_next: Optional[str] = None
    status: Optional[str] = None  # published / archived


@app.patch("/api/admin/projects/{project_id}")
async def admin_update_project(
    project_id: str,
    body: UpdateProjectRequest,
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Обновить поля проекта. None-значения не меняются."""
    rows = await db.execute_fetchall(
        "SELECT id FROM projects WHERE id = ?", (project_id,),
    )
    if not rows:
        raise HTTPException(404, "Проект не найден")

    updates: list[str] = []
    params: list = []
    for fld in ("title", "slide_task", "slide_complexity",
                "slide_action", "slide_effect", "slide_next"):
        v = getattr(body, fld)
        if v is not None:
            updates.append(f"{fld} = ?")
            params.append(v.strip() if isinstance(v, str) else v)
    if body.status is not None:
        if body.status not in ("published", "archived", "draft"):
            raise HTTPException(400, "Недопустимый status")
        updates.append("status = ?")
        params.append(body.status)
    if not updates:
        raise HTTPException(400, "Нет полей для обновления")
    updates.append("updated_at = datetime('now')")
    params.append(project_id)

    await db.execute(
        f"UPDATE projects SET {', '.join(updates)} WHERE id = ?", tuple(params),
    )
    await audit(
        db, payload["sub"], "update_project", target=project_id,
        details=f"fields={[f for f in updates if f.split(' ')[0] != 'updated_at']}",
    )
    await db.commit()
    return {"ok": True}


@app.delete("/api/admin/projects/{project_id}")
async def admin_delete_project(
    project_id: str,
    payload: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Жёсткое удаление проекта (для архивации используйте PATCH status)."""
    rows = await db.execute_fetchall(
        "SELECT slug FROM projects WHERE id = ?", (project_id,),
    )
    if not rows:
        raise HTTPException(404, "Проект не найден")
    await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await audit(db, payload["sub"], "delete_project", target=project_id)
    await db.commit()
    return {"ok": True}


def _resolve_date_range(
    period: str,
    date_from: str | None,
    date_to: str | None,
    today,
) -> tuple[object, object, str, list]:
    """Возвращает (start, end, where_clause, where_params).

    start = None означает «весь период» (без нижней границы фильтра).
    """
    if date_from and date_to:
        try:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(400, "Неверный формат даты (ожидается YYYY-MM-DD)")
        return (
            start, end,
            "created_at >= ? AND created_at <= ?",
            [start.isoformat(), (end + timedelta(days=1)).isoformat()],
        )

    _DELTA_DAYS = {"week": 6, "month": 29, "year": 364}
    if period in _DELTA_DAYS:
        start = today - timedelta(days=_DELTA_DAYS[period])
        return start, today, "created_at >= ?", [start.isoformat()]

    # period == "all" или неизвестный → без фильтра
    return None, today, "1=1", []


def _aggregate_by_mode(rows) -> list[dict]:
    stats: dict[str, dict] = {}
    for r in rows:
        m = r["mode"]
        bucket = stats.setdefault(
            m, {"mode": m, "label": _MODE_LABELS.get(m, m), "count": 0, "minutes": 0},
        )
        bucket["count"] += 1
        bucket["minutes"] += r["time_minutes"]
    return list(stats.values())


def _aggregate_sub_categories(rows) -> list[dict]:
    """Подкатегории относятся только к document_gen."""
    stats: dict[str, dict] = {}
    for r in rows:
        if r["mode"] != "document_gen" or not r["sub_category"]:
            continue
        c = r["sub_category"]
        bucket = stats.setdefault(
            c, {"sub_category": c, "label": _SUB_LABELS.get(c, c), "count": 0, "minutes": 0},
        )
        bucket["count"] += 1
        bucket["minutes"] += r["time_minutes"]
    return list(stats.values())


def _top_skills(rows, limit: int = 10) -> list[dict]:
    counts: dict[str, int] = {}
    for r in rows:
        if not r["skill_names"]:
            continue
        try:
            names = json.loads(r["skill_names"])
        except Exception:
            continue
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": n, "count": c}
        for n, c in sorted(counts.items(), key=lambda x: -x[1])[:limit]
    ]


def _build_trend(rows, start, end) -> list[dict]:
    """Тренд по дням; если период «всё» и точек > _TREND_BY_MONTH_THRESHOLD —
    группируем по месяцам."""
    day_counts: dict[str, int] = {}
    if start:
        cur = start
        while cur <= end:
            day_counts[cur.isoformat()] = 0
            cur += timedelta(days=1)
    for r in rows:
        day = r["created_at"][:10]
        if start:
            if day in day_counts:
                day_counts[day] += 1
        else:
            day_counts[day] = day_counts.get(day, 0) + 1

    trend = [{"date": d, "count": c} for d, c in sorted(day_counts.items())]

    if not start and len(trend) > _TREND_BY_MONTH_THRESHOLD:
        month_counts: dict[str, int] = {}
        for r in rows:
            month = r["created_at"][:7]
            month_counts[month] = month_counts.get(month, 0) + 1
        trend = [{"date": m, "count": c} for m, c in sorted(month_counts.items())]
    return trend


def _build_hourly(rows) -> list[dict]:
    hour_counts = {f"{h:02d}": 0 for h in range(24)}
    for r in rows:
        h = r["created_at"][11:13]
        if h in hour_counts:
            hour_counts[h] += 1
    return [{"hour": h, "count": c} for h, c in sorted(hour_counts.items())]


@app.get("/api/dashboard/stats")
async def dashboard_stats(
    period: str = "month",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    payload: dict = Depends(require_dashboard),
    db=Depends(get_db),
):
    today = date.today()

    start, end, where_clause, where_params = _resolve_date_range(
        period, date_from, date_to, today,
    )

    rows = await db.execute_fetchall(
        f"SELECT user_id, mode, sub_category, skill_names, time_minutes, created_at "
        f"FROM chat_stats WHERE {where_clause} ORDER BY created_at",
        where_params,
    )

    total = len(rows)
    total_minutes = sum(r["time_minutes"] for r in rows)
    unique_users = len({r["user_id"] for r in rows if r["user_id"]})

    by_mode = _aggregate_by_mode(rows)
    by_sub_category = _aggregate_sub_categories(rows)
    top_skills = _top_skills(rows)
    trend = _build_trend(rows, start, end)
    by_hour = _build_hourly(rows)

    if start:
        num_days = max(1, (end - start).days + 1)
    elif trend:
        num_days = max(1, len(trend))
    else:
        num_days = 1
    avg_per_day = round(total / num_days, 1) if total > 0 else 0

    return {
        "total_requests": total,
        "total_minutes": total_minutes,
        "total_hours": round(total_minutes / 60, 1),
        "unique_users": unique_users,
        "avg_per_day": avg_per_day,
        "by_mode": by_mode,
        "by_sub_category": by_sub_category,
        "top_skills": top_skills,
        "trend": trend,
        "by_hour": by_hour,
    }

# ── Bitrix24 webhook endpoint ─────────────────────────────────────────

@app.post("/bitrix/webhook")
async def bitrix24_webhook(request: Request):
    """Входящий webhook от Bitrix24 Chat.

    Аутентификация: application_token / webhook_secret из .env
    (BITRIX24_APPLICATION_TOKEN или BITRIX24_WEBHOOK_SECRET).

    Полный цикл: token check → channel session → save user msg →
    orchestrator → RAG → LLM → save response → Bitrix24Client.send_message().
    """
    if not settings.bitrix24_enabled:
        logger.warning("[BITRIX24] Запрос на /bitrix/webhook отклонён: BITRIX24_ENABLED=false")
        raise HTTPException(status_code=503, detail="Bitrix24 интеграция не активирована")

    # ── 1. Проверка токена ────────────────────────────────────────────
    application_token = request.query_params.get("application_token")
    webhook_secret = request.query_params.get("secret")
    if not verify_webhook_token(application_token=application_token, webhook_secret=webhook_secret):
        raise HTTPException(status_code=403, detail="Неверный токен Bitrix24 webhook")

    # ── 2. Парсинг payload ────────────────────────────────────────────
    try:
        raw_payload = await request.json()
    except Exception:
        raw_payload = {}

    try:
        inbound = Bitrix24Adapter.normalize_inbound(raw_payload)
    except Exception as exc:
        logger.warning("[Bitrix24Webhook] payload parse error: %s", exc)
        return JSONResponse({"ok": True, "error": "payload parse error"})

    if not inbound.text.strip():
        return JSONResponse({"ok": True, "skipped": "empty text"})

    external_user_id = inbound.external_user_id or "unknown"
    external_chat_id = inbound.external_chat_id or ""
    dialog_id = external_chat_id or external_user_id

    logger.info(
        "[Bitrix24Webhook] user=%s chat=%s text=%r",
        external_user_id, external_chat_id, inbound.text[:100],
    )

    # ── 3. Channel session: get or create ─────────────────────────────
    bot_user_id = settings.bitrix24_bot_user_id
    conv_id: str
    internal_user_id: str

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=30000")

        async with db.execute(
            "SELECT conversation_id, internal_user_id FROM channel_sessions "
            "WHERE channel=? AND external_user_id=? AND external_chat_id=?",
            ("bitrix24", external_user_id, external_chat_id),
        ) as cur:
            row = await cur.fetchone()

        if row:
            conv_id = row["conversation_id"]
            internal_user_id = row["internal_user_id"] or bot_user_id
            await db.execute(
                "UPDATE channel_sessions SET updated_at=datetime('now') "
                "WHERE channel=? AND external_user_id=? AND external_chat_id=?",
                ("bitrix24", external_user_id, external_chat_id),
            )
        else:
            conv_id = str(uuid.uuid4())
            internal_user_id = bot_user_id
            await db.execute(
                "INSERT INTO channel_sessions "
                "(channel, external_user_id, external_chat_id, internal_user_id, conversation_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("bitrix24", external_user_id, external_chat_id, internal_user_id, conv_id),
            )
            await db.execute(
                "INSERT INTO conversations (id, user_id, title, mode) VALUES (?, ?, ?, ?)",
                (conv_id, internal_user_id, f"Bitrix24 {external_user_id}", "consult"),
            )
            logger.info(
                "[Bitrix24Webhook] new session conv_id=%s user=%s", conv_id, external_user_id
            )

        # ── 4. Сохранить сообщение пользователя ──────────────────────
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conv_id, "user", inbound.text),
        )
        await db.commit()

    # ── 5. Orchestrator: build_plan ───────────────────────────────────
    ctx, scenario_hint = Bitrix24Adapter.to_request_context(
        inbound,
        internal_user_id=internal_user_id,
        conversation_id=conv_id,
    )

    active_scenario_id = scenario_hint or "legal_consultation"
    scenario_cfg = ScenarioRegistry.get_instance().get(active_scenario_id)
    b24_mode = scenario_cfg.legacy_mode_group or "consult"

    b24_plan = None
    if _orchestrator:
        try:
            pre_decision = AgentRouterDecision(
                scenario_id=active_scenario_id,
                legacy_mode=b24_mode,
                intent=None,
                confidence=0.9,
                reason="bitrix24 adapter",
                clarification=None,
                alternatives=[],
            )
            b24_plan = await _orchestrator.build_plan(ctx, router_decision=pre_decision)
            logger.info(
                "[Bitrix24Webhook] plan: scenario=%s skills=%d history=%d",
                b24_plan.scenario.id, len(b24_plan.skills), len(b24_plan.memory.messages),
            )
        except Exception as exc:
            logger.error("[Bitrix24Webhook] build_plan failed: %s", exc, exc_info=True)

    # ── 6. История диалога ────────────────────────────────────────────
    if b24_plan is not None:
        b24_history = [dict(m) for m in b24_plan.memory.messages]
    else:
        async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY created_at",
                (conv_id,),
            ) as cur:
                rows = await cur.fetchall()
        b24_history = [{"role": r["role"], "content": r["content"]} for r in rows]

    # ── 7. RAG поиск ─────────────────────────────────────────────────
    b24_rag_docs: list[dict] = []
    try:
        top_k = settings.top_k_for_mode(b24_mode)
        b24_rag_docs = await rag_module.async_search(
            inbound.text, b24_mode, top_k, settings.chroma_path
        )
        logger.info("[Bitrix24Webhook] RAG docs=%d mode=%s", len(b24_rag_docs), b24_mode)
    except Exception as exc:
        logger.warning("[Bitrix24Webhook] RAG failed: %s", exc)

    # ── 8. System prompt ──────────────────────────────────────────────
    # Orchestrator является единственным путём сборки prompt. Если build_plan
    # упал — отвечаем пользователю generic-сообщением и не пытаемся работать
    # через параллельную реализацию (раньше тут был legacy build_system_prompt,
    # удалённый в Группе 3 рефакторинга).
    if b24_plan is None:
        logger.error(
            "[Bitrix24Webhook] build_plan не выполнен — отвечаем generic-сообщением"
        )
        full_response = (
            "⚠️ Сервис временно недоступен. Попробуйте позже или обратитесь "
            "к администратору."
        )
    else:
        b24_system_prompt = await _orchestrator.get_prompt_assembler().build(
            b24_plan, b24_rag_docs, templates=[], tool_results=[], skills=list(b24_plan.skills),
        )

        # ── 9. LLM (не-стримящий) ─────────────────────────────────────────
        try:
            b24_parts = await query_llm(b24_system_prompt, b24_history, b24_mode)
            full_response = "\n\n".join(b24_parts)
        except Exception as exc:
            logger.error("[Bitrix24Webhook] LLM error: %s", exc, exc_info=True)
            full_response = "⚠️ Произошла ошибка при обработке запроса. Попробуйте позже."

    # ── 10. Сохранить ответ ───────────────────────────────────────────
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conv_id, "assistant", full_response),
        )
        await db.execute(
            "UPDATE conversations SET updated_at=datetime('now') WHERE id=?", (conv_id,)
        )
        await db.commit()

    # ── 11. Отправить ответ в Bitrix24 ────────────────────────────────
    b24_client = Bitrix24Client()
    await b24_client.send_message(dialog_id, full_response)

    return JSONResponse({"ok": True, "conv_id": conv_id})


# ── Global error handler (#12) ────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # FastAPI обычно перехватывает HTTPException / RequestValidationError
    # своими дефолтными обработчиками раньше нас, но на всякий случай
    # явно пробрасываем их: они несут осмысленные status_code и detail,
    # которые нельзя подменять на 500.
    if isinstance(exc, HTTPException):
        raise exc
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера. Обратитесь к администратору."},
    )
