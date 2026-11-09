"""
BM25-индекс с русской лемматизацией поверх тех же чанков, что и dense-индекс ChromaDB.

Используется как лексическая составляющая гибридного ретривинга: dense-поиск
через MiniLM плох на редких токенах (бренды «Примерин», «Образецил», номера
статей «ст. 14.3 КоАП», erid). BM25 с лемматизацией pymorphy3 закрывает этот
пробел и сливается с dense через Reciprocal Rank Fusion в rag.py.

Хранилище: pickle-файл рядом с Chroma. Лемматизация кешируется в памяти
процесса (на крупных файлах ~3M токенов кеш экономит ~95% вызовов морфоанализа).
Перестроение BM25Okapi ленивое — после add/delete индекс помечается грязным
и пересобирается из сохранённых токенов на следующий запрос.
"""

import re
import pickle
import logging
import threading
from pathlib import Path

logger = logging.getLogger("bm25")

INDEX_FILENAME = "bm25_index.pkl"
_TOKEN_RE = re.compile(r"[а-яёa-z0-9]{2,}", re.IGNORECASE)

_morph = None
_lemma_cache: dict[str, str] = {}
_lemma_lock = threading.Lock()


def _get_morph():
    global _morph
    if _morph is None:
        import pymorphy3
        logger.info("Загрузка морфоанализатора pymorphy3...")
        _morph = pymorphy3.MorphAnalyzer()
        logger.info("Морфоанализатор загружен")
    return _morph


def _lemmatize_token(tok: str) -> str:
    cached = _lemma_cache.get(tok)
    if cached is not None:
        return cached
    try:
        lemma = _get_morph().parse(tok)[0].normal_form
    except Exception:
        lemma = tok
    with _lemma_lock:
        _lemma_cache[tok] = lemma
    return lemma


def tokenize(text: str) -> list[str]:
    """Токенизирует и лемматизирует текст для BM25.

    Кириллические/латинские слова длиной ≥2 символов, lowercase, лемма через
    pymorphy3. Для латинских и числовых токенов pymorphy3 возвращает их как есть —
    что и нужно (бренды, номера статей, erid сохраняют точную форму).
    """
    raw = _TOKEN_RE.findall(text.lower())
    return [_lemmatize_token(t) for t in raw]


class BM25Index:
    """Персистентный BM25-индекс с инкрементальным обновлением.

    Состояние:
      chunks:  {chunk_id: {"content", "metadata", "tokens"}}
      bm25:    BM25Okapi (rebuild-on-demand)
      ids:     [chunk_id, ...]  — порядок параллелен корпусу BM25Okapi
    """

    def __init__(self, dir_path: str):
        self.path = Path(dir_path) / INDEX_FILENAME
        self.chunks: dict[str, dict] = {}
        self._bm25 = None
        self._ids: list[str] = []
        self._dirty = False
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "rb") as f:
                data = pickle.load(f)
            self.chunks = data.get("chunks", {})
            logger.info(f"BM25-индекс загружен: {len(self.chunks)} чанков")
        except Exception as e:
            logger.warning(f"Не удалось загрузить BM25-индекс ({e}); индекс будет создан заново")
            self.chunks = {}

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".pkl.tmp")
        with open(tmp, "wb") as f:
            pickle.dump({"chunks": self.chunks}, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(self.path)
        self._dirty = False
        logger.info(f"BM25-индекс сохранён: {len(self.chunks)} чанков")

    def _rebuild(self) -> None:
        if not self.chunks:
            self._bm25 = None
            self._ids = []
            return
        from rank_bm25 import BM25Okapi
        ids = list(self.chunks.keys())
        corpus = [self.chunks[i]["tokens"] for i in ids]
        self._bm25 = BM25Okapi(corpus)
        self._ids = ids

    def _ensure_built(self) -> None:
        if self._bm25 is None and self.chunks:
            self._rebuild()

    def add_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """items: [(chunk_id, content, metadata), ...]"""
        if not items:
            return
        with self._lock:
            for cid, content, metadata in items:
                self.chunks[cid] = {
                    "content": content,
                    "metadata": metadata,
                    "tokens": tokenize(content),
                }
            self._bm25 = None  # пометить на пересборку
            self._dirty = True

    def delete_by_doc_id(self, doc_id: str) -> int:
        with self._lock:
            to_remove = [
                cid for cid, ch in self.chunks.items()
                if ch["metadata"].get("doc_id") == doc_id
            ]
            for cid in to_remove:
                del self.chunks[cid]
            if to_remove:
                self._bm25 = None
                self._dirty = True
            return len(to_remove)

    def clear(self) -> None:
        with self._lock:
            self.chunks = {}
            self._bm25 = None
            self._ids = []
            self._dirty = True

    def search(self, query: str, n: int) -> list[tuple[str, float]]:
        """Возвращает top-n [(chunk_id, score)] с положительными скорами."""
        with self._lock:
            self._ensure_built()
            if self._bm25 is None:
                return []
            q_tokens = tokenize(query)
            if not q_tokens:
                return []
            scores = self._bm25.get_scores(q_tokens)
            top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
            return [(self._ids[i], float(scores[i])) for i in top if scores[i] > 0]

    def get(self, chunk_id: str) -> dict | None:
        return self.chunks.get(chunk_id)

    def count(self) -> int:
        return len(self.chunks)


# Singleton по chroma_path — соответствует поведению ChromaDB-клиента в rag.py
_indexes: dict[str, BM25Index] = {}
_indexes_lock = threading.Lock()


def get_index(chroma_path: str) -> BM25Index:
    with _indexes_lock:
        idx = _indexes.get(chroma_path)
        if idx is None:
            idx = BM25Index(chroma_path)
            _indexes[chroma_path] = idx
        return idx


def reset_for_path(chroma_path: str) -> None:
    """Полный сброс — удаляет файл индекса и singleton."""
    with _indexes_lock:
        idx = _indexes.pop(chroma_path, None)
    path = Path(chroma_path) / INDEX_FILENAME
    if path.exists():
        path.unlink()
    if idx is not None:
        idx.clear()
