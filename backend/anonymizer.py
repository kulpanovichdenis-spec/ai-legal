"""
Обезличивание текста перед отправкой в LLM и восстановление в ответе.

Архитектура:
    text_for_llm, anonymizer = Anonymizer.anonymize(raw_text)
    llm_response = await query_llm(..., text_for_llm)
    safe_response = anonymizer.deanonymize(llm_response)

Плейсхолдеры содержат только тип и количество цифр/слов — данные не раскрываются.

Pluggable: вызывается через PrivacyManager.anonymize_file_context() в зависимости от политики сценария.
"""

from __future__ import annotations

import re
from typing import Callable, Pattern


# Whitelist отсутствует — все ПДн и реквизиты маскируются без исключений.
_WHITELIST: set[str] = set()
_WHITELIST_SORTED: list[str] = []


# ── ОПФ (организационно-правовые формы) ─────────────────────────────────
_OPF = (
    r"ООО|ОАО|ЗАО|ПАО|АО|НКО|НАО|НПО|ИП|ФГУП|ГУП|МУП|"
    r"Общество\s+с\s+ограниченной\s+ответственностью|"
    r"Публичное\s+акционерное\s+общество|"
    r"Акционерное\s+общество|"
    r"Закрытое\s+акционерное\s+общество|"
    r"Открытое\s+акционерное\s+общество|"
    r"Индивидуальный\s+предприниматель"
)

# ── ОПФ иностранные (суффикс после названия) ────────────────────────────
_OPF_FOREIGN = r"GmbH|LLC|Inc\.?|Ltd\.?|Corp\.?|AG|S\.A\.|B\.V\.|N\.V\.|PLC|SE|S\.r\.l\."

# ── Строительные блоки для ФИО-паттернов (Итерация 3) ───────────────────
# _WL_RU — «длинное» кириллическое слово (3+ символа): Иванов, О'Брайен, Ким
# _WM_RU — «среднее» (2+ символа): покрывает короткие восточные компоненты (Ли, Ын)
# Оба допускают дефис (Салтыков-Щедрин) и апостроф (О'Брайен) внутри слова.
_WL_RU = (r"[А-ЯЁ][а-яё]{2,}"
           r"(?:'[А-ЯЁ][а-яё]+)*"
           r"(?:[-—][А-ЯЁ][а-яё]+)*")
_WM_RU = (r"[А-ЯЁ][а-яё]{1,}"
           r"(?:'[А-ЯЁ][а-яё]+)*"
           r"(?:[-—][А-ЯЁ][а-яё]+)*")
_SUF_RU = r"(?:\s+(?:оглы|кызы|ибн|бин|бен))?"

# ── Стоп-лист для FIO-паттернов (фиксы B1, B2 из AUDIT_anonymizer.md) ──
# Если ХОТЬ одно слово в матче FIO принадлежит этому списку — замена отменяется.
# Это устраняет массовые ложные срабатывания на юридическом контексте:
# «Гражданский Кодекс Российской», «Министерство Внутренних Дел»,
# «Согласно Договору И.О.», «Председатель Г.К.» и т.п.
# Цена: если рядом со стоп-словом стоит настоящее ФИО («Главный Бухгалтер
# Иванова»), захват пропускается — приоритет на сохранение юр. контекста.
# Хранится в нижнем регистре; сравнение тоже в нижнем регистре.
_FIO_STOPWORDS: frozenset[str] = frozenset({
    # Стороны и роли в договорах (B1)
    "договор", "договору", "договором", "договоре", "договора", "договоры",
    "сторона", "стороны", "сторону", "сторон", "стороной",
    "поставщик", "поставщика", "поставщику", "поставщиком",
    "покупатель", "покупателя", "покупателю", "покупателем",
    "заказчик", "заказчика", "заказчику", "заказчиком",
    "подрядчик", "подрядчика", "подрядчику",
    "исполнитель", "исполнителя", "исполнителю",
    "арендатор", "арендатора", "арендодатель", "арендодателя",
    "продавец", "продавца", "получатель", "получателя",
    "плательщик", "плательщика", "клиент", "клиента",
    "уважаемый", "уважаемая", "уважаемые",
    "председатель", "председателя", "директор", "директора",
    "генеральный", "главный", "бухгалтер", "сотрудник", "работник",
    "свидетель", "свидетеля", "истец", "истца",
    "ответчик", "ответчика", "представитель", "представителя",
    "эксперт", "эксперта", "специалист", "специалиста",
    "нотариус", "нотариуса", "судья", "судьи",
    "подрядчик", "подрядчика", "подрядчику",
    "согласно", "настоящий", "настоящая", "настоящего", "настоящему",
    "раздел", "пункт", "глава", "статья", "приложение", "часть",
    "город", "москва", "санкт", "спб",
    # Гос. органы, кодексы и НПА (B2)
    "министерство", "министерства", "общество", "обществом",
    "акционерное", "открытое", "закрытое", "публичное",
    "кодекс", "кодексу", "кодексом", "кодексе", "кодекса",
    "федерация", "федерации", "федеральная", "федеральный", "федеральное",
    "российская", "российской", "российский", "российское", "российскому",
    "поставки", "услуг", "услуги", "аренды", "подряда",
    "внутренних", "дел", "юстиции", "обороны", "финансов",
    "суд", "суда", "арбитражный", "арбитражного", "арбитражному",
    "антимонопольная", "антимонопольной",
    "служба", "службы", "правительство", "правительства",
    "высший", "высшего", "конституционный", "верховный", "верховного",
    "центральный", "банк", "россии",
    "налоговый", "налогового", "налоговая", "налоговой",
    "гражданский", "гражданского", "гражданская",
    "уголовный", "уголовного", "уголовное",
    "трудовой", "трудового", "земельный", "земельного",
    "семейный", "семейного",
    "решение", "решением", "постановление", "постановлением",
    "приказ", "приказом", "распоряжение", "указ", "закон", "законом",
    # Должности и звания (Итерация 3: Старший Юрист Петров → не ФИО)
    "лидер", "лидера", "лидеру",
    "президент", "президента", "президенту",
    "министр", "министра", "министру",
    "депутат", "депутата", "депутату",
    "советник", "советника", "советнику",
    "секретарь", "секретаря",
    "посол", "посла",
    "губернатор", "губернатора",
    "мэр", "мэра",
    "сенатор", "сенатора",
    "гражданин", "гражданина",
    "старший", "старшая", "старшего", "старшей",
    "младший", "младшая", "младшего",
    "ведущий", "ведущая", "ведущего",
    "юрист", "юриста", "юристу", "юристом",
    "менеджер", "менеджера", "менеджеру",
    "аналитик", "аналитика",
    "инспектор", "инспектора",
    "начальник", "начальника", "начальнику",
    "руководитель", "руководителя", "руководителю",
    "заместитель", "заместителя", "заместителю",
    "помощник", "помощника", "помощнику",
    # Типовые заголовки таблиц и финансовые термины
    "активности", "активность", "активностей",
    "стоимость", "стоимости", "стоимостью",
    "бюджет", "бюджета", "бюджету", "бюджетом",
    "наименование", "наименования", "наименованию",
    "количество", "количества", "количеству",
    "единица", "единицы", "единице",
    "итого", "всего", "сумма", "суммы", "сумме",
    "примечание", "примечания",
    # Прилагательные, образующие устойчивые сочетания (Итерация 3: Северный Поток)
    "торговый", "торговая", "торгового",
    "правовой", "правовая", "правового",
    "первый", "первая", "первого", "первом",
    "второй", "вторая", "второго", "втором",
    "третий", "третья", "третьего",
    "северный", "северная", "северного", "северном",
    "южный", "южная", "южного",
    "западный", "западная", "западного",
    "восточный", "восточная", "восточного",
    "рабочий", "рабочая", "рабочего",
    "деловой", "деловая",
    "финансовый", "финансовая", "финансового",
    "коммерческий", "коммерческая", "коммерческого",
    "технический", "техническая", "технического",
    "исполнительный", "исполнительная", "исполнительного",
    "государственный", "государственная", "государственного",
    "муниципальный", "муниципальная", "муниципального",
    "международный", "международная", "международного",
})


