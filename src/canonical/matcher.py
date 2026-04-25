"""Canonical answers matcher.

Идея: критичные FAQ-вопросы (вход в ЛК, утеря карты, перенос бонусов, оплата
бонусами 99%/2 копейки и т.п.) должны иметь *гарантированно одинаковые*
ответы. LLM такой гарантии не даёт. Поэтому держим список «канонических»
формулировок в YAML, и если запрос пользователя достаточно близок к одному
из триггеров — отдаём готовый ответ, минуя LLM.

Используется в `Pipeline.process` ПЕРЕД RAG/LLM. Если совпадения нет —
работа идёт обычным путём.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
import structlog

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    fuzz = None  # type: ignore

logger = structlog.get_logger()

DEFAULT_MIN_SCORE = 0.85
DEFAULT_PATH = "data/canonical/faq_canonical.yaml"


@dataclass
class CanonicalAnswer:
    id: str
    category: str
    triggers: list[str]
    answer: str
    min_score: float = DEFAULT_MIN_SCORE


class CanonicalMatcher:
    """Fuzzy-match пользовательского запроса к каноническим триггерам.

    Алгоритм:
    1. Нормализуем запрос пользователя (lowercase, убираем пунктуацию).
    2. Для каждого канонического ответа проверяем все его триггеры через
       rapidfuzz.token_set_ratio — устойчиво к перестановке слов.
    3. Берём лучший match по всем ответам. Если score >= min_score — возврат.

    Без rapidfuzz деградирует до простого подстрочного матчинга — для тестов
    и dev-среды без зависимости.
    """

    def __init__(self, yaml_path: str | None = None):
        self.path = yaml_path or os.environ.get("CANONICAL_ANSWERS_PATH", DEFAULT_PATH)
        self.answers: list[CanonicalAnswer] = []
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            logger.warning("canonical_answers_not_found", path=str(p))
            return
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for raw in data.get("answers", []):
                self.answers.append(
                    CanonicalAnswer(
                        id=raw["id"],
                        category=raw.get("category", "general"),
                        triggers=raw.get("triggers", []),
                        answer=raw["answer"].strip(),
                        min_score=raw.get("min_score", DEFAULT_MIN_SCORE),
                    )
                )
            logger.info("canonical_answers_loaded", count=len(self.answers))
        except Exception as e:
            logger.error("canonical_answers_load_error", err=str(e), path=str(p))

    @staticmethod
    def _normalize(text: str) -> str:
        import re

        low = text.lower().strip()
        low = re.sub(r"[?!.,;:«»\"'()]", " ", low)
        low = re.sub(r"\s+", " ", low)
        return low.strip()

    def _score(self, query_norm: str, trigger: str) -> float:
        trigger_norm = self._normalize(trigger)
        if not _HAS_RAPIDFUZZ:
            # Fallback: простой подстрочный матч
            if trigger_norm in query_norm or query_norm in trigger_norm:
                return 1.0
            words = set(trigger_norm.split())
            qwords = set(query_norm.split())
            if not words:
                return 0.0
            overlap = len(words & qwords) / len(words)
            return overlap
        # token_set_ratio: устойчив к перестановке и лишним словам
        return fuzz.token_set_ratio(query_norm, trigger_norm) / 100.0

    def match(self, user_message: str) -> CanonicalAnswer | None:
        """Вернуть лучший канонический ответ или None."""
        if not self.answers:
            return None
        if not user_message or not user_message.strip():
            return None

        q_norm = self._normalize(user_message)

        best: tuple[float, CanonicalAnswer | None] = (0.0, None)
        for ans in self.answers:
            for trig in ans.triggers:
                s = self._score(q_norm, trig)
                if s > best[0]:
                    best = (s, ans)

        score, hit = best
        if hit and score >= hit.min_score:
            logger.info(
                "canonical_match",
                id=hit.id,
                category=hit.category,
                score=round(score, 3),
                query=user_message[:80],
            )
            return hit

        if hit:
            logger.debug(
                "canonical_no_match",
                best_id=hit.id,
                best_score=round(score, 3),
                threshold=hit.min_score,
                query=user_message[:80],
            )
        return None

    def stats(self) -> dict:
        return {
            "total_answers": len(self.answers),
            "categories": sorted({a.category for a in self.answers}),
            "rapidfuzz_available": _HAS_RAPIDFUZZ,
        }
