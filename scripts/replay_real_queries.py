"""Replay тест: прогон 100 случайных реальных запросов через Telethon с замером
времени ответа и анализом самых медленных + outliers по тематике.

Источник: tests/scenarios/real_queries_all_days.json (654 уникальных запросов
из логов прода за 5 дней).

Цель:
1. Найти самые медленные запросы (top-10 по latency).
2. Гистограмма времени по категориям (consistency vs unique).
3. Сохранить отчёт в docs/replay_real_queries_<date>.md.

Запуск:
    export TG_API_ID=12345
    export TG_API_HASH=abc...
    python3.11 scripts/replay_real_queries.py [--n 100] [--seed 42] [--delay 5]

Защита от FloodWait — пауза 5 сек между запросами.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from datetime import date
from pathlib import Path

try:
    from telethon import TelegramClient
except ImportError:
    print("ERROR: pip install telethon", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).parent.parent
QUERIES_FP = ROOT / "tests/scenarios/real_queries_all_days.json"
SESSION_FP = ROOT / "data/agent_tester"

API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
TARGET = os.environ.get("TG_TARGET", "Euroopt_gpt_bot")


async def query_with_timing(client, text: str, timeout: int = 35) -> tuple[str | None, float]:
    """Отправить запрос и засечь сколько времени до первого ответа бота."""
    sent = await client.send_message(TARGET, text)
    t0 = time.perf_counter()
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        async for msg in client.iter_messages(TARGET, min_id=sent.id, limit=3):
            if msg.out:
                continue
            elapsed = time.perf_counter() - t0
            return msg.text or "<empty>", elapsed
    return None, time.perf_counter() - t0


async def main(args):
    if API_ID == 0 or not API_HASH:
        print("ERROR: TG_API_ID / TG_API_HASH env not set", file=sys.stderr)
        return 2

    # Загрузка пула запросов
    pool = json.load(open(QUERIES_FP, encoding="utf-8"))
    rng = random.Random(args.seed)
    sample = rng.sample(pool, min(args.n, len(pool)))

    client = TelegramClient(str(SESSION_FP), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT_AUTHORIZED")
        return 1

    print(f"=== Replay {len(sample)} реальных запросов ===")
    results: list[dict] = []
    for i, item in enumerate(sample, 1):
        q = item["query"][:200].strip()
        if not q:
            continue
        try:
            ans, elapsed = await query_with_timing(client, q, timeout=args.timeout)
        except Exception as e:
            ans = f"ERROR: {e}"
            elapsed = -1.0
        ans_text = ans or "<TIMEOUT>"
        truncated = ans_text[:200].replace("\n", " ")
        print(f"[{i}/{len(sample)}] {elapsed:5.2f}s | {q[:60]:<60} | {truncated[:80]}", flush=True)
        results.append({
            "query": q,
            "category": item.get("category", "?"),
            "elapsed": elapsed,
            "answer_len": len(ans_text),
            "answer_preview": truncated[:300],
            "is_refusal": "не вижу в моей базе" in ans_text.lower()
                          or "к сожалению, у меня нет" in ans_text.lower()
                          or "эту тему я не обсуждаю" in ans_text.lower(),
            "is_pii_block": "не могу обрабатывать персональные данные" in ans_text.lower(),
        })
        await asyncio.sleep(args.delay)

    await client.disconnect()

    # ==================== Анализ ====================
    valid = [r for r in results if r["elapsed"] >= 0]
    if not valid:
        print("Нет валидных результатов")
        return 1

    times = [r["elapsed"] for r in valid]
    print(f"\n=== Статистика ({len(valid)} удачных) ===")
    print(f"  min: {min(times):.2f}s | median: {statistics.median(times):.2f}s | "
          f"p95: {sorted(times)[int(len(times)*0.95)]:.2f}s | max: {max(times):.2f}s")
    print(f"  refusals: {sum(1 for r in valid if r['is_refusal'])} ({sum(1 for r in valid if r['is_refusal']) * 100 / len(valid):.1f}%)")
    print(f"  pii blocks: {sum(1 for r in valid if r['is_pii_block'])} ({sum(1 for r in valid if r['is_pii_block']) * 100 / len(valid):.1f}%)")

    top_slow = sorted(valid, key=lambda r: r["elapsed"], reverse=True)[:10]
    print("\n=== TOP-10 самых медленных ===")
    for r in top_slow:
        print(f"  {r['elapsed']:5.2f}s | [{r['category']}] {r['query'][:80]}")

    # Отчёт в Markdown
    out_fp = ROOT / f"docs/replay_real_queries_{date.today().isoformat()}.md"
    with open(out_fp, "w", encoding="utf-8") as f:
        f.write(f"# Replay {len(valid)} реальных запросов на проде — {date.today().isoformat()}\n\n")
        f.write(f"Источник: `tests/scenarios/real_queries_all_days.json` (seed={args.seed})\n\n")
        f.write("## Статистика времени ответа\n\n")
        f.write(f"- min:   **{min(times):.2f} sec**\n")
        f.write(f"- median: **{statistics.median(times):.2f} sec**\n")
        f.write(f"- p95:   **{sorted(times)[int(len(times)*0.95)]:.2f} sec**\n")
        f.write(f"- max:   **{max(times):.2f} sec**\n\n")
        f.write(f"- refusals: {sum(1 for r in valid if r['is_refusal'])} ({sum(1 for r in valid if r['is_refusal']) * 100 / len(valid):.1f}%)\n")
        f.write(f"- pii blocks: {sum(1 for r in valid if r['is_pii_block'])} ({sum(1 for r in valid if r['is_pii_block']) * 100 / len(valid):.1f}%)\n\n")

        f.write("## TOP-10 самых медленных запросов\n\n")
        f.write("| # | Время | Категория | Запрос | Превью ответа |\n")
        f.write("|---|---|---|---|---|\n")
        for i, r in enumerate(top_slow, 1):
            q = r["query"][:80].replace("|", "/")
            ans = r["answer_preview"][:120].replace("|", "/")
            f.write(f"| {i} | {r['elapsed']:.2f}s | {r['category']} | {q} | {ans} |\n")

        f.write("\n## Refusals (не нашёл ответ)\n\n")
        refusals = [r for r in valid if r["is_refusal"]]
        if refusals:
            f.write(f"Всего {len(refusals)} запросов получили refusal:\n\n")
            for r in refusals[:20]:
                f.write(f"- `{r['query'][:100]}` ({r['elapsed']:.2f}s)\n")
        else:
            f.write("_Нет refusal'ов — все запросы получили содержательный ответ._\n")

        f.write("\n## Все запросы\n\n")
        f.write("| # | Время | Категория | Запрос |\n")
        f.write("|---|---|---|---|\n")
        for i, r in enumerate(valid, 1):
            q = r["query"][:80].replace("|", "/")
            f.write(f"| {i} | {r['elapsed']:.2f}s | {r['category']} | {q} |\n")

    print(f"\nSAVED {out_fp.relative_to(ROOT)}")
    return 0


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=100, help="сколько запросов из 654")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--delay", type=float, default=5.0, help="пауза между запросами")
    p.add_argument("--timeout", type=int, default=35)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args)))


if __name__ == "__main__":
    cli()
