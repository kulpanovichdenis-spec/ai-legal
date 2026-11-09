"""
RAG (Retrieval-Augmented Generation) — семантический поиск по документам компании.

Dense-эмбеддер задаётся настройкой rag_embed_model (по умолчанию BAAI/bge-m3,
multilingual, 1024d). Векторное хранилище: ChromaDB (PersistentClient).
"""

import re
import asyncio
import logging
from itertools import zip_longest
from pathlib import Path

import bm25_index
import reranker
from config import settings

logger = logging.getLogger("rag")

# Reciprocal Rank Fusion: классическая константа k=60 (см. Cormack et al., 2009).
RRF_K = 60


def _rerank_if_enabled(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """Применяет cross-encoder реранкер если включён.

    Реранк делается ДО diversity, чтобы лучшие чанки не вылетели по квоте
    MAX_PER_DOC раньше времени. После реранка diversity получает уже
    отсортированный пул и выбирает top_k с соблюдением квот.

    Cross-encoder скорит только голову RRF-ранжирования (rag_rerank_candidates
    пар) — каждая пара это forward-pass ~568M-модели на CPU, и без потолка
    режим ad_check (пул ~120) уходил в десятки секунд. Хвост не скорится и
    остаётся позади головы в RRF-порядке, чтобы top_k > candidates не терял
    объём выдачи.
    """
    if not settings.rag_rerank_enabled or not candidates:
        return candidates[:top_n]
    cap = settings.rag_rerank_candidates
    head, tail = candidates[:cap], candidates[cap:]
    import time as _t
    t0 = _t.monotonic()
    reranked = reranker.rerank(
        query=query,
        candidates=head,
        model_name=settings.rag_rerank_model,
        max_length=settings.rag_rerank_max_length,
        top_n=len(head),
    )
    logger.info(
        f"[RERANK] candidates={len(head)}/{len(candidates)} "
        f"max_len={settings.rag_rerank_max_length} elapsed={_t.monotonic()-t0:.1f}s"
    )
    return (reranked + tail)[:top_n]

COLLECTION_NAME = "documents"
CHUNK_SIZE = 900        # символов — немного увеличен для сохранения полных абзацев
CHUNK_OVERLAP = 150     # символов перекрытия для сохранения контекста на границах
MAX_PER_DOC = 3         # максимум чанков из одной (файл, раздел) пары
MAX_PER_FILE = 8        # глобальный потолок чанков на файл независимо от числа разделов

# Документы крупнее этого порога индексируются с предварительным разбиением
# по заголовкам markdown (H1/H2/H3). Это решает проблему гигантских файлов
# (например, fas_pharma_cases.md ~17 МБ): без секционирования один файл
# даёт тысячи чанков, в top-K попадает мизерная фракция.
LARGE_DOC_THRESHOLD = 100_000   # символов
HUGE_DOC_WARN_THRESHOLD = 5_000_000

_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)

# Статья N.[.M] [текст] — soft chunk boundary внутри секций (нормативка / ФЗ).
# Срабатывает только если строка ЦЕЛИКОМ начинается со «Статья N.».
_ARTICLE_RE = re.compile(
    r"^Статья\s+(\d+(?:\.\d+)?)\.\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)

# Нумерованный раздел внутренних регламентов: «1. Цель», «2.3 Подпункт».
# Требования к матчу (защита от ложных срабатываний на оглавлении и сплошном тексте):
#   - строка начинается на номер N(.M){0,3} + «.» или «)» (опционально) + пробел;
#   - заголовок начинается с заглавной кириллической буквы;
#   - заголовок НЕ содержит цифр (отсеивает строки оглавления вида «1. Цель 3»);
#   - заголовок заканчивается кириллической буквой (отсеивает «1. Цель 3»);
#   - длина заголовка 3–100 символов.
_NUMBERED_SECTION_RE = re.compile(
    r"^(\d+(?:\.\d+){0,3})[.)]?\s+"
    r"([А-ЯЁ][^\n\d]{2,100}?[а-яёА-ЯЁ])\s*$",
    re.MULTILINE,
)

# Максимальная длина breadcrumb-префикса в чанке (защита от monstrously
# глубокой иерархии заголовков).
_BREADCRUMB_MAX_CHARS = 200

_model = None
_client = None
_collection = None
_chroma_path: str | None = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Загрузка модели {settings.rag_embed_model}...")
        _model = SentenceTransformer(settings.rag_embed_model)
        logger.info("Модель загружена")
    return _model


def _get_collection(chroma_path: str):
    global _client, _collection, _chroma_path
    if _collection is None or _chroma_path != chroma_path:
        import chromadb
        Path(chroma_path).mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=chroma_path)
        # embed_model в метаданных фиксируется при создании коллекции;
        # needs_reindex() сравнивает его с текущей настройкой.
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "embed_model": settings.rag_embed_model},
        )
        _chroma_path = chroma_path
    return _collection


