"""
Двухстадийное извлечение текста из PDF.

Стадия 1: pdfplumber — быстро берёт текстовый слой.
Стадия 2: если текстового слоя нет (скан) — PyMuPDF рендерит страницы в 144 DPI
          и Tesseract распознаёт (rus+eng). Страницы OCR-ятся параллельно —
          GIL отпускается во время subprocess-вызова tesseract.

Используется только для PDF. DOCX/TXT/изображения обрабатываются в documents.py.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger("legal-assistant")

_MIN_TEXT_LAYER_CHARS = 40  # если на странице меньше — считаем её сканом


def extract_pdf(filepath: Path, full_ocr: bool = False) -> str:
    """Извлечь текст из PDF, при отсутствии слоя — OCR.

    full_ocr=True: OCR прогоняется по ВСЕМ страницам, даже если pdfplumber
    уже нашёл на них текстовый слой. Нужно для рекламных креативов — бренд/
    лого там часто сделан как векторная графика (текст переведён в кривые
    для печати) и невидим для текстового слоя, хотя страница не считается
    сканом из-за остального текста (claims, сноски). Результат OCR
    добавляется к тексту слоя, а не заменяет его.
    """
    try:
        text_pages, empty_indices = _try_text_layer(filepath)
    except Exception as e:
        logger.warning(f"[PDF] pdfplumber failed for {filepath.name}: {e}")
        text_pages, empty_indices = [], None

    if empty_indices is None:
        logger.info(f"[PDF] {filepath.name}: текстовый слой не читается, OCR всех страниц")
        ocr_pages = _ocr_all_pages(filepath)
        return "\n\n".join(p for p in ocr_pages if p.strip()) or "[PDF не содержит распознаваемого текста]"

    total = len(text_pages)

    if full_ocr:
        logger.info(f"[PDF] {filepath.name}: full_ocr — OCR всех {total} стр. (рекламный креатив)")
        ocr_results = _ocr_pages(filepath, list(range(total)))
        for idx, ocr_text in enumerate(ocr_results):
            ocr_text = ocr_text.strip()
            if idx in empty_indices:
                text_pages[idx] = ocr_text
            elif ocr_text and ocr_text not in text_pages[idx]:
                text_pages[idx] = text_pages[idx] + "\n\n[OCR — включая графические элементы]:\n" + ocr_text
        return "\n\n".join(p for p in text_pages if p.strip()) or "[PDF не содержит распознаваемого текста]"

    if not empty_indices:
        logger.info(f"[PDF] {filepath.name}: текстовый слой — все {total} стр., OCR не требуется")
        joined = "\n\n".join(p for p in text_pages if p.strip())
        return joined or "[PDF пуст]"

    logger.info(
        f"[PDF] {filepath.name}: гибрид — текст на {total - len(empty_indices)}/{total} стр., "
        f"OCR на {len(empty_indices)} стр."
    )
    ocr_results = _ocr_pages(filepath, empty_indices)
    for idx, ocr_text in zip(empty_indices, ocr_results):
        text_pages[idx] = ocr_text
    return "\n\n".join(p for p in text_pages if p.strip()) or "[PDF не содержит распознаваемого текста]"


def _try_text_layer(filepath: Path) -> tuple[list[str], list[int] | None]:
    """Возвращает (страницы, индексы_пустых). Если pdfplumber не справился — (_, None)."""
    import pdfplumber

    text_pages: list[str] = []
    empty: list[int] = []
    with pdfplumber.open(str(filepath)) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text_pages.append(text)
            if len(text.strip()) < _MIN_TEXT_LAYER_CHARS:
                empty.append(i)
    return text_pages, empty


def _ocr_pages(filepath: Path, indices: list[int]) -> list[str]:
    """OCR только указанных страниц."""
    if not indices:
        return []
    workers = min(len(indices), max(1, (os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda i: _ocr_single_page(filepath, i), indices))
    return results


def _ocr_all_pages(filepath: Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(filepath)) as doc:
            n = doc.page_count
    except Exception as e:
        logger.error(f"PyMuPDF failed to open {filepath.name}: {e}")
        return []
    return _ocr_pages(filepath, list(range(n)))


def _ocr_single_page(filepath: Path, page_index: int) -> str:
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io

        with fitz.open(str(filepath)) as doc:
            page = doc.load_page(page_index)
            # scale=2.0 → ~144 DPI, баланс качества и скорости
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            img_bytes = pix.tobytes("png")

        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(
            img,
            lang="rus+eng",
            config="--psm 6 -c preserve_interword_spaces=1 -c thread_limit=1",
        )
        return text.strip()
    except Exception as e:
        logger.error(f"OCR page {page_index} of {filepath.name} failed: {e}")
        return ""
