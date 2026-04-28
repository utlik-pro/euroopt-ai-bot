"""Пост-деплой проверка: подтверждаем что Фаза 1 v2 реально активна.

Парсит свежие логи Render через CLI и ищет признаки:
1. embedding_model_loaded — должно быть e5 (e5_mode=True)
2. canonical_answer_served — должны срабатывать на FAQ-запросах
3. intent_classified — intent router работает
4. bot_started — worker поднялся

Запуск:
    PYTHONPATH=. python3.11 scripts/render_post_deploy_check.py [--service-id srv-...]

Возвращает 0 если все три признака найдены, иначе 1.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone


def fetch_logs(service_id: str, limit: int = 200, since_minutes: int = 10) -> str:
    """Через render CLI получить последние N логов за последние M минут."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=since_minutes)
    cmd = [
        "render", "logs",
        "--resources", service_id,
        "--limit", str(limit),
        "--start", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--end", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--output", "text",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return out.stdout
    except Exception as e:
        print(f"❌ render CLI failed: {e}", file=sys.stderr)
        return ""


def analyze(text: str) -> dict:
    """Поиск ключевых маркеров v2 Фазы 1 в логах."""
    markers = {
        "bot_started": False,
        "rag_engine_initialized": False,
        "embedding_model_loaded": None,  # value: model name
        "e5_mode": None,
        "canonical_match": 0,
        "canonical_answer_served": 0,
        "intent_classified": 0,
        "errors": [],
    }

    if not text:
        return markers

    # bot_started
    markers["bot_started"] = "bot_started" in text

    # RAG init
    markers["rag_engine_initialized"] = "rag_engine_initialized" in text

    # Embedding model
    m = re.search(r"embedding_model_loaded\s+e5_mode=(\w+)\s+model=(\S+)", text)
    if m:
        markers["e5_mode"] = m.group(1)
        markers["embedding_model_loaded"] = m.group(2)

    # canonical
    markers["canonical_match"] = len(re.findall(r"canonical_match", text))
    markers["canonical_answer_served"] = len(re.findall(r"canonical_answer_served", text))

    # intent
    markers["intent_classified"] = len(re.findall(r"intent_classified", text))

    # ошибки
    for line in text.splitlines():
        if re.search(r"(ERROR|Traceback|Exception|fatal|panic)", line, re.IGNORECASE):
            # Игнорируем известный шум от chromadb posthog telemetry
            if "posthog" in line.lower() or "chromadb.telemetry" in line:
                continue
            markers["errors"].append(line[:200])

    return markers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-id", default="srv-d7fkju67r5hc739bktfg")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--since-min", type=int, default=10)
    args = ap.parse_args()

    print(f"=== Post-deploy check: {args.service_id} ===\n")
    text = fetch_logs(args.service_id, limit=args.limit, since_minutes=args.since_min)
    if not text:
        print("❌ Логи пустые — не могу проверить")
        return 1

    m = analyze(text)

    # Печатаем результаты
    icon = lambda ok: "✅" if ok else "❌"

    print(f"{icon(m['bot_started'])} bot_started: {m['bot_started']}")
    print(f"{icon(m['rag_engine_initialized'])} rag_engine_initialized: {m['rag_engine_initialized']}")
    print(f"{icon(m['e5_mode'] == 'True')} embedding_model: {m['embedding_model_loaded']} (e5_mode={m['e5_mode']})")
    print(f"{icon(m['intent_classified'] > 0)} intent_classified: {m['intent_classified']} раз(а)")
    print(f"{icon(m['canonical_match'] > 0)} canonical_match: {m['canonical_match']} раз(а)")
    print(f"{icon(m['canonical_answer_served'] > 0)} canonical_answer_served: {m['canonical_answer_served']} раз(а)")

    if m["errors"]:
        print(f"\n⚠ Ошибки в логах ({len(m['errors'])}):")
        for err in m["errors"][:5]:
            print(f"  {err}")

    # Решение: всё ли ОК
    critical = [
        m["bot_started"],
        m["rag_engine_initialized"],
        m["e5_mode"] == "True",
    ]
    if all(critical):
        print("\n🟢 Базовая инфраструктура v2 поднята. Worker слушает Telegram.")
        if m["intent_classified"] == 0:
            print("ℹ Intent router пока не сработал — отправьте боту любое сообщение для проверки.")
        return 0
    else:
        print("\n❌ Не все критичные маркеры найдены — проверьте Render dashboard")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
