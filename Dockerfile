FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download embedding model (e5-multilingual — заметно лучше MiniLM на
# русском, особенно на коротких FAQ-запросах. Используется RAGEngine с
# префиксами query:/passage:. ~280 МБ).
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-base')"

# App code
COPY src/ ./src/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

# Create dirs
RUN mkdir -p logs reports data/chroma persist/logs

# Pre-build Chroma at image build (избегаем OOM на Render runtime).
# Шаги:
# 1. Парсим xlsx-справочник «Список ТО ЕТ Хит с форматами.xlsx» → all_stores.json
#    (1040 магазинов с разметкой brand/format/city, в т.ч. 55 автолавок).
# 2. Reindex: грузим все источники в ChromaDB (e5-base embeddings).
ENV CHROMA_PERSIST_DIR=/app/data/chroma
ENV EMBEDDING_MODEL=intfloat/multilingual-e5-base
ENV PYTHONUNBUFFERED=1
RUN python scripts/parse_stores_xlsx.py || echo "stores xlsx parse skipped"
RUN python scripts/reindex_v2.py || echo "reindex at build finished"

CMD ["python", "-m", "src.bot.main"]