def _chunk_text(text: str) -> list[str]:
    """
    Разбивка текста с учётом структуры документа:
    1. Сначала делим по абзацам (двойной перевод строки).
    2. Если абзац длиннее CHUNK_SIZE — делим по предложениям.
    3. Соседние абзацы объединяем, пока не превысим CHUNK_SIZE.
    4. На границах чанков сохраняем перекрытие CHUNK_OVERLAP символов.
    """
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""

    def _flush(buf: str) -> str:
        """Сохраняет чанк и возвращает хвост-перекрытие для следующего."""
        if buf.strip():
            chunks.append(buf.strip())
        return buf[-CHUNK_OVERLAP:].lstrip() if CHUNK_OVERLAP and len(buf) > CHUNK_OVERLAP else ""

    for para in paragraphs:
        if len(para) > CHUNK_SIZE:
            # Абзац сам по себе больше лимита — делим по предложениям
            if current.strip():
                current = _flush(current)
            sentences = re.split(r'(?<=[.!?;])\s+', para)
            for sent in sentences:
                combined = (current + " " + sent).strip() if current else sent
                if len(combined) > CHUNK_SIZE:
                    current = _flush(current)
                    current = sent
                else:
                    current = combined
        else:
            combined = (current + "\n\n" + para).strip() if current else para
            if len(combined) > CHUNK_SIZE:
                current = _flush(current)
                current = para
            else:
                current = combined

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _split_into_breadcrumbed_sections(text: str) -> list[tuple[str, str]]:
    """Разбивает markdown по заголовкам H1/H2/H3 со сборкой полного breadcrumb.

    Возвращает [(breadcrumb, body), ...]. Breadcrumb — путь от корня вида
    «H1 → H2 → H3». Глубина определяется числом '#'; при новом заголовке
    того же или меньшего уровня — стек обрезается. Пролог до первого
    заголовка сохраняется с пустым breadcrumb.
    """
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        prologue = text[: matches[0].start()].strip()
        if prologue:
            sections.append(("", prologue))

    stack: list[tuple[int, str]] = []  # [(level, title), …]
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        # Обрезаем стек: вершины уровня ≥ текущего больше не предки.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        breadcrumb = " → ".join(t for _, t in stack)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            sections.append((breadcrumb[:_BREADCRUMB_MAX_CHARS], body))

    return sections


def _split_by_articles(body: str) -> list[tuple[str, str]]:
    """Если в теле есть строки «Статья N.» или нумерованные заголовки разделов
    («1. Цель», «2.3 Порядок согласования») — делим по ним; иначе один кусок.

    Возвращает [(label, body), ...]. Заголовок остаётся внутри тела (для
    самостоятельного эмбеддинга), label дополняет breadcrumb.

    Если присутствуют ОБЕ структуры — приоритет у «Статья N.» (характерна
    для нормативки), нумерованные секции игнорируются. Это защищает от
    двойного дробления гибридных документов.
    """
    article_matches = list(_ARTICLE_RE.finditer(body))
    if article_matches:
        sources = [
            (m.start(), m.end(), f"Статья {m.group(1)}"
             + (f". {m.group(2).strip()[:120]}" if m.group(2).strip() else ""))
            for m in article_matches
        ]
    else:
        section_matches = list(_NUMBERED_SECTION_RE.finditer(body))
        if not section_matches:
            return [("", body)]
        sources = [
            (m.start(), m.end(), f"{m.group(1)}. {m.group(2).strip()[:120]}")
            for m in section_matches
        ]

    result: list[tuple[str, str]] = []
    if sources[0][0] > 0:
        pre = body[: sources[0][0]].strip()
        if pre:
            result.append(("", pre))

    for i, (start, _end, label) in enumerate(sources):
        end = sources[i + 1][0] if i + 1 < len(sources) else len(body)
        sec_body = body[start:end].strip()
        if sec_body:
            result.append((label, sec_body))
    return result


