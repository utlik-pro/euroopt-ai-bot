FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download embedding model (MiniLM — меньше памяти для Render 2GB)
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# App code
COPY src/ ./src/
COPY data/ ./data/
COPY scripts/ ./scripts/

# Create dirs
RUN mkdir -p logs reports data/chroma persist/logs

# Pre-build Chroma at image build (избегаем OOM на Render runtime)
ENV CHROMA_PERSIST_DIR=/app/data/chroma
ENV EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV PYTHONUNBUFFERED=1
RUN python scripts/reindex_v2.py || echo "reindex at build finished"

CMD ["python", "-m", "src.bot.main"]
