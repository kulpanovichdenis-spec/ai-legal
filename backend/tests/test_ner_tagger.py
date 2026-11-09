"""
Тесты NER-слоя анонимайзера (ner_tagger.py + интеграция в anonymizer.py).

Проверяют:
  - стоп-лист публичных организаций (госорганы/суды/регуляторы НЕ маскируются);
  - extract(): PER→ФИО, ORG-контрагент→КОМПАНИЯ, LOC и госорганы отброшены;
  - интеграцию с anonymizer: с включённым флагом ловится то, что regex пропускает
    (компания без ОПФ); с выключенным — поведение не меняется (no-op).

Если natasha не установлена (локальная среда без dev-зависимостей) — модуль
целиком пропускается.
"""
from __future__ import annotations

import pytest

import config
from ner_tagger import _is_public_org, get_tagger

pytest.importorskip("natasha", reason="natasha не установлена")


# ── Стоп-лист публичных организаций (чистая функция, без модели) ───────────────

class TestPublicOrgFilter:
    @pytest.mark.parametrize("name", [
        "Росздравнадзор",
        "Министерство здравоохранения",
        "Арбитражный суд города Москвы",
        "Верховный Суд РФ",
        "Банк России",
        "Федеральная служба по надзору в сфере здравоохранения",
    ])
    def test_public_bodies_excluded(self, name):
        assert _is_public_org(name) is True

    @pytest.mark.parametrize("name", [
        "Ромашка",
        "ПАО Сбербанк",
        "ООО Вектор-М",
    ])
    def test_counterparties_not_excluded(self, name):
        assert _is_public_org(name) is False


# ── Загрузка теггера один раз на модуль ───────────────────────────────────────

@pytest.fixture(scope="module")
def tagger():
    t = get_tagger()
    if t is None:
        pytest.skip("Natasha NER-теггер не загрузился")
    return t


class TestExtract:
    def test_per_tagged_as_fio(self, tagger):
        labels = {lbl for _, lbl in tagger.extract("Договор подписал Сидоров Пётр Алексеевич.")}
        assert "ФИО" in labels

    def test_org_counterparty_tagged_as_company(self, tagger):
        ents = tagger.extract("Поставку осуществляет ПАО Сбербанк по договору.")
        assert any(lbl == "КОМПАНИЯ" for _, lbl in ents)

    def test_public_org_not_masked(self, tagger):
        ents = tagger.extract("Росздравнадзор провёл внеплановую проверку.")
        assert all(lbl != "КОМПАНИЯ" for _, lbl in ents)

    def test_loc_skipped(self, tagger):
        ents = tagger.extract("Договор заключён в городе Москве сторонами.")
        assert not any(val == "Москве" for val, _ in ents)


# ── Интеграция с анонимайзером ────────────────────────────────────────────────

class TestAnonymizerNerIntegration:
    def test_company_without_opf_masked_when_enabled(self, tagger, monkeypatch):
        from anonymizer import Anonymizer
        monkeypatch.setattr(config.settings, "anonymizer_ner_enabled", True)
        out = Anonymizer().anonymize("Поставку осуществляет Ромашка по договору.")
        assert "Ромашка" not in out
        assert "[КОМПАНИЯ" in out

    def test_noop_when_disabled(self, monkeypatch):
        from anonymizer import Anonymizer
        monkeypatch.setattr(config.settings, "anonymizer_ner_enabled", False)
        # Без NER regex не ловит компанию без ОПФ — наглядно показывает вклад NER.
        out = Anonymizer().anonymize("Поставку осуществляет Ромашка по договору.")
        assert "Ромашка" in out

    def test_deanonymize_roundtrip_with_ner(self, tagger, monkeypatch):
        from anonymizer import Anonymizer
        monkeypatch.setattr(config.settings, "anonymizer_ner_enabled", True)
        text = "Стороны: ПАО Сбербанк и Сидоров Пётр Алексеевич."
        a = Anonymizer()
        masked = a.anonymize(text)
        assert masked != text
        assert a.deanonymize(masked) == text

    def test_long_text_skips_ner(self, tagger, monkeypatch):
        from anonymizer import Anonymizer
        monkeypatch.setattr(config.settings, "anonymizer_ner_enabled", True)
        monkeypatch.setattr(config.settings, "anonymizer_ner_max_chars", 50)
        # Текст длиннее лимита → NER не запускается, компания без ОПФ остаётся.
        out = Anonymizer().anonymize("Поставку осуществляет Ромашка " + "очень длинный текст " * 5)
        assert "Ромашка" in out