def _build_chunks_with_sections(text: str) -> list[tuple[str, str, int]]:
    """Возвращает [(chunk_with_breadcrumb, breadcrumb, section_index), ...].

    Атомарное чанкование: каждый чанк получает префикс с полным
    breadcrumb («[H1 → H2 → Статья 14] \\n\\n текст»), что даёт эмбеддингу
    контекст структуры — даже короткий пункт в глубине документа
    self-contained и выживает в попарных сравнениях.

    Внутри секции, если попадаются строки «Статья N.», тело делится по
    границам статей — статья становится атомарной единицей и не сливается
    с соседней.
    """
    sections = _split_into_breadcrumbed_sections(text)
    result: list[tuple[str, str, int]] = []
    sec_idx = 0
    for breadcrumb, body in sections:
        for art_label, art_body in _split_by_articles(body):
            if breadcrumb and art_label:
                full_label = f"{breadcrumb} → {art_label}"
            else:
                full_label = art_label or breadcrumb
            full_label = full_label[:_BREADCRUMB_MAX_CHARS]
            for chunk in _chunk_text(art_body):
                prefixed = f"[{full_label}]\n\n{chunk}" if full_label else chunk
                result.append((prefixed, full_label, sec_idx))
            sec_idx += 1
    return result


def index_document(
    doc_id: str,
    text: str,
    mode: str,
    category: str,
    original_filename: str,
    chroma_path: str,
) -> None:
    if not text or text.startswith("["):
        return

    if len(text) >= HUGE_DOC_WARN_THRESHOLD:
        logger.warning(
            f"[INDEX] Очень большой документ '{original_filename}': {len(text)} симв. "
            "Рекомендуется разбить на тематические разделы и загрузить отдельно."
        )

    collection = _get_collection(chroma_path)
    model = _get_model()

    try:
        collection.delete(where={"doc_id": {"$eq": doc_id}})
    except Exception:
        pass

    chunks_with_meta = _build_chunks_with_sections(text)
    if not chunks_with_meta:
        return

    chunks = [c[0] for c in chunks_with_meta]
    ids = [f"{doc_id}_{i}" for i in range(len(chunks_with_meta))]
    embeddings = model.encode(chunks, show_progress_bar=False).tolist()
    metadatas = [
        {
            "doc_id": doc_id,
            "mode": mode,
            "category": category,
            "filename": original_filename,
            "chunk_index": i,
            "section_title": meta_title,
            "section_index": sec_idx,
        }
        for i, (_, meta_title, sec_idx) in enumerate(chunks_with_meta)
    ]

    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    n_sections = len({m[2] for m in chunks_with_meta})
    logger.info(
        f"Проиндексировано {len(chunks)} чанков "
        f"({n_sections} разд.): {original_filename}"
    )

    # Параллельно индексируем чанки в BM25-индекс с лемматизацией.
    # На крупных файлах (fas_pharma_cases.md, ~28k чанков) это занимает время —
    # лемматизация кешируется, так что повторные документы быстрее.
    if settings.rag_bm25_enabled:
        try:
            bm25 = bm25_index.get_index(chroma_path)
            # Сначала вычистить старые чанки этого doc_id (на случай переиндексации)
            bm25.delete_by_doc_id(doc_id)
            bm25.add_batch([
                (ids[i], chunks[i], metadatas[i]) for i in range(len(chunks))
            ])
            bm25.save()
        except Exception as e:
            logger.warning(f"BM25 indexing failed for {original_filename}: {e}")


def delete_document(doc_id: str, chroma_path: str) -> None:
    try:
        collection = _get_collection(chroma_path)
        collection.delete(where={"doc_id": {"$eq": doc_id}})
        logger.info(f"Удалены чанки документа {doc_id} из индекса")
    except Exception as e:
        logger.warning(f"Ошибка удаления из индекса: {e}")
    if settings.rag_bm25_enabled:
        try:
            bm25 = bm25_index.get_index(chroma_path)
            removed = bm25.delete_by_doc_id(doc_id)
            if removed:
                bm25.save()
        except Exception as e:
            logger.warning(f"BM25 delete failed for {doc_id}: {e}")