def _fio_accept(m: re.Match) -> bool:
    """Возвращает False, если матч содержит стоп-слово — отменяет маскировку."""
    text = m.group(0)
    tokens = re.findall(r"[А-ЯЁа-яё]+", text)
    return not any(tok.lower() in _FIO_STOPWORDS for tok in tokens)


# ── Стоп-лист и фильтр для латинских ФИО (Итерация 3) ───────────────────
_FIO_LATIN_STOPWORDS: frozenset[str] = frozenset({
    # Служебные слова
    "the", "of", "and", "in", "for", "from", "with", "by", "to", "or", "at", "on", "as",
    # ОПФ на латинице
    "llc", "inc", "ltd", "gmbh", "ag", "sa", "bv", "nv", "plc", "corp", "co", "lp",
    # Обращения
    "mr", "mrs", "ms", "dr", "prof",
    # Месяцы (чтобы не захватить «January Smith»)
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})


def _fio_latin_accept(m: re.Match) -> bool:
    """Фильтр для латинских ФИО: отклоняет юр. термины и нарицательные слова."""
    tokens = re.findall(r"[A-Za-z]+", m.group(0))
    return not any(t.lower() in _FIO_LATIN_STOPWORDS for t in tokens)


# Плейсхолдер реквизита: [ИНН_ЮЛ_1_10цифр]. Цифры внутри него повторно не маскируем.
_PLACEHOLDER_RE = re.compile(
    r"\[[A-ZА-ЯЁ][A-ZА-ЯЁ0-9_]*_\d+(?:_\d+цифр)?\]"
)
_BIK_RE = re.compile(r"БИК[\s:№]*(\d{9})(?!\d)", re.IGNORECASE)
_INN10_LABEL_BEFORE = re.compile(r"ИНН[\s:№]*$", re.IGNORECASE)
_INN12_LABEL_BEFORE = re.compile(r"(?:ИНН\s+ФЛ|ИНН)[\s:№]*$", re.IGNORECASE)
_OGRN_LABEL_BEFORE = re.compile(r"ОГРН[\s:№]*$", re.IGNORECASE)
_OGRNIP_LABEL_BEFORE = re.compile(r"ОГРНИП[\s:№]*$", re.IGNORECASE)
_ACC_LABEL_BEFORE = re.compile(
    r"(?:р[/.\s]?с|к[/.\s]?с|сч[её]т[аов]?|"
    r"расч[её]тный\s+сч[её]т|корреспондентский\s+сч[её]т)[\s:№]*$",
    re.IGNORECASE,
)


def _inn10_valid(value: str) -> bool:
    # ИНН ЮЛ, 10 цифр: веса 2,4,10,3,5,9,4,6,8; остаток 10 даёт ключ 0.
    if len(value) != 10 or not value.isdigit():
        return False
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    total = sum(int(value[i]) * weights[i] for i in range(9))
    return total % 11 % 10 == int(value[9])


def _inn12_valid(value: str) -> bool:
    # ИНН ФЛ, 12 цифр: два ключа, остаток 10 в каждом даёт цифру 0.
    if len(value) != 12 or not value.isdigit():
        return False
    digits = [int(ch) for ch in value]
    weights11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    weights12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    n11 = sum(digits[i] * weights11[i] for i in range(10)) % 11 % 10
    if n11 != digits[10]:
        return False
    n12 = sum(digits[i] * weights12[i] for i in range(11)) % 11 % 10
    return n12 == digits[11]


def _ogrn13_valid(value: str) -> bool:
    # ОГРН: первые 12 цифр mod 11 mod 10 равны 13-й.
    if len(value) != 13 or not value.isdigit():
        return False
    return int(value[:12]) % 11 % 10 == int(value[12])


