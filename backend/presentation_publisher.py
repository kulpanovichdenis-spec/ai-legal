"""
Парсер markdown-презентации и публикатор записей в таблицу projects.

Используется в режиме «Презентация для бизнеса». LLM выдаёт сборку строго
в формате:

    ## Слайд 1 — Задача
    [текст]

    ## Слайд 2 — Сложность
    [текст]

    ## Слайд 3 — Что сделано
    - буллет
    - буллет

    ## Слайд 4 — Эффект
    - буллет
    - буллет

    ## Слайд 5 — Дальше   (опционально)
    - буллет

Парсер забирает текст под каждым заголовком, slugify-ит заголовок проекта,
вставляет/обновляет запись по conversation_id.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger("legal-assistant")


# Шаблон команды публикации от пользователя. Принимаем разговорные варианты.
_PUBLISH_INTENT_RE = re.compile(
    r"^\s*(?:опубликовать|опубликуй|публикуй|давай\s+публикуй|"
    r"да,?\s+публикуй|publish)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_publish_intent(message: str) -> bool:
    """Является ли сообщение пользователя командой публикации."""
    return bool(_PUBLISH_INTENT_RE.match(message or ""))


# Шапка слайда: «## Слайд N — Метка» или «## Слайд N - Метка» / «## Слайд N. Метка».
_SLIDE_HEADER_RE = re.compile(
    r"^##\s*Слайд\s+(\d+)\s*[—\-.:]\s*([^\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)

# Карта label → ключ поля БД (нормализация регистра/окончаний).
_LABEL_TO_FIELD = {
    "задача": "slide_task",
    "сложность": "slide_complexity",
    "что сделано": "slide_action",
    "сделано": "slide_action",
    "эффект": "slide_effect",
    "дальше": "slide_next",
    "следующие шаги": "slide_next",
}


@dataclass
class ParsedPresentation:
    title: Optional[str] = None
    authors: Optional[str] = None
    slides: dict[str, str] = field(default_factory=dict)
    raw: str = ""


_TITLE_RE = re.compile(r"^#\s+(?!#)(.+)$", re.MULTILINE)
_AUTHORS_RE = re.compile(
    r"^\s*(?:\*\*)?\s*Авторы\s*:?\s*(?:\*\*)?\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_presentation(markdown_text: str) -> ParsedPresentation:
    """Извлечь заголовок, авторов и 4–5 блоков слайдов из markdown-ответа LLM."""
    text = (markdown_text or "").strip()

    # Заголовок — первая строка, начинающаяся с одиночного '# '.
    title = None
    tm = _TITLE_RE.search(text)
    if tm:
        title = tm.group(1).strip().strip("«»\"'")

    # Авторы — строка вида «**Авторы:** Иванов И., Петрова М.»
    authors = None
    am = _AUTHORS_RE.search(text)
    if am:
        authors = am.group(1).strip().strip(".,;")

    matches = list(_SLIDE_HEADER_RE.finditer(text))
    slides: dict[str, str] = {}
    for i, m in enumerate(matches):
        label = m.group(2).strip().lower().rstrip(":.")
        # Нормализуем «что сделано» / «сделано» / «эффект» / «дальше».
        field_key = None
        for k, v in _LABEL_TO_FIELD.items():
            if label.startswith(k):
                field_key = v
                break
        if not field_key:
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # Убираем хвостовой разделитель/инструктивную строку («Готова к публикации…»).
        body = re.sub(r"\n---\s*$", "", body)
        body = re.sub(
            r"\n\s*Готова\s+к\s+публикации.*$", "", body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        body = body.strip()
        if body:
            slides[field_key] = body
    return ParsedPresentation(title=title, authors=authors, slides=slides, raw=text)


# Простая транслитерация для slug (БГОСТ-ish, без зависимости от unidecode).
_TRANSLIT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z",
    "и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
    "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch",
    "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
}


def _slugify(title: str) -> str:
    s = (title or "").lower().strip()
    out = []
    for ch in s:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() or ch == "-":
            out.append(ch)
        elif ch.isspace() or ch in "_.,;:/\\":
            out.append("-")
    slug = "".join(out)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80] or "project"


async def _generate_unique_slug(
    db: aiosqlite.Connection, base_title: str,
) -> str:
    base = _slugify(base_title)
    # Сначала пробуем чистый slug, потом с суффиксами -2, -3, …
    for attempt in range(0, 100):
        candidate = base if attempt == 0 else f"{base}-{attempt + 1}"
        async with db.execute(
            "SELECT 1 FROM projects WHERE slug = ?", (candidate,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return candidate
    # Совсем редкий случай — добавляем uuid suffix.
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _derive_title_from_slides(slides: dict[str, str]) -> str:
    """Если LLM не дала явного заголовка, берём первое предложение из «Задача»."""
    task = (slides.get("slide_task") or "").strip()
    if not task:
        return "Проект юридической службы"
    # Первое предложение или ≤90 символов.
    first = re.split(r"(?<=[.!?])\s+", task, maxsplit=1)[0]
    return (first or task)[:90].rstrip(".!? ")


async def publish_presentation(
    db: aiosqlite.Connection,
    conversation_id: str,
    user_id: str,
    user_name: str,
    parsed: ParsedPresentation,
    title_override: Optional[str] = None,
) -> dict:
    """Создать запись или обновить существующую по conversation_id.

    Возвращает dict с id, slug, title, status. Бросает ValueError, если
    обязательные слайды (Задача, Что сделано, Эффект) отсутствуют —
    публиковать пустые карточки не стоит.
    """
    required = ["slide_task", "slide_action", "slide_effect"]
    missing = [r for r in required if not parsed.slides.get(r)]
    if missing:
        raise ValueError(
            "В презентации не хватает обязательных слайдов: "
            + ", ".join(missing)
        )

    title = (
        title_override
        or parsed.title
        or _derive_title_from_slides(parsed.slides)
    ).strip()
    # Авторы — из markdown («**Авторы:** Иванов И., Петрова М.»);
    # fallback на user_name (логин админа), если бот не указал.
    author_name = (parsed.authors or user_name or "Юридическая служба").strip()

    # Существует ли уже карточка для этого диалога? — обновляем.
    async with db.execute(
        "SELECT id, slug FROM projects WHERE conversation_id = ?",
        (conversation_id,),
    ) as cur:
        existing = await cur.fetchone()

    now = datetime.utcnow().isoformat(timespec="seconds")
    if existing:
        project_id, slug = existing[0], existing[1]
        await db.execute(
            "UPDATE projects SET title = ?, author_name = ?, slide_task = ?, "
            "slide_complexity = ?, slide_action = ?, slide_effect = ?, "
            "slide_next = ?, updated_at = ?, status = 'published' WHERE id = ?",
            (
                title, author_name,
                parsed.slides.get("slide_task"),
                parsed.slides.get("slide_complexity"),
                parsed.slides.get("slide_action"),
                parsed.slides.get("slide_effect"),
                parsed.slides.get("slide_next"),
                now, project_id,
            ),
        )
        await db.commit()
        logger.info("[PROJECTS] updated %s slug=%s", project_id, slug)
        return {"id": project_id, "slug": slug, "title": title, "updated": True}
    else:
        project_id = str(uuid.uuid4())
        slug = await _generate_unique_slug(db, title)
        await db.execute(
            "INSERT INTO projects (id, slug, title, slide_task, slide_complexity, "
            "slide_action, slide_effect, slide_next, author_id, author_name, "
            "conversation_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            (
                project_id, slug, title,
                parsed.slides.get("slide_task"),
                parsed.slides.get("slide_complexity"),
                parsed.slides.get("slide_action"),
                parsed.slides.get("slide_effect"),
                parsed.slides.get("slide_next"),
                user_id, author_name, conversation_id, now, now,
            ),
        )
        await db.commit()
        logger.info("[PROJECTS] inserted %s slug=%s by %s", project_id, slug, user_id)
        return {"id": project_id, "slug": slug, "title": title, "updated": False}
