"""
Cross-encoder реранкер поверх гибридного (dense+BM25) ретривинга.

Зачем:
  Dense и BM25 ранжируют чанки по приближённой релевантности (косинус векторов
  и BM25-скор). Cross-encoder смотрит на пару (запрос, чанк) совместно через
  трансформер и даёт куда более точную оценку — в обмен на стоимость
  N forward-passes на запрос.

Архитектура:
  1. _raw_search возвращает rag_rerank_candidates штук (~50) после RRF.
  2. Cross-encoder скорит все пары (query, chunk_content).
  3. Сортировка по cross-encoder скору, top top_k*4 идёт дальше в diversity.

Модель:
  По умолчанию BAAI/bge-reranker-v2-m3 — multilingual, ~568 МБ. Поддерживает
  длинный контекст (до 8192 токенов), что важно для секционных префиксов
  и обогащённых чанков.

Ленивая загрузка: модель грузится при первом вызове, не на старте.
"""

import logging
import threading

logger = logging.getLogger("reranker")

_model = None
_model_name: str | None = None
_lock = threading.Lock()


def _get_model(model_name: str, max_length: int):
    """Lazy singleton CrossEncoder."""
    global _model, _model_name
    if _model is not None and _model_name == model_name:
        return _model
    with _lock:
        if _model is not None and _model_name == model_name:
            return _model
        from sentence_transformers import CrossEncoder
        logger.info(f"Загрузка cross-encoder реранкера {model_name}...")
        _model = CrossEncoder(model_name, max_length=max_length)
        _model_name = model_name
        logger.info("Реранкер загружен")
    return _model


def rerank(
    query: str,
    candidates: list[dict],
    model_name: str,
    max_length: int,
    top_n: int,
) -> list[dict]:
    """Пересортировывает кандидаты по cross-encoder релевантности к запросу.

    candidates: list[dict] с обязательным ключом 'content' (текст чанка).
    Возвращает top_n кандидатов в порядке убывания скора.
    При ошибке/пустом запросе — fallback на исходный порядок (срез top_n).
    """
    if not candidates or not query.strip():
        return candidates[:top_n]

    try:
        model = _get_model(model_name, max_length)
        pairs = [(query, c["content"]) for c in candidates]
        scores = model.predict(pairs, show_progress_bar=False, batch_size=16)
        # zip + sort стабилен; преобразуем scores в float (могут быть np.float32)
        ranked = sorted(
            zip(candidates, (float(s) for s in scores)),
            key=lambda x: x[1],
            reverse=True,
        )
        return [c for c, _ in ranked[:top_n]]
    except Exception as e:
        logger.warning(f"Реранкер недоступен ({e}); возвращаем исходный порядок")
        return candidates[:top_n]
