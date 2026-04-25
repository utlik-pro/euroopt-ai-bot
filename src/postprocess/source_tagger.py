"""Source tagger: помечает откуда пришёл ответ — из базы Евроопта или
из общего интернета.

ЗАКАЗЧИК (отчёт 24.04 P3): «часть ответов может восприниматься как рецепт
из базы Евроопта, хотя это общий внешний вариант; в отдельных ответах
нужна большая прозрачность источника».

Логика:
- Если в RAG нашёлся документ-рецепт с высоким score — ответ помечается
  как `INTERNAL` («📋 Из базы Евроопта»).
- Если RAG-рецептов нет, но был web-search — `EXTERNAL` («🌐 Общий
  вариант рецепта из интернета»).
- Если ни того ни другого — `NONE` (тэг не добавляется).

Префикс приклеивается к началу ответа отдельным абзацем, чтобы
пользователь сразу видел источник.

Применяется ТОЛЬКО для intent=RECIPES (детектируется в Pipeline).
Для остальных интентов (FAQ, Stores, Promo) у нас и так достаточно
строгие источники, маркер был бы избыточен.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger()


class ResponseSource(str, Enum):
    INTERNAL = "internal"  # из RAG-базы Евроопта (рецепты, FAQ)
    EXTERNAL = "external"  # из web-поиска (общий интернет)
    MIXED = "mixed"  # частично оттуда, частично оттуда
    NONE = "none"  # источника не было — общий ответ LLM


@dataclass
class TagResult:
    source: ResponseSource
    text: str  # ответ с префиксом источника


# Минимальный score RAG-документа, чтобы считать его «источником» рецепта.
# Ниже — это просто похожий по словам, не настоящий рецепт.
MIN_RECIPE_RAG_SCORE = 0.55

# Категории документов, относящихся к рецептам в нашей RAG.
RECIPE_CATEGORIES = frozenset(["recipe"])


class SourceTagger:
    """Определяет источник ответа и добавляет визуальный маркер."""

    PREFIX_INTERNAL = "📋 *Рецепт из базы Евроопта.*"
    PREFIX_EXTERNAL = "🌐 *Общий вариант рецепта из интернета.*"
    PREFIX_MIXED = "📋 *Из базы Евроопта (с дополнением из интернета).*"

    def __init__(
        self,
        min_recipe_score: float = MIN_RECIPE_RAG_SCORE,
        recipe_categories: frozenset[str] = RECIPE_CATEGORIES,
    ):
        self.min_recipe_score = min_recipe_score
        self.recipe_categories = recipe_categories

    def detect_source(
        self,
        rag_results: list[dict],
        web_results: list[dict],
    ) -> ResponseSource:
        """Определить, откуда у LLM был контекст для рецепта."""
        has_internal = any(
            (h.get("metadata") or {}).get("category") in self.recipe_categories
            and h.get("score", 0.0) >= self.min_recipe_score
            for h in rag_results or []
        )
        has_external = bool(web_results)

        if has_internal and has_external:
            return ResponseSource.MIXED
        if has_internal:
            return ResponseSource.INTERNAL
        if has_external:
            return ResponseSource.EXTERNAL
        return ResponseSource.NONE

    def tag(
        self,
        response_text: str,
        rag_results: list[dict] | None,
        web_results: list[dict] | None,
    ) -> TagResult:
        """Добавить маркер источника к ответу.

        Возвращает TagResult; если источник NONE — текст не меняется.
        """
        if not response_text:
            return TagResult(source=ResponseSource.NONE, text=response_text)

        source = self.detect_source(rag_results or [], web_results or [])

        if source == ResponseSource.NONE:
            return TagResult(source=source, text=response_text)

        # Если ответ уже содержит наши префиксы — не дублируем
        for prefix in (self.PREFIX_INTERNAL, self.PREFIX_EXTERNAL, self.PREFIX_MIXED):
            if response_text.lstrip().startswith(prefix):
                return TagResult(source=source, text=response_text)

        prefix = {
            ResponseSource.INTERNAL: self.PREFIX_INTERNAL,
            ResponseSource.EXTERNAL: self.PREFIX_EXTERNAL,
            ResponseSource.MIXED: self.PREFIX_MIXED,
        }[source]

        tagged = f"{prefix}\n\n{response_text}"
        logger.info("source_tagged", source=source.value)
        return TagResult(source=source, text=tagged)