def _ogrnip15_valid(value: str) -> bool:
    # ОГРНИП: первые 14 цифр mod 13 mod 10 равны 15-й.
    if len(value) != 15 or not value.isdigit():
        return False
    return int(value[:14]) % 13 % 10 == int(value[14])


def _account_bik_valid(bik: str, account: str) -> bool:
    # Ключ расчётного счёта: 23 цифры, веса 7,1,3, младший разряд суммы равен 0.
    # РКЦ (разряды 7–9 БИК = 000) — префикс «0» и 5–6-й разряды, иначе разряды 7–9.
    if len(bik) != 9 or len(account) != 20 or not bik.isdigit() or not account.isdigit():
        return False
    if bik[6:9] == "000":
        prefix = "0" + bik[4:6]
    else:
        prefix = bik[6:9]
    seq = prefix + account
    weights = (7, 1, 3)
    total = sum(int(seq[i]) * weights[i % 3] for i in range(23))
    return total % 10 == 0


def _span_inside_placeholder(text: str, start: int, end: int) -> bool:
    open_at = text.rfind("[", 0, start)
    if open_at < 0:
        return False
    if open_at < text.rfind("]", 0, start):
        return False
    close_at = text.find("]", end)
    if close_at < 0:
        return False
    return _PLACEHOLDER_RE.fullmatch(text[open_at:close_at + 1]) is not None


def _label_before(text: str, pos: int, label_re: Pattern[str]) -> bool:
    return label_re.search(text[:pos]) is not None


def _biks_in_text(text: str) -> list[str]:
    found: list[str] = []
    for match in _BIK_RE.finditer(text):
        if _span_inside_placeholder(text, match.start(1), match.end(1)):
            continue
        found.append(match.group(1))
    return found


def _bare_digits_ok(
    match: re.Match,
    label_re: Pattern[str],
    valid: Callable[[str], bool],
) -> bool:
    if _span_inside_placeholder(match.string, match.start(), match.end()):
        return False
    if _label_before(match.string, match.start(), label_re):
        return False
    return valid(match.group(0))


def _bare_inn10_accept(match: re.Match) -> bool:
    return _bare_digits_ok(match, _INN10_LABEL_BEFORE, _inn10_valid)


def _bare_inn12_accept(match: re.Match) -> bool:
    return _bare_digits_ok(match, _INN12_LABEL_BEFORE, _inn12_valid)


def _bare_ogrn_accept(match: re.Match) -> bool:
    return _bare_digits_ok(match, _OGRN_LABEL_BEFORE, _ogrn13_valid)


def _bare_ogrnip_accept(match: re.Match) -> bool:
    return _bare_digits_ok(match, _OGRNIP_LABEL_BEFORE, _ogrnip15_valid)


def _bare_account_accept(match: re.Match) -> bool:
    if _span_inside_placeholder(match.string, match.start(), match.end()):
        return False
    if _label_before(match.string, match.start(), _ACC_LABEL_BEFORE):
        return False
    # Две латинские буквы перед цифрами — хвост IBAN (DE89…), не номер счёта.
    prev = match.string[max(0, match.start() - 2):match.start()]
    if len(prev) == 2 and prev.isascii() and prev.isalpha():
        return False
    account = match.group(0)
    return any(_account_bik_valid(bik, account) for bik in _biks_in_text(match.string))


