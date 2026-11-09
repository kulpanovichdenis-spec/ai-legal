"""
Document text extraction — supports PDF, DOCX, TXT, JPG, PNG.

Контракт `extract_text`:
- При успехе возвращает извлечённый текст (может быть пустой строкой, если документ
  не содержит текста — например, отсканированное изображение, для которого OCR
  ничего не распознал).
- При ошибках чтения/парсинга возвращает sentinel-строку вида "[Ошибка ...]" —
  это позволяет показать пользователю причину в чате. Для определения такой
  строки используйте `is_extraction_error()`.
"""

from pathlib import Path


def is_extraction_error(text: str) -> bool:
    """Возвращает True, если строка — sentinel-маркер ошибки извлечения.

    Используется в эндпоинте загрузки документа, чтобы не сохранять в БД и не
    индексировать в RAG fake-контент вида "[Ошибка чтения PDF: ...]".

    Эвристика: непустая строка, обёрнутая в [ ], короче 300 символов.
    Реальные документы такой формы практически не встречаются; даже если первый
    абзац начинается с [Глава 1], за ним всегда идёт длинное продолжение.
    """
    if not text:
        return False
    stripped = text.strip()
    return (
        len(stripped) < 300
        and stripped.startswith("[")
        and stripped.endswith("]")
    )


