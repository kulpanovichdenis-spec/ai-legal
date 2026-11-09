"""
Reference extractor: вытаскивает из текста чанка ссылки на внешние нормативные
документы и пункты внутренних регламентов — для multi-hop RAG.

Идея: юридические ссылки имеют жёсткий синтаксис, regex покрывает большинство
случаев без LLM-вызова. На каждый чанк выполняется быстрое сканирование
~5 регулярок, результат — список канонических фраз («ФЗ-38 статья 24»,
«пункт 4.2 Положения о договорной работе»), которые можно скормить как
follow-up запросы в RAG для подтягивания целевых фрагментов.

Что распознаётся:
  - ст. N (Федерального закона | ФЗ-XX | КоАП РФ | ГК РФ | НК РФ)
  - Федеральный закон № XX (отдельно стоящий)
  - Постановление Правительства РФ № XXX
  - п. N.N <Положения|Регламента|Стандарта|Политики|Инструкции> о <NN>
  - п. N.N + последующая принадлежность к документу

Что НЕ распознаётся (out of scope для v1):
  - сноски/footnotes без явного указания акта;
  - ссылки на судебную практику (определения КС, ВС и т. п.);
  - международные конвенции.
"""

from __future__ import annotations

import re

# ── 1. ст. N + (ФЗ-XX | Федеральный закон | КоАП | ГК | НК | ТК | УК | ЖК) ──────
# Захватываем номер статьи и идентификатор кодекса/ФЗ.
_FZ_ARTICLE_RE = re.compile(
    r"\b(?:ст(?:атья|атьи|\.)\s*)(\d+(?:\.\d+)*)"
    r"\s+(?:ФЗ[-\s]*(?P<fz>\d+)"
    r"|Федеральн\w+\s+закон\w*(?:\s+от\s+[\d.]+)?\s*№?\s*(?P<fz2>\d+)"
    r"|(?P<code>КоАП|ГК|НК|ТК|УК|ЖК|ЗК|БК|АПК|ГПК|УПК)(?:\s+РФ)?)",
    re.IGNORECASE,
)

# ── 2. ФЗ-XX отдельно (например, «по ФЗ-38», «согласно ФЗ № 38») ───────────────
_FZ_STANDALONE_RE = re.compile(
    r"\bФЗ[-\s]*№?\s*(\d+)\b",
    re.IGNORECASE,
)

# ── 3. Постановление Правительства РФ № XXX (дата опциональна) ─────────────────
_PP_RE = re.compile(
    r"\bПостановлени\w*\s+Правительства(?:\s+РФ)?"
    r"(?:\s+от\s+([\d.]+))?\s+№?\s*(\d+)",
    re.IGNORECASE,
)

# Терминатор «about»-фразы для внутренних ссылок: ожидаем сильную пунктуацию
# ИЛИ начало следующей ссылки («… и пункту 3.1», «… и ст. 2»). Не стопаем
# на голом «и»/«или» — иначе «договорной работе» обрежется до «договорной».
_ABOUT_END = (
    r"(?=[,.;:)\]]"
    r"|\s+и\s+(?:п(?:ункт(?:а|у|ом|ах)?|\.)|ст(?:атья|атьи|\.))"
    r"|\s+или\s+(?:п(?:ункт(?:а|у|ом|ах)?|\.)|ст(?:атья|атьи|\.))"
    r"|$)"
)

# Не даём «about» начаться с союза/частицы — иначе «Регламента и ст. 2»
# выдаст артефакт about="и ст".
_ABOUT_START_NEG = r"(?!(?:и|или|либо|а|но)\b)"

# ── 4. п. N.N [прилагательное] <Положения|Регламента|...> [о <NN>] ─────────────
# «согласно п. 4.2 Положения о договорной работе»
# «пункт 3.1 Регламента маркетинговых активностей»
# «пункт 1.2 Антикоррупционной политики»  ← одно прилагательное перед doc_type.
_INTERNAL_REF_RE = re.compile(
    r"\b(?:п(?:ункт(?:а|у|ом|ах)?|\.)\s*)(\d+(?:\.\d+)*)"
    r"\s+(?:(?P<adj>[А-ЯЁа-яё][А-Яа-яёЁ-]+)\s+)?"
    r"(?P<doc_type>Положени\w+|Регламент\w*|Стандарт\w*|Политик\w+|"
    r"Инструкци\w+|Порядк\w*|Методик\w+)"
    r"(?:\s+(?:(?:о|об|по)\s+)?(?P<about>" + _ABOUT_START_NEG
    + r"[А-Яа-яёЁ\w\s-]{2,80}?))?"
    + _ABOUT_END,
    re.UNICODE | re.IGNORECASE,
)

