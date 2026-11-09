"""
Опциональный NER-слой для анонимайзера на базе Natasha (navec + slovnet).

Закрывает слабые места regex-движка anonymizer.py:
  - ФИО рядом со стоп-словами, где FIO-паттерн намеренно молчит
    («Главный бухгалтер Иванова Мария» → regex пропускает, NER ловит PER);
  - организации без ОПФ/кавычек («Поставку осуществляет Ромашка»,
    «ПАО Сбербанк») → regex ловит только ООО «...» в кавычках.

Что НЕ маскируем:
  - LOC (города, «Российской Федерации») — высокий риск порчи правового текста
    (часть ссылки «ГК РФ», города судов); адреса ловит regex по якорю.
  - ORG госорганов/судов/регуляторов/кодексов — их маскировка вредит пониманию
    (см. _PUBLIC_ORG_*). Эти сущности публичные, а не ПДн контрагента.

Модель Natasha идёт весами в составе пакета (~30 МБ) — работает офлайн, без
скачивания. Грузится лениво, синглтоном, ~0.9 с / ~200 МБ RAM. Включается флагом
ANONYMIZER_NER_ENABLED (по умолчанию выключен).
"""
from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("legal-assistant")

# ── Стоп-лист публичных/правовых организаций (НЕ маскируем) ────────────────────
# Слова-маркеры госоргана/правового термина (сравнение по целым словам, lowercase).
_PUBLIC_ORG_WORDS: frozenset[str] = frozenset({
    "суд", "министерство", "ведомство", "прокуратура", "правительство",
    "администрация", "дума", "парламент", "кодекс", "конституция", "госуслуги",
    "росздравнадзор", "роспотребнадзор", "роскомнадзор", "рособрнадзор",
    "роспатент", "ростехнадзор", "росреестр", "роструд", "ростат", "росстат",
    "фас", "фнс", "фсс", "пфр", "фссп", "мвд", "фсб", "мчс", "цик",
})

# Многословные маркеры (подстрока в lowercase).
_PUBLIC_ORG_PHRASES: tuple[str, ...] = (
    "банк россии", "центральный банк", "совет федерации",
    "федеральная служба", "федеральное агентство", "верховный суд",
    "конституционный суд", "арбитражный суд", "пенсионный фонд",
    "счётная палата", "генеральная прокуратура",
)

_WORD_RE = re.compile(r"[а-яёa-z]+")


def _is_public_org(text: str) -> bool:
    """True, если организация — госорган/суд/регулятор/правовой термин."""
    low = text.lower()
    for ph in _PUBLIC_ORG_PHRASES:
        if ph in low:
            return True
    tokens = set(_WORD_RE.findall(low))
    return bool(tokens & _PUBLIC_ORG_WORDS)


# ── Ленивый синглтон Natasha ──────────────────────────────────────────────────
_lock = threading.Lock()
_tagger: "_NatashaTagger | None" = None
_loaded = False


class _NatashaTagger:
    def __init__(self) -> None:
        from natasha import Segmenter, NewsEmbedding, NewsNERTagger
        self._segmenter = Segmenter()
        self._ner = NewsNERTagger(NewsEmbedding())

    def extract(self, text: str) -> list[tuple[str, str]]:
        """Возвращает [(текст_сущности, метка)] для PER→ФИО, ORG→КОМПАНИЯ.

        LOC и публичные организации отбрасываются. Дубликаты и сущности
        короче 3 символов отсекаются вызывающей стороной.
        """
        from natasha import Doc
        doc = Doc(text)
        doc.segment(self._segmenter)
        doc.tag_ner(self._ner)
        out: list[tuple[str, str]] = []
        for span in doc.spans:
            value = (span.text or "").strip()
            if len(value) < 3:
                continue
            if span.type == "PER":
                out.append((value, "ФИО"))
            elif span.type == "ORG":
                if _is_public_org(value):
                    continue
                out.append((value, "КОМПАНИЯ"))
            # LOC и прочее — пропускаем
        return out


def get_tagger() -> "_NatashaTagger | None":
    """Лениво создаёт и кеширует NER-теггер. None, если Natasha недоступна."""
    global _tagger, _loaded
    if _loaded:
        return _tagger
    with _lock:
        if _loaded:
            return _tagger
        try:
            _tagger = _NatashaTagger()
            logger.info("[NER] Natasha NER-теггер загружен")
        except Exception as exc:
            logger.warning("[NER] не удалось загрузить Natasha: %s — NER отключён", exc)
            _tagger = None
        _loaded = True
    return _tagger
