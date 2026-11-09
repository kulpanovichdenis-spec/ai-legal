"""
Корпус тестов на анонимайзер ПДн (anonymizer.py).

Анонимайзер — privacy-критичный модуль: его выход уходит во внешний LLM.
Тесты фиксируют, что:
  - ключевые типы ПДн/реквизитов маскируются (нет утечки значения в выводе);
  - юридический контекст НЕ ловится как ФИО (стоп-листы B1/B2);
  - деанонимизация — точный round-trip;
  - dlp_scan считает маркеры, не раскрывая значений.

Каждый тест маскировки проверяет ДВА свойства:
  1. исходное чувствительное значение отсутствует в анонимизированном тексте;
  2. в тексте появился плейсхолдер ожидаемого типа [МЕТКА_...].
"""
from __future__ import annotations

import pytest

from anonymizer import Anonymizer, dlp_scan


def _anon(text: str) -> str:
    return Anonymizer().anonymize(text)


# ── Маскировка по типам ───────────────────────────────────────────────────────

class TestMasking:
    def test_email(self):
        out = _anon("Пишите на ivan.petrov@example.com по вопросу.")
        assert "ivan.petrov@example.com" not in out
        assert "[EMAIL_1]" in out

    def test_phone_ru(self):
        out = _anon("Телефон: +7 495 123-45-67, звоните.")
        assert "123-45-67" not in out
        assert "[ТЕЛЕФОН" in out

    def test_inn_ul_10(self):
        out = _anon("ИНН 7707083893 организации.")
        assert "7707083893" not in out
        assert "[ИНН_ЮЛ" in out

    def test_inn_fl_12(self):
        out = _anon("ИНН ФЛ 500100732259 физлица.")
        assert "500100732259" not in out
        assert "[ИНН_ФЛ" in out

    def test_ogrn_13(self):
        out = _anon("ОГРН 1027700132195 компании.")
        assert "1027700132195" not in out
        assert "[ОГРН" in out

    def test_ogrnip_15(self):
        out = _anon("ОГРНИП 304500116000157 предпринимателя.")
        assert "304500116000157" not in out
        assert "[ОГРНИП" in out

    def test_kpp(self):
        out = _anon("КПП 770701001 в реквизитах.")
        assert "770701001" not in out
        assert "[КПП" in out

    def test_snils(self):
        out = _anon("СНИЛС 112-233-445 95 работника.")
        assert "112-233-445 95" not in out
        assert "[СНИЛС" in out

    def test_passport(self):
        out = _anon("паспорт 45 11 123456 выдан.")
        assert "123456" not in out
        assert "[ПАСПОРТ" in out

    def test_driver_license(self):
        out = _anon("водительское удостоверение 77 АА 123456.")
        assert "[ВОД_УД" in out

    def test_account_20(self):
        out = _anon("расчётный счёт 40702810500000012345 в банке.")
        assert "40702810500000012345" not in out
        assert "[СЧЕТ" in out

    def test_iban(self):
        out = _anon("IBAN DE89370400440532013000 для перевода.")
        assert "DE89370400440532013000" not in out
        assert "[IBAN" in out

    def test_card(self):
        out = _anon("номер карты 4276 3801 2345 6789 оплата.")
        assert "4276 3801 2345 6789" not in out
        assert "[КАРТА" in out

    def test_company_with_opf(self):
        out = _anon("Поставщик — ООО «Ромашка» по договору.")
        assert "Ромашка" not in out
        assert "[КОМПАНИЯ" in out

    def test_fio_three_words(self):
        out = _anon("Подписал директор Сидоров Пётр Алексеевич лично.")
        assert "Сидоров Пётр Алексеевич" not in out
        assert "[ФИО" in out

    def test_fio_surname_initials(self):
        out = _anon("Согласовано: Иванов И.И. от лица компании.")
        assert "Иванов И.И" not in out
        assert "[ФИО" in out


# ── Стоп-листы: юридический контекст НЕ должен ловиться как ФИО ─────────────────

