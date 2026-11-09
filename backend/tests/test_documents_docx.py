"""Извлечение текста DOCX: таблицы и колонтитулы доходят до анонимайзера."""
from __future__ import annotations

from pathlib import Path

from docx import Document

from documents import extract_text


def _write(path: Path, doc: Document) -> None:
    doc.save(path)


def test_table_cell_header_and_body_paragraph_are_extracted(tmp_path: Path):
    doc = Document()
    doc.add_paragraph("ТЕЛО-АБЗАЦ основной текст договора")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "ЯЧЕЙКА-ТАБЛИЦА реквизит только в таблице"
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "ВЛОЖЕННАЯ-ЯЧЕЙКА реквизит во вложенной таблице"
    table.cell(0, 1).text = "ВТОРАЯ-ЯЧЕЙКА соседняя ячейка той же строки"
    section = doc.sections[0]
    section.header.paragraphs[0].text = "ШАПКА-КОЛОНТИТУЛ реквизит только в верхнем колонтитуле"
    section.footer.paragraphs[0].text = "ПОДВАЛ-КОЛОНТИТУЛ реквизит только в нижнем колонтитуле"
    path = tmp_path / "requisites.docx"
    _write(path, doc)

    text = extract_text(path, "requisites.docx")

    assert "ТЕЛО-АБЗАЦ основной текст договора" in text
    assert "ЯЧЕЙКА-ТАБЛИЦА реквизит только в таблице" in text
    assert "ВЛОЖЕННАЯ-ЯЧЕЙКА реквизит во вложенной таблице" in text
    assert "ВТОРАЯ-ЯЧЕЙКА соседняя ячейка той же строки" in text
    assert "ШАПКА-КОЛОНТИТУЛ реквизит только в верхнем колонтитуле" in text
    assert "ПОДВАЛ-КОЛОНТИТУЛ реквизит только в нижнем колонтитуле" in text
    # Тело (абзацы и ячейки по чтению), затем колонтитулы.
    assert text.index("ТЕЛО-АБЗАЦ") < text.index("ЯЧЕЙКА-ТАБЛИЦА")
    assert text.index("ЯЧЕЙКА-ТАБЛИЦА") < text.index("ВЛОЖЕННАЯ-ЯЧЕЙКА")
    assert text.index("ВЛОЖЕННАЯ-ЯЧЕЙКА") < text.index("ВТОРАЯ-ЯЧЕЙКА")
    assert text.index("ВТОРАЯ-ЯЧЕЙКА") < text.index("ШАПКА-КОЛОНТИТУЛ")
    assert text.index("ШАПКА-КОЛОНТИТУЛ") < text.index("ПОДВАЛ-КОЛОНТИТУЛ")


def test_header_paragraph_already_in_body_is_not_repeated(tmp_path: Path):
    doc = Document()
    doc.add_paragraph("Повторяющийся абзац ШАПКА-И-ТЕКСТ")
    doc.add_paragraph("Уникальный абзац тела")
    header = doc.sections[0].header
    header.paragraphs[0].text = "Повторяющийся абзац ШАПКА-И-ТЕКСТ"
    header.add_paragraph("Только колонтитул УНИКАЛЬНАЯ-ШАПКА")
    path = tmp_path / "dup.docx"
    _write(path, doc)

    text = extract_text(path, "dup.docx")

    assert text.count("Повторяющийся абзац ШАПКА-И-ТЕКСТ") == 1
    assert "Уникальный абзац тела" in text
    assert "Только колонтитул УНИКАЛЬНАЯ-ШАПКА" in text
