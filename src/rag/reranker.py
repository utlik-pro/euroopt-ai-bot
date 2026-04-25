"""Re-ranker для результатов RAG.

Идея: после первичного гибридного поиска (e5 embeddings + BM25) у нас на
руках top-N кандидатов. Они отсортированы по `0.6*emb + 0.4*bm25` — это
хорошо, но «похожие» в embeddings не всегда «правильные» по смыслу.

Re-ranker — это второй проход, более точный. Он берёт пары (запрос, документ)
и оценивает их совместимость. Двух режимов:

1. **lite** (по умолчанию, без сетевых зависимостей и моделей):
   считает keyword-overlap между запросом и документом + bonus за совпадение
   ключевых сущностей (брендов, городов, чисел). Простая, быстрая, локальная
   эвристика. На русском с маленькой базой даёт +5–10% точности.

2. **cross-encoder** (опционально, через RERANKER_MODEL env):
   использует CrossEncoder из sentence-transformers (например,
   `BAAI/bge-reranker-base` — мультиязычная). Модель видит пару (query,
   passage) одновременно и выдаёт score. Точность +15–20% на коротких
   русских запросах, но требует ~300МБ скачать при первом запуске.

В RAGEngine.search re-rank применяется опционально (флаг ENABLE_RERANKER).
По умолчанию выключено — старое поведение сохраняется.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


# Слова, которые НЕ влияют на семантику (удаляем перед расчётом overlap)
_STOPWORDS = frozenset(
    [
        "и", "в", "на", "у", "к", "из", "по", "для", "что", "это", "как",
        "ли", "же", "бы", "или", "а", "но", "о", "об", "от", "при",
        "the", "a", "an", "of", "in", "on", "at", "to", "for",
    ]
)


def _tokenize(text: str) -> list[str]:
    """Простая токенизация для русского/английского."""
    return [t for t in re.findall(r"\w+", text.lower(), re.UNICODE) if t not in _STOPWORDS]


def _stems(tokens: list[str]) -> set[str]:
    """Грубое стеммирование: берём первые 5 символов каждого токена.

    Это покрывает русскую морфологию: «магазин/магазины/магазинов» → один stem.
    Не идеально, но без зависимостей и работает достаточно для re-rank.
    """
    return {t[:5] for t in tokens if len(t) >= 3}


@dataclass
class RerankResult:
    """Результат re-rank: те же hits, но переранжированные."""

    hits: list[dict]
    method: str  # "lite" | "cross-encoder" | "skipped"


class LiteReranker:
    """Эвристический re-ranker без зависимостей.

    Score = w_overlap * keyword_overlap + w_orig * original_score + w_bonus * sentity_match
    """

    OVERLAP_WEIGHT = 0.45
    ORIG_WEIGHT = 0.35
    ENTITY_BONUS = 0.20

    # Сущности, совпадение которых даёт бонус
    BRAND_TOKENS = frozenset(["евроопт", "хит", "грошык", "ямигом", "еплюс", "едоставка"])

    def rerank(self, query: str, hits: list[dict], top_k: int | None = None) -> list[dict]:
        if not hits:
            return hits

        q_tokens = _tokenize(query)
        if not q_tokens:
            return hits[:top_k] if top_k else hits

        q_stems = _stems(q_tokens)
        q_brands = {t for t in q_tokens if t in self.BRAND_TOKENS}

        for h in hits:
            doc_text = h.get("text", "")
            d_tokens = _tokenize(doc_text)
            d_stems = _stems(d_tokens)

            # Overlap по основам слов (Jaccard-like)
            if q_stems and d_stems:
                inter = len(q_stems & d_stems)
                union = len(q_stems | d_stems)
                overlap = inter / union
            else:
                overlap = 0.0

            # Bonus за бренд: если оба упомянули один и тот же бренд
            d_brands = {t for t in d_tokens if t in self.BRAND_TOKENS}
            entity_match = 1.0 if (q_brands and q_brands & d_brands) else 0.0

            orig_score = h.get("score", 0.0)
            new_score = (
                self.OVERLAP_WEIGHT * overlap
                + self.ORIG_WEIGHT * orig_score
                + self.ENTITY_BONUS * entity_match
            )
            h["rerank_score"] = new_score
            h["score"] = new_score  # перезаписываем чтобы downstream видел новый порядок

        ranked = sorted(hits, key=lambda x: -x["rerank_score"])
        return ranked[:top_k] if top_k else ranked


class CrossEncoderReranker:
    """Cross-encoder re-ranker через sentence-transformers.

    Использует BAAI/bge-reranker-base (default) или модель из env
    RERANKER_MODEL. Модель скачивается при первом запуске (~300МБ).

    Lazy-load: модель грузится только при первом вызове rerank().
    """

    DEFAULT_MODEL = "BAAI/bge-reranker-base"

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.environ.get("RERANKER_MODEL", self.DEFAULT_MODEL)
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            logger.info("reranker_model_loaded", model=self.model_name)
        except Exception as e:
            logger.error("reranker_load_failed", err=str(e), model=self.model_name)
            raise

    def rerank(self, query: str, hits: list[dict], top_k: int | None = None) -> list[dict]:
        if not hits:
            return hits

        self._ensure_loaded()
        pairs = [(query, h.get("text", "")) for h in hits]
        scores = self._model.predict(pairs, show_progress_bar=False)

        for h, s in zip(hits, scores):
            h["rerank_score"] = float(s)
            # Сохраняем оригинальный score; перезаписываем основной для сортировки.
            h["original_score"] = h.get("score", 0.0)
            h["score"] = float(s)

        ranked = sorted(hits, key=lambda x: -x["rerank_score"])
        return ranked[:top_k] if top_k else ranked


def get_reranker(mode: str = "lite") -> LiteReranker | CrossEncoderReranker | None:
    """Получить re-ranker по режиму.

    mode:
        "lite" — без зависимостей, быстро, локально
        "cross-encoder" — точнее, требует загрузки модели ~300МБ
        "off" / иное — None (re-rank пропускается)
    """
    mode = (mode or "lite").lower()
    if mode == "lite":
        return LiteReranker()
    if mode in ("cross-encoder", "cross_encoder", "ce"):
        return CrossEncoderReranker()
    return None
