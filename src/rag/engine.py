import chromadb
import structlog

from src.config import settings

logger = structlog.get_logger()


class RAGEngine:
    """RAG-движок на ChromaDB.

    Использует дефолтную embedding модель ChromaDB (all-MiniLM-L6-v2).
    Для лучшего качества на русском в Фазе 2 переключить на
    мультиязычную модель (paraphrase-multilingual-MiniLM-L12-v2).
    """

    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
        )
        self.collection = self.client.get_or_create_collection(
            name="euroopt_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("rag_engine_initialized", persist_dir=settings.chroma_persist_dir)

    def add_documents(self, documents: list[dict]):
        """Add documents to the knowledge base.

        Each document should have: id, text, metadata (category, source, etc.)
        """
        if not documents:
            return

        ids = [doc["id"] for doc in documents]
        texts = [doc["text"] for doc in documents]
        metadatas = [doc.get("metadata", {}) for doc in documents]

        self.collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
        logger.info("documents_added", count=len(documents))

    def search(self, query: str, n_results: int | None = None, category: str | None = None) -> list[dict]:
        """Search the knowledge base.

        Returns list of {text, metadata, score} sorted by relevance.
        """
        n = n_results or settings.rag_top_k
        where = {"category": category} if category else None

        results = self.collection.query(
            query_texts=[query],
            n_results=n,
            where=where,
        )

        docs = []
        if results["documents"] and results["documents"][0]:
            for i, text in enumerate(results["documents"][0]):
                score = 1 - results["distances"][0][i] if results["distances"] else 0
                if score >= settings.rag_score_threshold:
                    docs.append({
                        "text": text,
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "score": score,
                    })

        logger.info("rag_search", query=query[:50], results_count=len(docs))
        return docs

    def get_stats(self) -> dict:
        return {"total_documents": self.collection.count()}
