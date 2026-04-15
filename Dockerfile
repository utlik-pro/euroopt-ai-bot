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

# Create dirs (persistent volumes mount here on Render)
RUN mkdir -p logs reports data/chroma

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.bot.main"]
