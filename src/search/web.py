"""Web search через Tavily API.

Используется как fallback когда RAG не нашёл релевантных чанков
(или для получения актуальных данных с сайтов Евроторга).

Настройки (в .env):
  TAVILY_API_KEY          — ключ от tavily.com
  WEB_SEARCH_ENABLED      — true/false
  WEB_SEARCH_DOMAINS      — CSV список доменов (по умолчанию: evroopt.by,hitdiscount.by,groshyk.by,igra.evroopt.by)
  WEB_SEARCH_CACHE_TTL    — TTL кэша в секундах (по умолчанию 21600 = 6 часов)
  WEB_SEARCH_MAX_PER_DAY  — дневной лимит (по умолчанию 500)
"""
import os
import time
import hashlib
import threading
from typing import Optional

import structlog

logger = structlog.get_logger()


class WebSearchClient:
    """Обёртка над Tavily с кэшем, доменным фильтром и дневным лимитом."""

    def __init__(self):
        self._api_key = os.environ.get("TAVILY_API_KEY", "").strip()
        self._enabled = os.environ.get("WEB_SEARCH_ENABLED", "false").lower() == "true"
        self._domains = [
            d.strip() for d in os.environ.get(
                "WEB_SEARCH_DOMAINS",
                "evroopt.by,hitdiscount.by,groshyk.by,igra.evroopt.by,eplus.by,e-dostavka.by"
            ).split(",") if d.strip()
        ]
        self._cache_ttl = int(os.environ.get("WEB_SEARCH_CACHE_TTL", "21600"))
        self._max_per_day = int(os.environ.get("WEB_SEARCH_MAX_PER_DAY", "500"))

        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._daily_count = 0
        self._daily_reset = 0.0
        self._lock = threading.Lock()

        if self._enabled and not self._api_key:
            logger.warning("web_search_no_api_key")
            self._enabled = False
        elif self._enabled:
            try:
                from tavily import TavilyClient
                self._client = TavilyClient(api_key=self._api_key)
                logger.info("web_search_initialized", domains=self._domains, ttl=self._cache_ttl)
            except Exception as e:
                logger.error("web_search_init_error", error=str(e))
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _cache_key(self, query: str, domains: Optional[list[str]]) -> str:
        raw = f"{query}|{'|'.join(sorted(domains or []))}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _check_daily_limit(self) -> bool:
        now = time.time()
        if now - self._daily_reset > 86400:
            self._daily_reset = now
            self._daily_count = 0
        return self._daily_count < self._max_per_day

    def search(
        self,
        query: str,
        max_results: int = 5,
        domains: Optional[list[str]] = None,
        include_general: bool = False,
    ) -> list[dict]:
        """Выполнить поиск. Возвращает [{title, url, content, score}].

        Если `include_general=True` — ищет по всему интернету (без доменного фильтра).
        По умолчанию — только по брендовым доменам Евроторга.
        """
        if not self._enabled:
            return []
        if not self._check_daily_limit():
            logger.warning("web_search_daily_limit")
            return []

        actual_domains = domains if domains is not None else (None if include_general else self._domains)
        key = self._cache_key(query, actual_domains)

        # Кэш
        with self._lock:
            if key in self._cache:
                ts, res = self._cache[key]
                if time.time() - ts < self._cache_ttl:
                    logger.info("web_search_cache_hit", query=query[:50])
                    return res

        # Запрос
        try:
            kwargs = {"query": query, "max_results": max_results, "search_depth": "basic"}
            if actual_domains:
                kwargs["include_domains"] = actual_domains
            t0 = time.time()
            resp = self._client.search(**kwargs)
            dt = int((time.time() - t0) * 1000)

            results = []
            for r in resp.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                })

            with self._lock:
                self._cache[key] = (time.time(), results)
                self._daily_count += 1

            logger.info("web_search_done",
                        query=query[:50], results=len(results),
                        duration_ms=dt, daily_count=self._daily_count)
            return results
        except Exception as e:
            logger.error("web_search_error", query=query[:50], error=str(e))
            return []


# Singleton
_instance: Optional[WebSearchClient] = None


def get_web_search() -> WebSearchClient:
    global _instance
    if _instance is None:
        _instance = WebSearchClient()
    return _instance
