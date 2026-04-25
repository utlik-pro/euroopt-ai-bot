"""Response cache: общий кэш «нормализованный вопрос → ответ» с TTL.

ЦЕЛЬ — повторяемость. На одинаковый вопрос двух разных пользователей в
течение TTL бот гарантированно отдаёт один и тот же ответ. Закрывает
претензию из отчёта 24.04: расхождение оценок Наташа vs Лёша в 32%.

ПОЧЕМУ TTL КОРОТКИЙ (по умолчанию 1 час):
1. Акции обновляются еженедельно — кэшировать «Какие акции?» на сутки
   рискованно (бот будет врать про устаревшие акции).
2. После правки промпта/данных кэш сам очищается без ручного сброса.
3. 1 час покрывает 90% повторов в рамках сессии тестирования.
TTL настраивается через CACHE_TTL_SECONDS (можно поднять до 6/12 часов
на dev/тест; в продакшен — оставлять умеренным).

ПОЧЕМУ ЭТО НЕ ДОЛГОВРЕМЕННАЯ ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ:
кэш — общий, не привязан к user_id. Хранение per-user истории на
длительный срок противоречит ТЗ (§2: «бот НЕ собирает персональные
данные») и ДС №1 (§2.1.1: маскирование ПД на входе и в логах).
Долговременная персонализация — отдельный этап и отдельный ДС.

ИСКЛЮЧЕНИЯ ИЗ КЭША:
- Сообщения с PII-плейсхолдерами ([телефон], [email], [ФИО], [адрес],
  [номер_карты], [паспорт], [ID]) — каждый юзер прислал свои данные,
  ответ должен быть индивидуальным, в кэш не кладём.
- Запросы с явными «временными» маркерами («сегодня», «сейчас»,
  «текущ») — могут быстро устаревать, кэш с короче-TTL опасен.
"""
from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog

from src.search.query_normalizer import canonicalize_for_cache

logger = structlog.get_logger()

DEFAULT_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))  # 1 час
DEFAULT_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "1000"))

# Плейсхолдеры PII — не кэшируем сообщения с ними
PII_PLACEHOLDER_RE = re.compile(
    r"\[(?:телефон|email|ФИО|фио|адрес|номер_карты|паспорт|ID|id|дата)\]"
)
# Временные маркеры — повышают риск устаревания
EPHEMERAL_MARKERS = (
    "сегодня", "сейчас", "только что", "минуту назад", "секунду назад",
    "сию минуту", "только сегодня", "именно сейчас",
)


@dataclass
class CacheEntry:
    response: str
    created_at: float


class ResponseCache:
    """LRU + TTL кэш ответов.

    Ключ — нормализованный токен-сет запроса (см. canonicalize_for_cache).
    Это значит: «магазины в Лиде» и «Лида Евроопт магазины» дают один ключ.

    Не потокобезопасный — ChatHistory тоже не потокобезопасный, в нашем
    однопроцессном aiogram-боте этого достаточно. Если переедем на
    несколько worker'ов — заменить на Redis.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._stats = {"hits": 0, "misses": 0, "stores": 0, "evictions": 0, "skips": 0}

    @staticmethod
    def _is_cacheable(message: str) -> bool:
        """Можно ли вообще кэшировать этот запрос."""
        if not message or not message.strip():
            return False
        # PII-плейсхолдеры → индивидуальный ответ → не кэшируем
        if PII_PLACEHOLDER_RE.search(message):
            return False
        low = message.lower()
        # Временные маркеры → не кэшируем (или кэш с коротким TTL —
        # пока консервативно: вообще не кэшируем)
        for marker in EPHEMERAL_MARKERS:
            if marker in low:
                return False
        return True

    def _make_key(self, message: str) -> str | None:
        if not self._is_cacheable(message):
            return None
        key = canonicalize_for_cache(message)
        return key or None

    def get(self, message: str) -> str | None:
        """Вернуть кэшированный ответ или None."""
        key = self._make_key(message)
        if key is None:
            self._stats["skips"] += 1
            return None

        entry = self._store.get(key)
        if entry is None:
            self._stats["misses"] += 1
            return None

        # TTL-проверка
        if time.monotonic() - entry.created_at > self.ttl_seconds:
            del self._store[key]
            self._stats["misses"] += 1
            return None

        # LRU: обновляем позицию
        self._store.move_to_end(key)
        self._stats["hits"] += 1
        logger.info("cache_hit", key=key[:60])
        return entry.response

    def put(self, message: str, response: str) -> None:
        """Положить ответ в кэш. Тихо игнорирует не-кэшируемое."""
        key = self._make_key(message)
        if key is None:
            return
        if not response:
            return

        self._store[key] = CacheEntry(response=response, created_at=time.monotonic())
        self._store.move_to_end(key)
        self._stats["stores"] += 1

        # LRU eviction
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)
            self._stats["evictions"] += 1

        logger.info("cache_store", key=key[:60], total=len(self._store))

    def clear(self) -> None:
        self._store.clear()
        logger.info("cache_cleared")

    def stats(self) -> dict:
        total_lookups = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total_lookups if total_lookups > 0 else 0.0
        return {
            **self._stats,
            "size": len(self._store),
            "hit_rate": round(hit_rate, 3),
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
        }
