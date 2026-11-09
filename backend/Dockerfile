FROM python:3.12-slim

WORKDIR /app

ENV HF_HOME=/app/.cache/huggingface

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    libgl1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .

# Install CPU-only torch before sentence-transformers to avoid CUDA packages (~2GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка dense-эмбеддера bge-m3 (~2.3 ГБ) — нужно из-за TRANSFORMERS_OFFLINE=1
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

# Pre-download cross-encoder reranker (~568 MB) — нужно из-за TRANSFORMERS_OFFLINE=1
# в runtime. Если выключить реранкер — слой кешируется, образ не пересобирается.
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=512)"

# Pre-warm pymorphy3 — словари тянутся как python-пакет, но прогреем кеш загрузки
RUN python -c "import pymorphy3; pymorphy3.MorphAnalyzer().parse('тест')"

# Парсеры старого бинарного .doc (Word 97-2003). Отдельным слоем после тяжёлых
# ML-загрузок, чтобы их пересборка не сбрасывала кеш моделей.
RUN apt-get update && apt-get install -y --no-install-recommends antiword catdoc \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# (#13) Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app/data/skills /app/data/documents /app/data/chroma \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