class TestStopwords:
    @pytest.mark.parametrize("text", [
        "Согласно Гражданскому кодексу Российской Федерации.",
        "Министерство Внутренних Дел утвердило порядок.",
        "Стороны заключили Договор Поставки товара.",
        "Арбитражный Суд города Москвы вынес решение.",
    ])
    def test_legal_phrases_not_masked_as_fio(self, text):
        out = _anon(text)
        assert "[ФИО" not in out


# ── Деанонимизация: точный round-trip ─────────────────────────────────────────

class TestRoundTrip:
    def test_deanonymize_restores_original(self):
        text = (
            "Иванов Иван Иванович, ИНН 7707083893, "
            "тел. +7 495 123-45-67, ООО «Ромашка»."
        )
        a = Anonymizer()
        masked = a.anonymize(text)
        assert masked != text  # что-то замаскировано
        assert a.deanonymize(masked) == text  # восстановлено точно

    def test_consistent_placeholder_for_same_value(self):
        a = Anonymizer()
        masked = a.anonymize("ИНН 7707083893 и снова ИНН 7707083893.")
        # Один и тот же ИНН → один и тот же плейсхолдер (счётчик не растёт).
        assert masked.count("[ИНН_ЮЛ_1") == 2

    def test_deanonymize_token_wrapped_in_extra_brackets_or_backticks(self):
        a = Anonymizer()
        a._reverse["[КОМПАНИЯ_1]"] = "ООО «Астория»"
        a._reverse["[ДАТА_1]"] = "16.07.2026"
        a._reverse["[СУММА_1]"] = "1 000 руб."
        a._reverse["[АДРЕС_1]"] = "Большая Морская"
        text = (
            "Организация `[[КОМПАНИЯ_1]]`, "
            "дата [[ДАТА_1]], "
            "сумма `[СУММА_1]`, "
            "адрес `[АДРЕС_1]`."
        )
        out = a.deanonymize(text)
        assert out == (
            "Организация ООО «Астория», "
            "дата 16.07.2026, "
            "сумма 1 000 руб., "
            "адрес Большая Морская."
        )


# ── DLP-сканер: счётчики без раскрытия значений ───────────────────────────────

class TestDlpScan:
    def test_counts_by_type(self):
        stats = dlp_scan("ИНН 7707083893, email a@b.ru, тел +7 495 123-45-67")
        assert stats.get("ИНН_ЮЛ", 0) >= 1
        assert stats.get("EMAIL", 0) >= 1
        assert stats.get("ТЕЛЕФОН", 0) >= 1

    def test_does_not_leak_values(self):
        stats = dlp_scan("ИНН 7707083893")
        # Ключи — только типы ПДн; самих значений среди ключей нет.
        assert "7707083893" not in stats
        assert all(not k.isdigit() for k in stats)

    def test_clean_text_no_markers(self):
        assert dlp_scan("Просто текст без персональных данных.") == {}


# ── Граничные случаи ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        assert _anon("") == ""

    def test_no_pii_unchanged(self):
        text = "Какой порядок согласования договора в компании?"
        assert _anon(text) == text

    def test_digit_length_suffix_in_placeholder(self):
        # Для реквизитов в плейсхолдер вшивается количество цифр (проверка формата).
        out = _anon("ИНН 7707083893")
        assert "_10цифр]" in out


# ── Реквизиты без подписи: только при верной контрольной сумме ───────────────
# ИНН/ОГРН — официальный ключ. Расчётный счёт — ключ по БИК из того же текста.
# Подпись «ИНН» / «ОГРН» / «р/с» по-прежнему маскирует номер и без проверки суммы.

_VALID_INN10 = "7707083893"
_INVALID_INN10 = "7707083894"
_VALID_INN12 = "500100732259"
_INVALID_INN12 = "500100732250"
_VALID_OGRN13 = "1027700132195"
_INVALID_OGRN13 = "1027700132196"
_VALID_OGRNIP = "304500116000157"
_INVALID_OGRNIP = "304500116000158"
# БИК кредитной организации + счёт с верным ключом (валюта 810 внутри номера).
_BIK_BANK = "044525225"
_ACCOUNT_OK = "40702810900000000000"
# 20 цифр с кодом валюты 810, но ключ к этому БИК не сходится.
_ACCOUNT_CURRENCY_BAD = "40702810123456789012"
# БИК РКЦ (разряды 7–9 = 000): префикс ключа «0» + 5–6-й разряды, не последние три.
_BIK_RKC = "044525000"
_ACCOUNT_RKC = "40102810300000000000"