def extract_text(filepath: Path, filename: str, full_ocr: bool = False) -> str:
    """Extract text content from uploaded document.

    full_ocr: для PDF — прогнать OCR по всем страницам, а не только по тем,
    где pdfplumber не нашёл текстовый слой (см. extractor.extract_pdf).
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("txt", "md"):
        return filepath.read_text(encoding="utf-8", errors="replace")

    elif ext == "jsonl":
        import json
        lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
        parts = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parts.append(" | ".join(f"{k}: {v}" for k, v in obj.items() if v is not None))
                else:
                    parts.append(str(obj))
            except json.JSONDecodeError:
                parts.append(line)
        return "\n".join(parts)

    elif ext == "pdf":
        try:
            from extractor import extract_pdf
            return extract_pdf(filepath, full_ocr=full_ocr)
        except Exception as e:
            err = str(e).lower()
            if "encrypt" in err or "password" in err or "aes" in err:
                return "[PDF защищён паролем — извлечение текста невозможно]"
            # fallback на старый pypdf
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(filepath))
                pages = [p.extract_text() or "" for p in reader.pages]
                result = "\n\n".join(x for x in pages if x.strip())
                return result or _ocr_pdf(filepath)
            except Exception as e2:
                return f"[Ошибка чтения PDF: {e2}]"

    elif ext == "docx":
        try:
            return _extract_docx(filepath)
        except Exception as e:
            return f"[Ошибка чтения DOCX: {e}]"

    elif ext == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(f"[Лист: {sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    if any(c.strip() for c in cells):
                        parts.append("\t".join(cells))
            wb.close()
            return "\n".join(parts)
        except Exception as e:
            return f"[Ошибка чтения XLSX: {e}]"

    elif ext == "xls":
        try:
            import xlrd
            wb = xlrd.open_workbook(str(filepath))
            parts = []
            for sheet in wb.sheets():
                parts.append(f"[Лист: {sheet.name}]")
                for row_idx in range(sheet.nrows):
                    cells = [str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols)]
                    if any(c.strip() for c in cells):
                        parts.append("\t".join(cells))
            return "\n".join(parts)
        except Exception as e:
            return f"[Ошибка чтения XLS: {e}]"

    elif ext == "doc":
        return _extract_doc(filepath)

    elif ext in ("jpg", "jpeg", "png"):
        return _ocr_image(filepath)

    else:
        try:
            return filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return f"[Неподдерживаемый формат: {ext}]"


def _extract_docx(filepath: Path) -> str:
    """Текст DOCX: тело в порядке документа, затем колонтитулы.

    Абзацы и таблицы (включая вложенные) идут как в файле: строки, затем ячейки.
    Колонтитул, который дословно повторяет абзац тела, второй раз не добавляется.
    Сноски и комментарии python-docx отдельными объектами не отдаёт — их нет в тексте.
    """
    from docx import Document

    doc = Document(str(filepath))
    body = _docx_blocks(doc)
    seen = {part.strip() for part in body}
    headers: list[str] = []
    footers: list[str] = []
    even_pages = bool(doc.settings.odd_and_even_pages_header_footer)
    for section in doc.sections:
        for story in _docx_headers(section, even_pages):
            _docx_collect_new(headers, seen, _docx_blocks(story))
        for story in _docx_footers(section, even_pages):
            _docx_collect_new(footers, seen, _docx_blocks(story))
    return "\n\n".join(body + headers + footers)


def _docx_headers(section, even_pages: bool):
    stories = [section.header]
    if section.different_first_page_header_footer:
        stories.append(section.first_page_header)
    if even_pages:
        stories.append(section.even_page_header)
    return stories


def _docx_footers(section, even_pages: bool):
    stories = [section.footer]
    if section.different_first_page_header_footer:
        stories.append(section.first_page_footer)
    if even_pages:
        stories.append(section.even_page_footer)
    return stories


def _docx_collect_new(bucket: list[str], seen: set[str], parts: list[str]) -> None:
    for part in parts:
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        bucket.append(part)


def _docx_blocks(container) -> list[str]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parts: list[str] = []
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                parts.append(block.text)
        elif isinstance(block, Table):
            parts.extend(_docx_table(block))
    return parts


def _docx_table(table) -> list[str]:
    # Объединённая ячейка повторяется в каждой строке сетки — берём её один раз.
    parts: list[str] = []
    seen_cells: set[int] = set()
    for row in table.rows:
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen_cells:
                continue
            seen_cells.add(marker)
            parts.extend(_docx_blocks(cell))
    return parts


def _extract_doc(filepath: Path) -> str:
    """Извлечь текст из старого бинарного .doc (Word 97-2003).

    .doc — это OLE2-контейнер, а не текст. Без специального парсера read_text()
    прочитал бы сырые байты как UTF-8 → мусор из null-байтов (раздувает короткий
    договор до сотен тысяч символов, реквизиты/ИНН тонут или режутся лимитом).

    Используем antiword (хорошо держит таблицы; UTF-8.txt — маппинг кириллицы),
    с фолбэком на catdoc. Оба — системные пакеты, ставятся в Dockerfile.
    """
    import subprocess
    for cmd in (
        ["antiword", "-m", "UTF-8.txt", str(filepath)],
        ["catdoc", "-d", "utf-8", str(filepath)],
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=60)
        except FileNotFoundError:
            continue
        except Exception:
            continue
        text = out.stdout.decode("utf-8", errors="replace").replace("\x00", "").strip()
        if text:
            return text
    return "[Не удалось извлечь текст из .doc — нужен antiword или catdoc]"


def _ocr_image(filepath: Path) -> str:
    """Извлечь текст из изображения через OCR."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(str(filepath))
        text = pytesseract.image_to_string(img, lang="rus+eng")
        return text.strip() or "[Текст на изображении не обнаружен]"
    except Exception as e:
        return f"[Ошибка OCR: {e}]"


def _ocr_pdf(filepath: Path) -> str:
    """OCR для PDF без текстового слоя (сканы)."""
    try:
        import pytesseract
        from PIL import Image
        from pypdf import PdfReader
        import io
        reader = PdfReader(str(filepath))
        pages = []
        for page in reader.pages:
            for img_obj in page.images:
                img = Image.open(io.BytesIO(img_obj.data))
                text = pytesseract.image_to_string(img, lang="rus+eng")
                if text.strip():
                    pages.append(text.strip())
        return "\n\n".join(pages) if pages else "[PDF не содержит распознаваемого текста]"
    except Exception as e:
        return f"[Ошибка OCR PDF: {e}]"