def _meta_to_item(doc: str, meta: dict) -> dict:
    return {
        "content": doc,
        "filename": meta.get("filename", ""),
        "category": meta.get("category", ""),
        "_doc_id": meta.get("doc_id", ""),
        "_chunk_index": meta.get("chunk_index", 0),
        "_section_index": meta.get("section_index", 0),
    }


def _raw_search(query: str, mode: str, n: int, chroma_path: str) -> list[dict]:
    """Гибридный поиск: dense (Chroma) + BM25 (если включён), слитие через RRF.

    Возвращает до n сырых результатов с post-фильтром по mode и служебными
    полями _doc_id/_chunk_index/_section_index для последующего обогащения.
    Поддерживает comma-separated режимы в метаданных (пример: mode="consult,ad_check").

    BM25 выручает там, где dense слаб: точные совпадения по бренду
    («Примерин»), номеру статьи («ст. 14.3 КоАП»), erid, наименованиям.
    Dense выручает на парафразах и смысловых вопросах. RRF (k=60) сливает
    оба источника без необходимости калибровать сырые скоры.
    """
    collection = _get_collection(chroma_path)
    total = collection.count()
    if total == 0 or not query.strip():
        return []

    fetch_n = min(n * 4, total)

    # 1) Dense (Chroma)
    model = _get_model()
    query_embedding = model.encode([query], show_progress_bar=False).tolist()[0]
    dense = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_n,
    )
    dense_ids: list[str] = (dense.get("ids") or [[]])[0] or []
    dense_docs: list[str] = (dense.get("documents") or [[]])[0] or []
    dense_metas: list[dict] = (dense.get("metadatas") or [[]])[0] or []
    dense_lookup = {
        cid: (doc, meta) for cid, doc, meta in zip(dense_ids, dense_docs, dense_metas)
    }

    # 2) BM25 (если включён)
    bm25_ids: list[str] = []
    bm25_index_obj = None
    if settings.rag_bm25_enabled:
        try:
            bm25_index_obj = bm25_index.get_index(chroma_path)
            bm25_hits = bm25_index_obj.search(query, fetch_n)
            bm25_ids = [h[0] for h in bm25_hits]
        except Exception as e:
            logger.warning(f"BM25 search error: {e}")

    # 3) RRF фьюзинг
    rrf_scores: dict[str, float] = {}
    for rank, cid in enumerate(dense_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    bm25_weight = settings.rag_bm25_weight
    for rank, cid in enumerate(bm25_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + bm25_weight / (RRF_K + rank + 1)

    fused_ids = sorted(rrf_scores.keys(), key=lambda c: rrf_scores[c], reverse=True)

    # 4) Подбор content/metadata с post-фильтром по mode
    filtered: list[dict] = []
    for cid in fused_ids:
        if cid in dense_lookup:
            doc, meta = dense_lookup[cid]
        elif bm25_index_obj is not None:
            ch = bm25_index_obj.get(cid)
            if not ch:
                continue
            doc, meta = ch["content"], ch["metadata"]
        else:
            continue
        doc_modes = [m.strip() for m in meta.get("mode", "all").split(",")]
        if "all" in doc_modes or mode in doc_modes:
            filtered.append(_meta_to_item(doc, meta))
            if len(filtered) >= n:
                break

    return filtered


def _enrich_with_adjacent(items: list[dict], chroma_path: str) -> list[dict]:
    """Обогащает каждый найденный чанк содержимым соседних чанков (i-1, i+1).

    Это сохраняет контекст на границах разбивки: если релевантный фрагмент
    начинается с середины абзаца или сразу за заголовком статьи, LLM видит
    полный смысловой блок, а не обрезанный кусок.
    Соседи добавляются только для дополнительного контекста; дублей в выдаче
    не возникает, поскольку их содержимое вливается в тот же чанк.
    """
    if not items:
        return items

    collection = _get_collection(chroma_path)

    # Собираем все id соседних чанков одним списком
    adjacent_ids: list[str] = []
    for item in items:
        doc_id = item["_doc_id"]
        idx = item["_chunk_index"]
        if doc_id:
            if idx > 0:
                adjacent_ids.append(f"{doc_id}_{idx - 1}")
            adjacent_ids.append(f"{doc_id}_{idx + 1}")

    # Загружаем соседей одним batch-запросом
    adj_map: dict[str, str] = {}
    if adjacent_ids:
        try:
            result = collection.get(ids=adjacent_ids)
            for adj_id, adj_doc in zip(result["ids"], result["documents"]):
                if adj_doc:
                    adj_map[adj_id] = adj_doc
        except Exception as e:
            logger.debug(f"Adjacent chunk fetch: {e}")

    enriched: list[dict] = []
    for item in items:
        doc_id = item["_doc_id"]
        idx = item["_chunk_index"]

        prev_text = adj_map.get(f"{doc_id}_{idx - 1}", "") if idx > 0 else ""
        next_text = adj_map.get(f"{doc_id}_{idx + 1}", "")

        parts: list[str] = []
        if prev_text:
            parts.append(prev_text)
        parts.append(item["content"])
        if next_text:
            parts.append(next_text)

        enriched.append({
            "content": "\n\n".join(parts),
            "filename": item["filename"],
            "category": item["category"],
        })

    return enriched


def _apply_diversity(candidates: list[dict], top_k: int) -> list[dict]:
    """Двухуровневая диверсификация выдачи.

    - На уровне (файл, раздел) — не более MAX_PER_DOC чанков. Это позволяет
      релевантному прецеденту/статье вернуть до 3 связанных фрагментов.
    - На уровне файла — не более MAX_PER_FILE. Защита от ситуации, когда
      гигантский документ (например, fas_pharma_cases.md) с десятками
      релевантных разделов заполоняет всю выдачу.
    """
    section_counts: dict[tuple[str, int], int] = {}
    file_counts: dict[str, int] = {}
    result: list[dict] = []
    for item in candidates:
        fname = item["filename"]
        sec_idx = item.get("_section_index", 0)
        sec_key = (fname, sec_idx)
        if section_counts.get(sec_key, 0) >= MAX_PER_DOC:
            continue
        if file_counts.get(fname, 0) >= MAX_PER_FILE:
            continue
        section_counts[sec_key] = section_counts.get(sec_key, 0) + 1
        file_counts[fname] = file_counts.get(fname, 0) + 1
        result.append(item)
        if len(result) >= top_k:
            break
    return result


def _candidate_pool_size(top_k: int) -> int:
    """Размер пула кандидатов для реранкера (если включён) или для diversity."""
    base = top_k * 4
    if settings.rag_rerank_enabled:
        return max(base, settings.rag_rerank_candidates)
    return base


def search(query: str, mode: str, top_k: int, chroma_path: str) -> list[dict]:
    """
    Гибридный поиск (dense+BM25 → RRF) с опциональным cross-encoder реранкером,
    диверсификацией по разделам и контекстным обогащением.

    Шаги:
    1. _raw_search: dense+BM25, RRF, mode-фильтр → пул кандидатов.
    2. (опционально) Cross-encoder реранкер пересортировывает пул.
    3. Diversity: ≤MAX_PER_DOC на (файл, раздел), ≤MAX_PER_FILE на файл.
    4. Обогащение соседними чанками (i±1).
    """
    try:
        pool = _candidate_pool_size(top_k)
        candidates = _raw_search(query, mode, pool, chroma_path)
        candidates = _rerank_if_enabled(query, candidates, top_k * 4)
        selected = _apply_diversity(candidates, top_k)
        return _enrich_with_adjacent(selected, chroma_path)
    except Exception as e:
        logger.warning(f"RAG search error: {e}")
        return []


def search_dual(
    primary_query: str,
    secondary_query: str,
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    """
    Двойной поиск с диверсификацией и контекстным обогащением.

    primary_query  — полный текст запроса с именем продукта → находит листок-вкладыш.
    secondary_query — только текст нарушения без имени продукта → находит нормативные чанки.

    Результаты чередуются, дедуплицируются по содержимому, ограничиваются MAX_PER_DOC
    на документ, затем обогащаются соседними чанками.
    Используется в режиме ad_check: primary = «Примерин препарат номер один»,
    secondary = «препарат номер один».
    """
    try:
        n = _candidate_pool_size(top_k) // 2 or top_k * 3
        primary = _raw_search(primary_query, mode, n, chroma_path)
        secondary = _raw_search(secondary_query, mode, n, chroma_path) if secondary_query.strip() else []

        seen_content: set[str] = set()
        merged: list[dict] = []
        for p, s in zip_longest(primary, secondary):
            for item in (p, s):
                if item and item["content"] not in seen_content:
                    seen_content.add(item["content"])
                    merged.append(item)

        # Реранкер использует основной запрос (с именем продукта) — он содержит
        # наиболее полную информацию о намерении пользователя.
        merged = _rerank_if_enabled(primary_query, merged, top_k * 4)
        selected = _apply_diversity(merged, top_k)
        return _enrich_with_adjacent(selected, chroma_path)
    except Exception as e:
        logger.warning(f"RAG dual search error: {e}")
        return []


def search_multi_query(
    queries: list[str],
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    """
    Гибридный поиск по НЕСКОЛЬКИМ переформулированным запросам с RRF-слиянием.

    Используется когда `query_rewriter` развернул сообщение пользователя в
    2–4 поисковые формулировки (широкая + узкие). Идея: пользователь пишет
    разговорно, но эмбеддер и BM25 ищут по конкретным токенам. Несколько
    формулировок (с юридической лексикой) повышают recall.

    Шаги:
    1. Для каждого запроса — _raw_search (dense+BM25 RRF + mode-фильтр).
    2. RRF поверх ранжированных списков (k=60).
    3. Реранкер по первому (широкому) запросу — он содержит основную интенцию.
    4. Diversity + обогащение соседями.

    На случай пустого/одиночного списка — fallback на обычный search().
    """
    queries = [q.strip() for q in queries if q and q.strip()]
    if not queries:
        return []
    if len(queries) == 1:
        return search(queries[0], mode, top_k, chroma_path)

    try:
        pool = _candidate_pool_size(top_k)
        ranked_lists: list[list[dict]] = []
        for q in queries:
            ranked = _raw_search(q, mode, pool, chroma_path)
            if ranked:
                ranked_lists.append(ranked)

        if not ranked_lists:
            return []

        # RRF: для каждого документа суммируем 1/(k + rank) по всем спискам.
        # Ключ — content (стабильнее id чанка между списками).
        K = 60
        scores: dict[str, float] = {}
        first_seen: dict[str, dict] = {}
        for ranked in ranked_lists:
            for rank, item in enumerate(ranked):
                key = item["content"]
                scores[key] = scores.get(key, 0.0) + 1.0 / (K + rank + 1)
                if key not in first_seen:
                    first_seen[key] = item

        merged = sorted(
            first_seen.values(),
            key=lambda it: scores[it["content"]],
            reverse=True,
        )

        # Реранкер использует первый (широкий) запрос — он несёт основную
        # интенцию; узкие добавляли recall, но менее репрезентативны для cross-encoder.
        merged = _rerank_if_enabled(queries[0], merged, top_k * 4)
        selected = _apply_diversity(merged, top_k)
        return _enrich_with_adjacent(selected, chroma_path)
    except Exception as e:
        logger.warning(f"RAG multi-query search error: {e}")
        return []


def search_multi_product(
    message: str,
    product_names: list[str],
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    """
    Поиск для запросов с несколькими продуктами.

    Для каждого имени продукта выполняется отдельный поиск — это гарантирует,
    что листок-вкладыш/СГР/декларация каждого продукта попадёт в выдачу,
    даже если общий запрос вытесняет его нормативными документами.

    Порядок приоритетов в merged:
      1. Продуктовые результаты (по очереди для каждого продукта).
      2. Нормативные результаты (полный текст запроса).

    Итоговый лимит масштабируется: top_k + len(product_names) * 3.
    """
    try:
        seen_content: set[str] = set()
        merged: list[dict] = []

        def _add_unique(items: list[dict]) -> None:
            for item in items:
                if item["content"] not in seen_content:
                    seen_content.add(item["content"])
                    merged.append(item)

        for name in product_names:
            _add_unique(_raw_search(name, mode, top_k * 2, chroma_path))

        _add_unique(_raw_search(message, mode, _candidate_pool_size(top_k), chroma_path))

        total_k = top_k + len(product_names) * 3
        merged = _rerank_if_enabled(message, merged, total_k * 2)
        selected = _apply_diversity(merged, total_k)
        return _enrich_with_adjacent(selected, chroma_path)
    except Exception as e:
        logger.warning(f"RAG multi-product search error: {e}")
        return []


def needs_reindex(chroma_path: str) -> bool:
    """True, если коллекция пуста ИЛИ построена другим эмбеддером.

    Смена rag_embed_model меняет размерность векторов — старая коллекция
    несовместима, её нужно дропнуть и переиндексировать.
    """
    try:
        collection = _get_collection(chroma_path)
        if collection.count() == 0:
            return True
        indexed_model = (collection.metadata or {}).get("embed_model", "")
        return indexed_model != settings.rag_embed_model
    except Exception:
        return True


def drop_collection(chroma_path: str) -> None:
    """Удаляет коллекцию ChromaDB целиком — нужно при смене схемы чанкинга,
    чтобы на следующей индексации применилась новая логика."""
    global _collection
    import chromadb
    _client_local = chromadb.PersistentClient(path=chroma_path)
    try:
        _client_local.delete_collection(COLLECTION_NAME)
        logger.info("Коллекция RAG удалена — потребуется переиндексация")
    except Exception as e:
        logger.warning(f"Не удалось удалить коллекцию: {e}")
    _collection = None

    if settings.rag_bm25_enabled:
        try:
            bm25_index.reset_for_path(chroma_path)
            logger.info("BM25-индекс удалён")
        except Exception as e:
            logger.warning(f"Не удалось удалить BM25-индекс: {e}")


async def async_index_document(
    doc_id: str,
    text: str,
    mode: str,
    category: str,
    original_filename: str,
    chroma_path: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, index_document, doc_id, text, mode, category, original_filename, chroma_path
    )


async def async_delete_document(doc_id: str, chroma_path: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, delete_document, doc_id, chroma_path)


async def async_drop_collection(chroma_path: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, drop_collection, chroma_path)


async def async_search(query: str, mode: str, top_k: int, chroma_path: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search, query, mode, top_k, chroma_path)


async def async_search_dual(
    primary_query: str,
    secondary_query: str,
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, search_dual, primary_query, secondary_query, mode, top_k, chroma_path
    )


async def async_search_multi_product(
    message: str,
    product_names: list[str],
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, search_multi_product, message, product_names, mode, top_k, chroma_path
    )


async def async_search_multi_query(
    queries: list[str],
    mode: str,
    top_k: int,
    chroma_path: str,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, search_multi_query, queries, mode, top_k, chroma_path
    )


def followup_by_references(
    initial_docs: list[dict],
    mode: str,
    chroma_path: str,
    max_followups: int = 3,
    docs_per_followup: int = 2,
    scan_top_n: int = 5,
) -> list[dict]:
    """Multi-hop через перекрёстные ссылки.

    Берёт top-N чанков из initial_docs, вытаскивает regex-ом ссылки
    («ст. 24 ФЗ-38», «п. 4.2 Положения о договорной работе»), и по каждой
    ссылке делает мини-поиск. Возвращает НОВЫЕ чанки, которых не было
    в initial (дедуп по content).

    Реализует идею перекрёстных ссылок из LightRAG без построения графа:
    юридические ссылки имеют жёсткий синтаксис, regex покрывает
    большинство случаев без LLM-вызова.
    """
    if not initial_docs:
        return []
    try:
        from reference_extractor import extract_legal_references
    except Exception:
        return []

    seen_refs: set[str] = set()
    seen_content: set[str] = set(d.get("content", "") for d in initial_docs)
    refs_to_follow: list[str] = []
    for d in initial_docs[:scan_top_n]:
        for ref in extract_legal_references(d.get("content", "")):
            key = ref.lower()
            if key in seen_refs:
                continue
            seen_refs.add(key)
            refs_to_follow.append(ref)
            if len(refs_to_follow) >= max_followups:
                break
        if len(refs_to_follow) >= max_followups:
            break

    if not refs_to_follow:
        return []

    extra: list[dict] = []
    for ref in refs_to_follow:
        try:
            candidates = _raw_search(ref, mode, docs_per_followup * 4, chroma_path)
        except Exception as e:
            logger.debug("[FOLLOWUP] _raw_search для %r failed: %s", ref, e)
            continue
        added = 0
        for c in candidates:
            content = c.get("content", "")
            if not content or content in seen_content:
                continue
            seen_content.add(content)
            extra.append(c)
            added += 1
            if added >= docs_per_followup:
                break

    if extra:
        logger.info(
            "[FOLLOWUP] mode=%s refs=%d → %d новых чанков",
            mode, len(refs_to_follow), len(extra),
        )
    return extra


async def async_followup_by_references(
    initial_docs: list[dict],
    mode: str,
    chroma_path: str,
    max_followups: int = 3,
    docs_per_followup: int = 2,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        followup_by_references,
        initial_docs, mode, chroma_path, max_followups, docs_per_followup,
    )