class TestBareRequisiteChecksum:
    def test_bare_valid_inn10_masked_with_digit_suffix(self):
        out = _anon(f"Код {_VALID_INN10} в карточке.")
        assert _VALID_INN10 not in out
        assert "[ИНН_ЮЛ_1_10цифр]" in out

    def test_bare_invalid_inn10_not_masked(self):
        text = f"Номер договора {_INVALID_INN10}."
        assert _anon(text) == text

    def test_bare_valid_inn12_masked(self):
        out = _anon(f"Код {_VALID_INN12} в карточке.")
        assert _VALID_INN12 not in out
        assert "[ИНН_ФЛ_1_12цифр]" in out

    def test_bare_invalid_inn12_not_masked(self):
        text = f"Номер договора {_INVALID_INN12}."
        assert _anon(text) == text

    def test_bare_valid_ogrn_masked(self):
        out = _anon(f"Запись {_VALID_OGRN13} в реестре.")
        assert _VALID_OGRN13 not in out
        assert "[ОГРН_1_13цифр]" in out

    def test_bare_invalid_ogrn_not_masked(self):
        text = f"Штрихкод {_INVALID_OGRN13}."
        assert _anon(text) == text

    def test_bare_valid_ogrnip_masked(self):
        out = _anon(f"Запись {_VALID_OGRNIP} в реестре.")
        assert _VALID_OGRNIP not in out
        assert "[ОГРНИП_1_15цифр]" in out

    def test_bare_invalid_ogrnip_not_masked(self):
        text = f"Штрихкод {_INVALID_OGRNIP}."
        assert _anon(text) == text

    def test_account_masked_when_bik_checksum_matches(self):
        text = f"БИК {_BIK_BANK}\n{_ACCOUNT_OK}\n{_ACCOUNT_CURRENCY_BAD}"
        out = _anon(text)
        assert _ACCOUNT_OK not in out
        assert "[СЧЕТ_1_20цифр]" in out
        # Код валюты внутри 20 цифр сам по себе номер не маскирует.
        assert _ACCOUNT_CURRENCY_BAD in out

    def test_account_not_masked_without_matching_bik(self):
        text = f"Спецификация {_ACCOUNT_OK} без банка."
        assert _anon(text) == text

    def test_rkc_bik_account_masked(self):
        out = _anon(f"БИК {_BIK_RKC} {_ACCOUNT_RKC}")
        assert _ACCOUNT_RKC not in out
        assert "[СЧЕТ_1_20цифр]" in out

    def test_labeled_inn_still_masked_even_if_checksum_fails(self):
        out = _anon(f"ИНН {_INVALID_INN10} организации.")
        assert _INVALID_INN10 not in out
        assert "[ИНН_ЮЛ_1_10цифр]" in out

    def test_labeled_account_still_masked(self):
        out = _anon(f"р/с {_ACCOUNT_CURRENCY_BAD} в банке.")
        assert _ACCOUNT_CURRENCY_BAD not in out
        assert "[СЧЕТ_1_20цифр]" in out

    def test_bare_round_trip_keeps_placeholder_format(self):
        text = f"Код {_VALID_INN10}, БИК {_BIK_BANK}, {_ACCOUNT_OK}."
        anonymizer = Anonymizer()
        masked = anonymizer.anonymize(text)
        assert "[ИНН_ЮЛ_1_10цифр]" in masked
        assert "[СЧЕТ_1_20цифр]" in masked
        assert anonymizer.deanonymize(masked) == text

    def test_digits_inside_placeholder_are_not_masked(self):
        text = f"[ИНН_ЮЛ_{_VALID_INN10}_10цифр]"
        assert _anon(text) == text

    def test_dlp_counts_bare_valid_inn_once_and_not_labeled_twice(self):
        assert dlp_scan(_VALID_INN10).get("ИНН_ЮЛ") == 1
        assert dlp_scan(f"ИНН {_VALID_INN10}").get("ИНН_ЮЛ") == 1