# ── 5. ст. N [прилагательное] <Положения|...> [о <NN>] ─────────────────────────
_INTERNAL_ARTICLE_RE = re.compile(
    r"\b(?:ст(?:атья|атьи|\.)\s*)(\d+(?:\.\d+)*)"
    r"\s+(?:(?P<adj>[А-ЯЁа-яё][А-Яа-яёЁ-]+)\s+)?"
    r"(?P<doc_type>Положени\w+|Регламент\w*|Стандарт\w*|Политик\w+|Устав\w*)"
    r"(?:\s+(?:(?:о|об|по)\s+)?(?P<about>" + _ABOUT_START_NEG
    + r"[А-Яа-яёЁ\w\s-]{2,80}?))?"
    + _ABOUT_END,
    re.UNICODE | re.IGNORECASE,
)

# Канонические сокращения кодексов — чтобы «КОАП» (после .upper()) выходил «КоАП».
_CODE_CANONICAL = {
    "КОАП": "КоАП",
    "ГК": "ГК",
    "НК": "НК",
    "ТК": "ТК",
    "УК": "УК",
    "ЖК": "ЖК",
    "ЗК": "ЗК",
    "БК": "БК",
    "АПК": "АПК",
    "ГПК": "ГПК",
    "УПК": "УПК",
}


def extract_legal_references(text: str, max_refs: int = 5) -> list[str]:
    """Вернуть список канонических ссылок для follow-up поиска (≤ max_refs).

    Канон: «ФЗ-38 статья 24», «КоАП РФ статья 14.3», «пункт 4.2 Положения
    о договорной работе». На вход — содержимое одного или нескольких чанков.
    Длина каждой ссылки ≤ ~80 символов, поэтому годится напрямую как запрос
    в дальнейший _raw_search (dense+BM25).
    """
    if not text:
        return []
    seen: set[str] = set()
    refs: list[str] = []

    # ст. N ФЗ/код
    for m in _FZ_ARTICLE_RE.finditer(text):
        art = m.group(1)
        fz = m.group("fz") or m.group("fz2")
        code = m.group("code")
        if fz:
            phrase = f"ФЗ-{fz} статья {art}"
        elif code:
            phrase = f"{_CODE_CANONICAL.get(code.upper(), code.upper())} РФ статья {art}"
        else:
            continue
        _add(phrase, seen, refs)

    # Постановление Правительства № NNN
    for m in _PP_RE.finditer(text):
        num = m.group(2)
        if num:
            _add(f"Постановление Правительства РФ № {num}", seen, refs)

    # п. N.N [прилаг] Положения о ...
    for m in _INTERNAL_REF_RE.finditer(text):
        p_num = m.group(1)
        adj = (m.group("adj") or "").strip()
        doc_type = (m.group("doc_type") or "").strip()
        about = (m.group("about") or "").strip().rstrip(",.;:")
        # Скип «п. 1 настоящего» и подобных — нет привязки к документу.
        if not doc_type:
            continue
        doc_part = f"{adj} {doc_type}".strip() if adj else doc_type
        if about:
            phrase = f"пункт {p_num} {doc_part} о {about}"
        else:
            phrase = f"пункт {p_num} {doc_part}"
        _add(phrase[:120], seen, refs)

    # ст. N [прилаг] локального акта
    for m in _INTERNAL_ARTICLE_RE.finditer(text):
        art = m.group(1)
        adj = (m.group("adj") or "").strip()
        doc_type = (m.group("doc_type") or "").strip()
        about = (m.group("about") or "").strip().rstrip(",.;:")
        if not doc_type:
            continue
        doc_type = f"{adj} {doc_type}".strip() if adj else doc_type
        if about:
            phrase = f"статья {art} {doc_type} о {about}"
        else:
            phrase = f"статья {art} {doc_type}"
        _add(phrase[:120], seen, refs)

    return refs[:max_refs]


def _add(phrase: str, seen: set[str], refs: list[str]) -> None:
    key = phrase.lower()
    if key not in seen:
        seen.add(key)
        refs.append(phrase)
