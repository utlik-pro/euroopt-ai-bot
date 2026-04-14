"""Hybrid RAG: ChromaDB (e5 embeddings) + BM25 keyword search.

- e5 модель добавляет префиксы 'query:' / 'passage:' для лучшего матчинга
- BM25 параллельно ищет по ключевым словам — спасает короткие FAQ-запросы
- Финальный score = 0.6 * embedding + 0.4 * BM25 (нормализованные)
"""
import re
import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
import structlog

from rank_bm25 import BM25Okapi

from src.config import settings

logger = structlog.get_logger()


def _tokenize(text: str) -> list[str]:
    """Простая токенизация для русского: lowercase + слова."""
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


class E5Embedding(EmbeddingFunction):
    """E5-multilingual эмбеддинги с правильными префиксами.

    intfloat/multilingual-e5-base — 280 МБ, заметно лучше MiniLM
    на коротких русских запросах и парафразах.
    """

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(settings.embedding_model)
        self._is_e5 = "e5" in settings.embedding_model.lower()
        logger.info("embedding_model_loaded", model=settings.embedding_model, e5_mode=self._is_e5)

    def _prefix(self, text: str, kind: str) -> str:
        if not self._is_e5:
            return text
        return f"{kind}: {text}"

    def __call__(self, input: Documents) -> Embeddings:
        # ChromaDB вызывает это и для запросов, и для документов одинаково.
        # Применяем префикс passage: — для запросов RAGEngine.search использует отдельный embed_query.
        prefixed = [self._prefix(t, "passage") for t in input]
        emb = self.model.encode(prefixed, show_progress_bar=False, normalize_embeddings=True)
        return emb.tolist()

    def embed_query(self, text: str) -> list[float]:
        prefixed = self._prefix(text, "query")
        emb = self.model.encode([prefixed], show_progress_bar=False, normalize_embeddings=True)
        return emb[0].tolist()


class RAGEngine:
    """Hybrid RAG: e5 embeddings + BM25 keyword search."""

    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._embedding_fn = E5Embedding()
        self.collection = self.client.get_or_create_collection(
            name="euroopt_knowledge_v3",  # новая коллекция под e5
            metadata={"hnsw:space": "cosine"},
            embedding_function=self._embedding_fn,
        )
        self._bm25 = None
        self._bm25_ids: list[str] = []
        self._bm25_texts: list[str] = []
        self._bm25_metas: list[dict] = []
        logger.info("rag_engine_initialized", persist_dir=settings.chroma_persist_dir)

    def _build_bm25(self) -> None:
        all_data = self.collection.get()
        ids = all_data.get("ids") or []
        if not ids:
            self._bm25 = None
            return
        self._bm25_ids = ids
        self._bm25_texts = all_data.get("documents") or []
        self._bm25_metas = all_data.get("metadatas") or [{} for _ in ids]
        tokenized = [_tokenize(t) for t in self._bm25_texts]
        self._bm25 = BM25Okapi(tokenized)
        logger.info("bm25_index_built", docs=len(ids))

    def add_documents(self, documents: list[dict]):
        if not documents:
            return
        ids = [doc["id"] for doc in documents]
        texts = [doc["text"] for doc in documents]
        metadatas = [doc.get("metadata", {}) for doc in documents]
        self.collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
        # Сбрасываем BM25 — нужно перестроить
        self._bm25 = None
        logger.info("documents_added", count=len(documents))

    def _search_embedding(self, query: str, n: int) -> list[dict]:
        # Используем embed_query чтобы получить query-prefix для e5
        try:
            qemb = self._embedding_fn.embed_query(query)
            results = self.collection.query(query_embeddings=[qemb], n_results=n)
        except Exception:
            results = self.collection.query(query_texts=[query], n_results=n)

        docs = []
        if results["documents"] and results["documents"][0]:
            for i, text in enumerate(results["documents"][0]):
                score = 1 - results["distances"][0][i] if results.get("distances") else 0
                docs.append({
                    "id": results["ids"][0][i] if results.get("ids") else f"emb_{i}",
                    "text": text,
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    "emb_score": score,
                    "bm25_score": 0.0,
                })
        return docs

    def _search_bm25(self, query: str, n: int) -> list[dict]:
        if self._bm25 is None:
            self._build_bm25()
        if self._bm25 is None or not self._bm25_ids:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        max_s = max(scores) if len(scores) else 0
        if max_s <= 0:
            return []
        # топ-N по BM25
        idx_sorted = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
        out = []
        for i in idx_sorted:
            if scores[i] <= 0:
                continue
            out.append({
                "id": self._bm25_ids[i],
                "text": self._bm25_texts[i],
                "metadata": self._bm25_metas[i] or {},
                "emb_score": 0.0,
                "bm25_score": scores[i] / max_s,  # нормализуем 0..1
            })
        return out

    def search(self, query: str, n_results: int | None = None, category: str | None = None) -> list[dict]:
        n = n_results or settings.rag_top_k

        # Берём с запасом из обоих источников
        emb_hits = self._search_embedding(query, n=max(n * 2, 8))
        bm25_hits = self._search_bm25(query, n=max(n * 2, 8))

        # Объединяем по id
        merged: dict[str, dict] = {}
        for h in emb_hits + bm25_hits:
            existing = merged.get(h["id"])
            if existing:
                existing["emb_score"] = max(existing["emb_score"], h["emb_score"])
                existing["bm25_score"] = max(existing["bm25_score"], h["bm25_score"])
            else:
                merged[h["id"]] = dict(h)

        # Финальный score: 0.6 * emb + 0.4 * bm25
        for h in merged.values():
            h["score"] = 0.6 * h["emb_score"] + 0.4 * h["bm25_score"]

        ranked = sorted(merged.values(), key=lambda x: -x["score"])

        # Категория-фильтр
        if category:
            ranked = [h for h in ranked if (h.get("metadata") or {}).get("category") == category]

        # Порог
        thr = settings.rag_score_threshold
        result = [h for h in ranked if h["score"] >= thr][:n]

        logger.info("rag_search",
                    query=query[:50],
                    emb_hits=len(emb_hits),
                    bm25_hits=len(bm25_hits),
                    after_threshold=len(result))
        return result

    def get_stats(self) -> dict:
        return {"total_documents": self.collection.count()}
