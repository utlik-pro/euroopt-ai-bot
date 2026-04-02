"""Структурированное логирование для ежедневных отчётов и аналитики.

Логирует:
- Все запросы пользователей и ответы бота
- Стоимость LLM-запросов (токены → деньги)
- Срабатывания контент-фильтра
- RAG-результаты (что нашли, релевантность)
- Ошибки и время ответа
"""

import json
import time
from datetime import datetime, date
from pathlib import Path
from dataclasses import dataclass, field, asdict

import structlog

logger = structlog.get_logger()

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


# Стоимость за 1K input/output токенов (BYN, приблизительно)
TOKEN_COSTS = {
    "glm-4-flash": {"input": 0.00001, "output": 0.00001},
    "glm-4": {"input": 0.00007, "output": 0.00007},
    "glm-4-plus": {"input": 0.00010, "output": 0.00010},
    "gemini-2.0-flash": {"input": 0.00011, "output": 0.00011},
    "gpt-4o-mini": {"input": 0.00017, "output": 0.00017},
    "deepseek-chat": {"input": 0.00030, "output": 0.00030},
    "deepseek-reasoner": {"input": 0.00061, "output": 0.00061},
    "claude-3-5-haiku-20241022": {"input": 0.00104, "output": 0.00104},
    "gemini-1.5-pro": {"input": 0.00138, "output": 0.00138},
    "claude-sonnet-4-20250514": {"input": 0.00390, "output": 0.00390},
}


@dataclass
class RequestLog:
    timestamp: str = ""
    user_id: int = 0
    user_message: str = ""
    bot_response: str = ""
    # Pipeline
    content_filtered: bool = False
    filter_reason: str = ""
    rag_results_count: int = 0
    rag_top_score: float = 0.0
    promotions_shown: int = 0
    # LLM
    llm_provider: str = ""
    llm_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_byn: float = 0.0
    # Performance
    response_time_ms: int = 0
    error: str = ""


class InteractionLogger:
    """Логирует все взаимодействия в JSONL-файлы (один файл на день)."""

    def _get_log_file(self) -> Path:
        today = date.today().isoformat()
        return LOGS_DIR / f"interactions_{today}.jsonl"

    def log_request(self, log: RequestLog):
        log.timestamp = datetime.now().isoformat()
        log.cost_byn = self._calc_cost(log.llm_model, log.input_tokens, log.output_tokens)

        filepath = self._get_log_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(log), ensure_ascii=False) + "\n")

        logger.info(
            "request_logged",
            user_id=log.user_id,
            model=log.llm_model,
            tokens=log.input_tokens + log.output_tokens,
            cost_byn=f"{log.cost_byn:.6f}",
            response_time_ms=log.response_time_ms,
            filtered=log.content_filtered,
        )

    def _calc_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        costs = TOKEN_COSTS.get(model, {"input": 0.002, "output": 0.002})
        return (input_tokens / 1000 * costs["input"]) + (output_tokens / 1000 * costs["output"])

    def get_daily_stats(self, target_date: date | None = None) -> dict:
        """Статистика за день для ежедневного отчёта."""
        d = target_date or date.today()
        filepath = LOGS_DIR / f"interactions_{d.isoformat()}.jsonl"

        if not filepath.exists():
            return {"date": d.isoformat(), "total_requests": 0}

        logs = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line))

        if not logs:
            return {"date": d.isoformat(), "total_requests": 0}

        total = len(logs)
        filtered = sum(1 for l in logs if l.get("content_filtered"))
        errors = sum(1 for l in logs if l.get("error"))
        total_cost = sum(l.get("cost_byn", 0) for l in logs)
        avg_response = sum(l.get("response_time_ms", 0) for l in logs) / total if total else 0
        total_tokens = sum(l.get("input_tokens", 0) + l.get("output_tokens", 0) for l in logs)

        # Уникальные пользователи
        unique_users = len(set(l.get("user_id", 0) for l in logs))

        # Модели использованные
        models_used = {}
        for l in logs:
            m = l.get("llm_model", "unknown")
            models_used[m] = models_used.get(m, 0) + 1

        return {
            "date": d.isoformat(),
            "total_requests": total,
            "unique_users": unique_users,
            "content_filtered": filtered,
            "errors": errors,
            "total_cost_byn": round(total_cost, 4),
            "total_tokens": total_tokens,
            "avg_response_time_ms": round(avg_response),
            "models_used": models_used,
        }


# Singleton
interaction_logger = InteractionLogger()