# Паттерны применяются В ПОРЯДКЕ. Первый матч «съедает» подстроку.
# Паттерн-запись: (имя, regex, метка_плейсхолдера, номер_группы_для_замены, accept).
# group=0 — заменяется весь матч; иначе — только эта группа (остальное сохраняется,
# чтобы LLM видела структуру «Наименование товара: [ТОВАР_1]»).
# accept(match) — необязательный фильтр; если возвращает False, замена пропускается.
_AcceptFn = Callable[[re.Match], bool]
_PATTERNS: list[tuple[str, Pattern[str], str, int, _AcceptFn | None]] = [
    # E-mail
    ("EMAIL",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
     "EMAIL", 0, None),
    # Компания в кавычках с ОПФ: ООО «Ромашка», АО "Вектор-М".
    # Закрывающая кавычка зависит от открывающей — это устраняет ложные
    # обрезания при вложенных кавычках: ООО «Страховая Компания "Север"».
    ("COMPANY",
     re.compile(
         rf"(?:{_OPF})\s+"
         r"(?:«[^»]{2,120}»"       # «guillemets» — вложенные " допустимы
         r"|\"[^\"]{2,120}\""       # "double"
         r"|'[^']{2,120}')",        # 'single'
         re.IGNORECASE,
     ),
     "КОМПАНИЯ", 0, None),
    # Иностранная компания: название + OPF-суффикс (GmbH, LLC, Ltd …).
    # Захватывает 1–4 слова перед суффиксом. Идёт после COMPANY, чтобы
    # русские ОПФ уже были съедены.
    ("COMPANY_FOREIGN",
     re.compile(
         rf"[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё\-]{{1,30}}"
         rf"(?:\s+[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё\-]{{0,30}}){{0,3}}"
         rf"\s+(?:{_OPF_FOREIGN})\b",
     ),
     "КОМПАНИЯ", 0, None),
    # ИП ФИО
    ("IP_FIO",
     re.compile(
         r"\b(?:ИП|Индивидуальный\s+предприниматель)\s+"
         r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\."
         r"|\b(?:ИП|Индивидуальный\s+предприниматель)\s+"
         r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+",
         re.IGNORECASE,
     ),
     "ИП_ФИО", 0, None),
    # ── ФИО (Итерация 1 + 2) ─────────────────────────────────────────────
    # accept=_fio_accept — фильтр стоп-слов (B1, B2): пропускает матчи с
    # юридическим контекстом («Гражданский Кодекс Российской», «Договору И.О.»).
    # Поддержка (Итерация 2):
    #   A7 — двойные/тройные фамилии через дефис (Салтыков-Щедрин, Семёнов-Тян-Шанский)
    #   A8 — дефисное имя (Жан-Пьер)
    #   A9 — национальные постфиксы (оглы, кызы, ибн, бин, бен)
    #   A4 — инициалы перед фамилией (И.И. Иванов, А.С. Пушкин)
    #   A5 — опциональная последняя точка («Иванов И.И» без точки)
    # CAPS-вариант («ИВАНОВ И.И.») вынесен в отдельный паттерн FIO_CAPS,
    # чтобы не включать re.IGNORECASE для всей FIO-ветки (иначе строчные
    # тройки слов начнут давать ложные срабатывания).
    ("FIO",
     re.compile(
         # 1. Три «длинных» слова (3+ символа) + нац. постфикс.
         #    Апостроф (О'Брайен) и em-dash (Иванов—Петров) поддерживаются через _WL_RU.
         rf"\b(?:{_WL_RU})\s+(?:{_WL_RU})\s+(?:{_WL_RU}){_SUF_RU}\b"
         # 2. «Длинная» фамилия + два инициала (последняя точка опциональна). [A5]
         rf"|\b(?:{_WL_RU})\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?(?!\w)"
         # 3. Два инициала + «длинная» фамилия. [A4]
         rf"|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s+(?:{_WL_RU})\b"
         # 4. «Длинная» фамилия + один инициал (Дёмин С., Петров А.). [новое]
         rf"|\b(?:{_WL_RU})\s+[А-ЯЁ]\.(?!\w)"
         # 5. Три «средних» слова (2+ символа) + нац. постфикс. [A6]
         #    Покрывает короткие восточные имена: Ким Чен Ын, Ли Чен Цзинь.
         rf"|\b(?:{_WM_RU})\s+(?:{_WM_RU})\s+(?:{_WM_RU}){_SUF_RU}\b"
         # 6. Два «длинных» слова — ФИО без отчества. [A3]
         #    Иван Иванов, Сидоров Пётр. Стоп-лист блокирует юр. контекст.
         rf"|\b(?:{_WL_RU})\s+(?:{_WL_RU})\b"
     ),
     "ФИО", 0, _fio_accept),
    # CAPS-формы: «ИВАНОВ И.И.» и «ИВАНОВ ИВАН ИВАНОВИЧ» в шапках, штампах, подписных блоках.
    # Без IGNORECASE на основном FIO, чтобы не ловить строчные тройки.
    ("FIO_CAPS",
     re.compile(
         # 1. ФАМИЛИЯ И.О.
         r"\b[А-ЯЁ]{3,}(?:[-—][А-ЯЁ]+)*\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?(?!\w)"
         # 2. ТРИ СЛОВА КАПСЛОКОМ — полное ФИО в реестрах, ЭДО, сертификатах
         r"|\b[А-ЯЁ]{3,}(?:[-—][А-ЯЁ]+)*\s+[А-ЯЁ]{3,}(?:[-—][А-ЯЁ]+)*\s+[А-ЯЁ]{3,}(?:[-—][А-ЯЁ]+)*\b"
     ),
     "ФИО", 0, _fio_accept),
    # ── ФИО на латинице (Итерация 3) ─────────────────────────────────────
    # Покрывает: John Smith, Jean-Pierre Dupont, Ivan Petrov,
    #            Jean-Pierre Martin Leblanc (3 компонента).
    # Фильтр _fio_latin_accept отклоняет: LLC/GmbH/Inc, служебные слова, месяцы.
    ("FIO_LATIN",
     re.compile(
         r"\b[A-Z][a-z]{1,}(?:-[A-Z][a-z]+)?"
         r"\s+[A-Z][a-z]{1,}(?:-[A-Z][a-z]+)?"
         r"(?:\s+[A-Z][a-z]{1,}(?:-[A-Z][a-z]+)?)?\b"
     ),
     "ФИО", 0, _fio_latin_accept),
    # ── Коммерческие данные ───────────────────────────────────────────
    # Тема услуги: «услуги по X» — маскирует X до ближайшей запятой/точки/переноса.
    # Минимум 10 символов отсекает «услуги по договору», «услуги по акту» и т.п.
    ("SERVICE_TOPIC",
     re.compile(
         r"(?:услуг[аи]?\s+по|оказани[еяю]\s+услуг\s+по"
         r"|предоставлени[еяю]\s+(?:услуг|сервис[аов])\s+по)"
         r"\s+"
         r"([^,\n.;]{10,300})",
         re.IGNORECASE,
     ),
     "ТЕМА_УСЛУГИ", 1, None),
    # Описание товара/услуги: якорь сохраняется, содержимое маскируется.
    # Многострочный захват (до пустой строки): (?:[^\n]|\n(?!\n))+
    # «а именно:» — якорь для таблиц в договорах об услугах.
    ("PRODUCT_DESC",
     re.compile(
         r"(Наименование\s+(?:товара|услуг[аи]?|продукции|работ)"
         r"|Предмет\s+договора"
         r"|Описание\s+(?:товара|услуг[аи]?|продукции|работ)"
         r"|Ассортимент"
         r"|Спецификация"
         r"|Товар(?:\s+и\s+его\s+характеристики)?"
         r"|Услуг[аи]"
         r"|Продукция"
         r"|Артикул"
         r"|Товарный\s+знак"
         r"|а\s+именно"
         r"|Предмет\s+обращения)"
         r"\s*[:–-]\s*"
         r"((?:[^\n]|\n(?!\n)){3,2000})",
         re.IGNORECASE,
     ),
     "ОПИСАНИЕ", 2, None),
    # Срок оплаты — раньше DATE/PRICE, чтобы забрать более длинный матч.
    ("PAYMENT_TERM",
     re.compile(
         r"(?:оплата|оплатить|оплачивает(?:ся)?|платёж|платеж|перечисляет(?:ся)?)"
         r"[^\n.]{0,60}?"
         r"(?:в\s+течение\s+\d{1,4}\s+(?:календарных\s+|рабочих\s+|банковских\s+)?(?:дней|дня|дн\.|суток)"
         r"|не\s+позднее\s+\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}"
         r"|не\s+позднее\s+«?\d{1,2}»?\s+[а-яё]+\s+\d{4}"
         r"|до\s+«?\d{1,2}»?\s+[а-яё]+\s+\d{4}"
         r"|до\s+\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})",
         re.IGNORECASE,
     ),
     "СРОК_ОПЛАТЫ", 0, None),
    # Название клинического исследования: якорь + текст в кавычках «…».
    # [^«]{0,60} — допускает перенос строки между якорем и открывающей кавычкой.
    ("PROTOCOL_NAME",
     re.compile(
         r"(?:протокол(?:а|у|ом|е)?\s+исследовани[яею]"
         r"|клинического\s+исследовани[яею]"
         r"|название\s+исследовани[яею])"
         r"[^«]{0,60}"
         r"«(?:[^»]|\n){5,1000}»",
         re.IGNORECASE,
     ),
     "ПРОТОКОЛ", 0, None),
    # Цена / сумма с валютой. Опциональный скобочный текст между числом и валютой.
    ("PRICE",
     re.compile(
         r"\b\d{1,3}(?:[\s ]\d{3})*(?:[.,]\d{1,2})?"
         r"(?:\s*\([^)]{0,100}\))?\s?"
         r"(?:руб(?:\.|лей|ля)?|₽|RUB|USD|$|EUR|€|долл\.?(?:\s*США)?|евро|копеек|коп\.?)",
         re.IGNORECASE,
     ),
     "СУММА", 0, None),
    # Сумма с множителем (тыс./млн./млрд.) отдельно — сначала число, потом масштаб, потом валюта
    ("PRICE_SCALED",
     re.compile(
         r"\b\d+(?:[.,]\d+)?\s?(?:тыс|млн|млрд)\.?\s?(?:руб(?:\.|лей)?|₽|USD|EUR|€|долл\.?|евро)",
         re.IGNORECASE,
     ),
     "СУММА", 0, None),
    # НДС
    ("VAT",
     re.compile(
         r"НДС[^.\n]{0,30}?\d{1,2}(?:[.,]\d{1,2})?\s?%"
         r"|без\s+НДС"
         r"|НДС\s+не\s+облагается",
         re.IGNORECASE,
     ),
     "НДС", 0, None),
    # Процент (наценка, скидка, штраф)
    ("PERCENT",
     re.compile(
         r"(?:скидк[аи]|наценк[аи]|неустойк[аи]|штраф[а-я]*|пен[яи])"
         r"[^\n.]{0,40}?\d{1,3}(?:[.,]\d+)?\s?%",
         re.IGNORECASE,
     ),
     "ПРОЦЕНТ", 0, None),
    # Количество с единицей измерения
    ("QUANTITY",
     re.compile(
         r"\b\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?\s?"
         r"(?:шт\.?|штук[аи]?|кг|кг\.|гр\.?|тонн[аы]?|т\.|"
         r"упак(?:овок|овки)?\.?|уп\.|коробок|паллет[аы]?|"
         r"литр[аов]?|л\.|мл|м³|м3|м²|м2|метров?|"
         r"ед\.|единиц[аы]?|компл\.|комплектов?|доз[аы]?|ампул[аы]?|флаконов?|таблеток)\b",
         re.IGNORECASE,
     ),
     "КОЛИЧЕСТВО", 0, None),
    # Даты DD.MM.YYYY / DD-MM-YYYY / DD/MM/YYYY
    ("DATE_NUM",
     re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/](?:\d{4}|\d{2})\b"),
     "ДАТА", 0, None),
    # Даты «15 января 2025 г.»
    ("DATE_TEXT",
     re.compile(
         r"«?\d{1,2}»?\s+(?:январ[ея]|феврал[ея]|март[а]?|апрел[ея]|ма[яй]|"
         r"июн[ея]|июл[ея]|август[а]?|сентябр[ея]|октябр[ея]|ноябр[ея]|декабр[ея])"
         r"\s+\d{4}\s*(?:г\.|года)?",
         re.IGNORECASE,
     ),
     "ДАТА", 0, None),
    # ── Числовые реквизиты с обязательным контекстным якорем (фикс A10) ──
    # До фикса любое 10-значное число маскировалось как «паспорт», 13-значное —
    # как «ОГРН» (под раздачу попадали номера договоров, EAN, IMEI, SKU).
    # С подписью («ИНН», «ОГРН», «р/с») маскируем как раньше, без проверки суммы.
    # Без подписи — только ИНН 10/12, ОГРН 13, ОГРНИП 15 и расчётный счёт,
    # и только если сходится контрольная сумма (для счёта — с БИК из того же текста).
    # Код валюты внутри 20 цифр номер не маскирует. Настроенный whitelist не задет —
    # он обрабатывается раньше через _protect_whitelist.
    # ОГРНИП (15 цифр) идёт раньше ОГРН (13 цифр), чтобы не пересекаться;
    # ИНН ФЛ (12 цифр) раньше ИНН ЮЛ (10 цифр) по той же причине.
    ("PASSPORT",
     re.compile(
         r"(?:паспорт(?:а|у|ом|е)?|серия\s+и\s+номер)[\s:№]*"
         r"(\d{2}\s?\d{2}\s?\d{6})(?!\d)",
         re.IGNORECASE,
     ),
     "ПАСПОРТ", 1, None),
    # СНИЛС: XXX-XXX-XXX XX (11 цифр, разделители опциональны)
    ("SNILS",
     re.compile(
         r"(?:СНИЛС|страховой\s+номер(?:\s+ИЛС)?)"
         r"[\s:№]*(\d{3}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{2})(?!\d)",
         re.IGNORECASE,
     ),
     "СНИЛС", 1, None),
    # Водительское удостоверение: «77 АА 123456» (серия = регион + 2 буквы)
    ("DL",
     re.compile(
         r"(?:водительск(?:ое|ого|ому)\s+удостоверени[ея]|вод\.?\s*уд\.?|в\.у\.?)"
         r"[\s:№]*(\d{2}\s?[А-ЯЁA-Z]{2}\s?\d{6})",
         re.IGNORECASE,
     ),
     "ВОД_УД", 1, None),
    # Госномер автомобиля: А123ВГ 77 / А123ВГ177.
    # Допустимые буквы АВЕКМНОРСТУХ (кириллица, используемая на RU-табличках).
    ("CAR_PLATE",
     re.compile(
         r"\b[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}[\s]?\d{2,3}\b"
     ),
     "ГОЗНАК", 0, None),
    # Кадастровый номер: 77:01:0001001:123 — формат достаточно уникален без якоря
    ("CADASTRE",
     re.compile(r"\b\d{2}:\d{2}:\d{6,7}:\d{1,6}\b"),
     "КАДАСТР", 0, None),
    ("OGRN15",
     re.compile(r"(?:ОГРНИП)[\s:№]*(\d{15})(?!\d)", re.IGNORECASE),
     "ОГРНИП", 1, None),
    ("OGRN13",
     re.compile(r"(?:ОГРН)[\s:№]*(\d{13})(?!\d)", re.IGNORECASE),
     "ОГРН", 1, None),
    ("INN12",
     re.compile(r"(?:ИНН\s+ФЛ|ИНН)[\s:№]*(\d{12})(?!\d)", re.IGNORECASE),
     "ИНН_ФЛ", 1, None),
    ("INN10",
     re.compile(r"(?:ИНН)[\s:№]*(\d{10})(?!\d)", re.IGNORECASE),
     "ИНН_ЮЛ", 1, None),
    ("KPP",
     re.compile(r"(?:КПП)[\s:№]*(\d{9})(?!\d)", re.IGNORECASE),
     "КПП", 1, None),
    ("OKPO",
     re.compile(r"(?:ОКПО)[\s:№]*(\d{8,10})(?!\d)", re.IGNORECASE),
     "ОКПО", 1, None),
    ("OKTMO",
     re.compile(r"(?:ОКТМО)[\s:№]*(\d{8,11})(?!\d)", re.IGNORECASE),
     "ОКТМО", 1, None),
    ("OKATO",
     re.compile(r"(?:ОКАТО)[\s:№]*(\d{8,11})(?!\d)", re.IGNORECASE),
     "ОКАТО", 1, None),
    ("OKVED",
     re.compile(r"(?:ОКВЭД\d*|вид\s+деятельности)[\s:№]*(\d{2}(?:\.\d{1,2}){0,2})", re.IGNORECASE),
     "ОКВЭД", 1, None),
    ("OKUGU",
     re.compile(r"(?:ОКОГУ)[\s:№]*(\d{7})(?!\d)", re.IGNORECASE),
     "ОКОГУ", 1, None),
    ("ACC20",
     re.compile(
         r"(?:р[/.\s]?с|к[/.\s]?с|сч[её]т[аов]?|"
         r"расч[её]тный\s+сч[её]т|корреспондентский\s+сч[её]т)[\s:№]*"
         r"(\d{20})(?!\d)",
         re.IGNORECASE,
     ),
     "СЧЕТ", 1, None),
    # Без подписи. До маскировки БИК: иначе цифр БИК уже нет и ключ счёта не сверить.
    # Длинные номера раньше коротких, чтобы 15 цифр не разменять на 10.
    ("OGRNIP15_BARE",
     re.compile(r"(?<!\d)\d{15}(?!\d)"),
     "ОГРНИП", 0, _bare_ogrnip_accept),
    ("OGRN13_BARE",
     re.compile(r"(?<!\d)\d{13}(?!\d)"),
     "ОГРН", 0, _bare_ogrn_accept),
    ("INN12_BARE",
     re.compile(r"(?<!\d)\d{12}(?!\d)"),
     "ИНН_ФЛ", 0, _bare_inn12_accept),
    ("INN10_BARE",
     re.compile(r"(?<!\d)\d{10}(?!\d)"),
     "ИНН_ЮЛ", 0, _bare_inn10_accept),
    ("ACC20_BARE",
     re.compile(r"(?<!\d)\d{20}(?!\d)"),
     "СЧЕТ", 0, _bare_account_accept),
    # БИК
    ("BIK",
     re.compile(r"\bБИК[\s:]*0?\d{8,9}\b", re.IGNORECASE),
     "БИК", 0, None),
    # ── IBAN / SWIFT / банковская карта (Итерация 5) ─────────────────────
    # IBAN — якорный паттерн целиком, включая буквенный префикс страны.
    # Пробельный формат (DE89 3704 …) ловится вторым альтернативом.
    ("IBAN",
     re.compile(
         r"IBAN[\s:]*([A-Z]{2}\d{2}[A-Z0-9]{10,30})"   # компактный: DE89370400440532013000
         r"|IBAN[\s:]*([A-Z]{2}\d{2}(?:[\s\-]\w{4}){3,9})",  # пробельный: DE89 3704 0044 …
         re.IGNORECASE,
     ),
     "IBAN", 0, None),
    # SWIFT/BIC: 8 или 11 символов (4 буквы банка + 2 страны + 2 лок. + 3 отд.)
    ("SWIFT",
     re.compile(
         r"(?:SWIFT|BIC)[\s:]*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b",
         re.IGNORECASE,
     ),
     "SWIFT", 1, None),
    # Банковская карта — с якорем (любой формат 16 цифр)
    ("CARD",
     re.compile(
         r"(?:(?:номер\s+)?карт[ыаеу]|card(?:\s+(?:number|no))?|pan)"
         r"[\s:№]*(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})(?!\d)",
         re.IGNORECASE,
     ),
     "КАРТА", 1, None),
    # Банковская карта — без якоря, но явные пробелы между 4 группами цифр
    ("CARD_BARE",
     re.compile(r"(?<!\d)(\d{4}[\s]\d{4}[\s]\d{4}[\s]\d{4})(?!\d)"),
     "КАРТА", 1, None),
    # ── Телефоны (Итерация 2: A12) ───────────────────────────────────────
    # Российский формат: префикс +7 / 7 (без +) / 8, опциональные точки в
    # разделителях. Покрывает CRM-выгрузки (79001234567), формат с точками
    # (+7.900.123.45.67) и канонический +7 (495) XXX-XX-XX.
    # Применяется ПЕРЕД PHONE_INTL: российские номера съедаются здесь и не
    # доходят до международного паттерна.
    ("PHONE",
     re.compile(
         r"\b(?:\+?7|8)[\s\-.()]*\d{3}[\s\-.()]*\d{3}[\s\-.()]*\d{2}[\s\-.()]*\d{2}\b"
     ),
     "ТЕЛЕФОН", 0, None),
    # Международный E.164: обязательный префикс «+» с кодом страны (1-3 цифры),
    # дальше 7-15 цифр с любыми разделителями. Якорь — сам «+», поэтому ложных
    # срабатываний на длинных числах меньше. Российские (+7) уже съедены выше.
    ("PHONE_INTL",
     re.compile(
         r"\+\d{1,3}[\s\-.()]+\d[\d\s\-.()]{6,16}\d"
     ),
     "ТЕЛЕФОН", 0, None),
    # Адрес: индекс + улица/дом
    ("ADDRESS_INDEX",
     re.compile(
         r"\b\d{6},\s?[^.;\n]{5,200}(?:ул\.|улица|пр\.|проспект|"
         r"д\.|дом|корп\.|стр\.|оф\.|офис|кв\.|пом\.)[^.;\n]{0,80}"
     ),
     "АДРЕС", 0, None),
    # Длинные идентификаторы без префикса. Ровно 20 цифр сюда не входят:
    # это возможный расчётный счёт, его маскирует только ACC20_BARE при верном ключе.
    ("LONGNUM",
     re.compile(r"(?<!\d)(?:\d{16,19}|\d{21,25})(?!\d)"),
     "НОМЕР", 0, None),
]


class Anonymizer:
    """Двусторонний маппинг оригинал ↔ плейсхолдер для одного запроса."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}       # original → placeholder
        self._reverse: dict[str, str] = {}   # placeholder → original
        self._counters: dict[str, int] = {}

    # ── Public API ─────────────────────────────────────────────────────
    def anonymize(self, text: str) -> str:
        if not text:
            return text
        # 1. Защищаем whitelist подстановкой временных токенов (чтобы не задеть маскировкой).
        protected, wl_map = self._protect_whitelist(text)

        # 1.5. Опциональный NER-слой (Natasha): маскирует ФИО/организации, которые
        # regex пропускает (имена рядом со стоп-словами, компании без ОПФ). Гейтится
        # ANONYMIZER_NER_ENABLED; при выключенном флаге — no-op. Идёт ДО regex, чтобы
        # numeric-реквизиты затем обработал regex, а имена/орг уже были замаскированы.
        protected = self._apply_ner(protected)

        # 2. Прогоняем паттерны по порядку.
        # Когда accept-фильтр отвергает матч, продвигаемся на 1 символ от
        # его старта (а не на m.end(), как делает re.sub) — иначе следующий
        # валидный матч, перекрывающийся с отвергнутым, теряется. Пример:
        # «Подрядчик Дюпон Жан-Пьер Антуанович» — стоп-слово «подрядчик»
        # отвергает матч «Подрядчик Дюпон Жан-Пьер», но настоящий «Дюпон
        # Жан-Пьер Антуанович» должен быть найден следующим проходом.
        for name, pattern, label, group_idx, accept in _PATTERNS:
            protected = self._apply_pattern(protected, pattern, label, group_idx, accept)

        # 3. Возвращаем whitelist-токены обратно.
        for token, original in wl_map.items():
            protected = protected.replace(token, original)

        return protected

    def deanonymize(self, text: str) -> str:
        if not text or not self._reverse:
            return text
        # Сначала обёртки: модель иногда пишет [[КОМПАНИЯ_1]], `[КОМПАНИЯ_1]`
        # или `[[КОМПАНИЯ_1]]`. Если заменить внутренний токен раньше, скобки
        # и обратные кавычки останутся вокруг восстановленного значения.
        for placeholder in sorted(self._reverse, key=len, reverse=True):
            original = self._reverse[placeholder]
            for wrapped in (
                f"`[{placeholder}]`",
                f"[{placeholder}]",
                f"`{placeholder}`",
            ):
                text = text.replace(wrapped, original)
            text = text.replace(placeholder, original)
        # Фолбэк: LLM иногда воспроизводит плейсхолдеры без квадратных скобок
        # (КОМПАНИЯ_31 вместо [КОМПАНИЯ_31]). Проверяем голые метки отдельным проходом.
        for placeholder in sorted(self._reverse, key=len, reverse=True):
            bare = placeholder[1:-1]   # [КОМПАНИЯ_31] → КОМПАНИЯ_31
            if bare and bare in text:
                text = text.replace(bare, self._reverse[placeholder])
        return text

    # ── Internals ──────────────────────────────────────────────────────
    def _protect_whitelist(self, text: str) -> tuple[str, dict[str, str]]:
        wl_map: dict[str, str] = {}
        out = text
        for i, value in enumerate(_WHITELIST_SORTED):
            if value in out:
                token = f"\x00WL{i:04d}\x00"
                wl_map[token] = value
                out = out.replace(value, token)
        return out, wl_map

    def _apply_ner(self, text: str) -> str:
        """NER-пред-проход (Natasha). No-op, если флаг выключен или текст длинный.

        Маскирует найденные ФИО/организации литеральной заменой по границам слова
        через общий механизм _replace (плейсхолдеры консистентны, деанонимизация
        работает). Любая ошибка NER не должна ломать анонимизацию — fallback к
        regex-only.
        """
        if not text:
            return text
        try:
            from config import settings
            if not settings.anonymizer_ner_enabled:
                return text
            if len(text) > settings.anonymizer_ner_max_chars:
                return text
        except Exception:
            return text

        try:
            import ner_tagger
            tagger = ner_tagger.get_tagger()
            if tagger is None:
                return text
            entities = tagger.extract(text)
        except Exception as exc:
            import logging
            logging.getLogger("legal-assistant").warning(
                "[NER] extract failed: %s — regex-only", exc
            )
            return text

        # Длинные сущности раньше: «ПАО Сбербанк» заменяем до «Сбербанк».
        for ent_text, label in sorted(set(entities), key=lambda x: len(x[0]), reverse=True):
            if len(ent_text) < 3:
                continue
            pat = re.compile(r"(?<!\w)" + re.escape(ent_text) + r"(?!\w)")
            text = pat.sub(lambda m: self._replace(m.group(0), label), text)
        return text

    def _apply_pattern(
        self,
        text: str,
        pattern: Pattern[str],
        label: str,
        group_idx: int,
        accept: _AcceptFn | None,
    ) -> str:
        out: list[str] = []
        pos = 0
        n = len(text)
        while pos <= n:
            m = pattern.search(text, pos)
            if m is None:
                out.append(text[pos:])
                break
            if accept is not None and not accept(m):
                # Отвергнутый матч: переходим на 1 символ от старта,
                # чтобы не пропустить перекрывающийся валидный матч.
                step = max(m.start() + 1, pos + 1)
                out.append(text[pos:step])
                pos = step
                continue
            out.append(text[pos:m.start()])
            if group_idx == 0:
                out.append(self._replace(m.group(0), label))
            else:
                out.append(self._replace_group(m, group_idx, label))
            pos = m.end()
            if m.end() == m.start():
                pos += 1  # защита от пустого матча
        return "".join(out)

    def _replace_group(self, match: re.Match, group_idx: int, label: str) -> str:
        """Заменить только указанную группу, сохранив остальной матч (якоря типа «Наименование товара:»)."""
        full = match.group(0)
        captured = match.group(group_idx)
        if captured is None:
            return full
        start = match.start(group_idx) - match.start(0)
        end = match.end(group_idx) - match.start(0)
        placeholder = self._replace(captured, label)
        return full[:start] + placeholder + full[end:]

    # Метки, для которых важна длина цифр (реквизиты) — вшиваем суффикс для проверки формата.
    _DIGIT_LEN_LABELS = {
        "ИНН_ЮЛ", "ИНН_ФЛ", "КПП", "ОГРН", "ОГРНИП",
        "СЧЕТ", "БИК", "ПАСПОРТ", "НОМЕР",
    }

    def _replace(self, original: str, label: str) -> str:
        if original in self._map:
            return self._map[original]
        self._counters[label] = self._counters.get(label, 0) + 1
        idx = self._counters[label]
        suffix = ""
        if label in self._DIGIT_LEN_LABELS:
            digits = sum(ch.isdigit() for ch in original)
            if digits >= 6:
                suffix = f"_{digits}цифр"
        placeholder = f"[{label}_{idx}{suffix}]"
        self._map[original] = placeholder
        self._reverse[placeholder] = original
        return placeholder

    def stats(self) -> dict[str, int]:
        """Возвращает количество замен по типу ПДн без раскрытия значений.

        Пример: {"ФИО": 3, "ТЕЛЕФОН": 1, "ИНН_ЮЛ": 1}
        Используется для структурированного аудит-лога в main.py (фикс C3).
        """
        return dict(self._counters)

    # ── Debug ──────────────────────────────────────────────────────────
    def mapping(self) -> dict[str, str]:
        return dict(self._map)


def _load_whitelist_file() -> None:
    """Загружает дополнительные whitelist-записи из WHITELIST_FILE (фикс C1).

    Вызывается один раз при импорте модуля. Ошибки не критичны — логируются
    и игнорируются. Пересортировывает _WHITELIST_SORTED после загрузки.
    """
    import logging as _logging
    import pathlib as _pathlib
    _log = _logging.getLogger(__name__)
    try:
        from config import settings
        path = settings.whitelist_file
        if not path:
            return
        lines = _pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        added = 0
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                _WHITELIST.add(line)
                added += 1
        if added:
            global _WHITELIST_SORTED
            _WHITELIST_SORTED = sorted(_WHITELIST, key=len, reverse=True)
            _log.info(f"[WHITELIST] Загружено дополнительных записей: {added} из {path}")
    except FileNotFoundError:
        import logging as _l
        _l.getLogger(__name__).warning(f"[WHITELIST] Файл не найден: {path}")
    except Exception as exc:
        import logging as _l
        _l.getLogger(__name__).warning(f"[WHITELIST] Ошибка загрузки: {exc}")


_load_whitelist_file()


def dlp_scan(text: str) -> dict[str, int]:
    """DLP-сканер: подсчитывает ПДн-маркеры в тексте без замены (фикс D5).

    Используется при загрузке документов в RAG, чтобы предупредить администратора
    о наличии персональных данных в файле перед индексацией.
    Возвращает {метка_типа_ПДн: количество_совпадений}. Сами значения не сохраняются.
    Пример: {"ФИО": 2, "ТЕЛЕФОН": 1, "ИНН_ЮЛ": 1}
    """
    # Защищаем настроенный whitelist заглушками, чтобы не считать реквизиты
    # своей компании как ПДн третьих лиц.
    protected = text
    for value in _WHITELIST_SORTED:
        if value in protected:
            protected = protected.replace(value, "\x00" * len(value))

    counts: dict[str, int] = {}
    for _name, pattern, label, _group_idx, accept in _PATTERNS:
        for m in pattern.finditer(protected):
            if accept is not None and not accept(m):
                continue
            counts[label] = counts.get(label, 0) + 1
    return counts
