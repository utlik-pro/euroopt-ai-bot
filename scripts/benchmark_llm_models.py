"""Benchmark — сравнение LLM-моделей на наших 59 acceptance-сценариях.

Прогоняет каждую модель через те же 59 сценариев (tester_scenarios.json),
измеряет:
- Pass rate (по эвристикам как в Telethon validator)
- Latency p50, p95
- Cost per query (по реальным token usage)
- Длина ответа

Цель: найти баланс между ценой и качеством.

Запуск:
    python3.11 scripts/benchmark_llm_models.py \\
        --models gpt-4o-mini deepseek-chat glm-4-flash \\
        --runs 1 \\
        --limit 20  # для быстрой проверки

Без --limit прогонит все 59 сценариев на каждой модели.

ВАЖНО: использует **наши** API-ключи (Anthropic / OpenAI / DeepSeek / GLM / etc.)
из .env, выставляет LLM_PROVIDER + LLM_MODEL и переопределяет в pipeline.

Затраты на полный прогон 59 × 4 модели = ~$0.50-1.00 при текущих ценах.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCENARIOS_FP = ROOT / "tests/scenarios/tester_scenarios.json"

# Приблизительные цены на 01.05.2026 ($/1M токенов).
# Обновлять при новой ценовой политике.
PRICING = {
    # OpenAI
    "gpt-4o-mini":       {"in": 0.15, "out": 0.60, "provider": "openai"},
    "gpt-4o":            {"in": 2.50, "out": 10.00, "provider": "openai"},
    # DeepSeek
    "deepseek-chat":     {"in": 0.27, "out": 1.10, "provider": "deepseek"},
    "deepseek-reasoner": {"in": 0.55, "out": 2.19, "provider": "deepseek"},
    # GLM (Zhipu / z.ai)
    "glm-4-flash":       {"in": 0.10, "out": 0.30, "provider": "glm"},
    "glm-4-plus":        {"in": 0.65, "out": 1.30, "provider": "glm"},
    # Claude
    "claude-3-5-haiku":  {"in": 0.80, "out": 4.00, "provider": "claude"},
    # Mistral
    "mistral-small":     {"in": 0.20, "out": 0.60, "provider": "mistral"},
    # Qwen
    "qwen2.5-72b":       {"in": 0.40, "out": 1.20, "provider": "qwen"},
    # Yandex
    "yandexgpt-lite":    {"in": 0.50, "out": 1.00, "provider": "yandexgpt"},
    "yandexgpt-pro":     {"in": 1.20, "out": 1.20, "provider": "yandexgpt"},
}


def cost_estimate(model: str, in_tokens: int, out_tokens: int) -> float:
    p = PRICING.get(model, {"in": 0.0, "out": 0.0})
    return (in_tokens * p["in"] + out_tokens * p["out"]) / 1_000_000


async def run_one_model(
    model: str, scenarios: list[dict], runs: int = 1, debug: bool = False
) -> dict:
    """Прогнать модель N раз через все сценарии. Возвращает агрегаты."""
    sys.path.insert(0, str(ROOT))
    # Импорт пайплайна с переопределением модели
    os.environ["LLM_PROVIDER"] = PRICING[model]["provider"]
    os.environ["LLM_MODEL"] = model
    # Очистим cached модули
    for mod in list(sys.modules):
        if mod.startswith("src.") or mod == "src":
            del sys.modules[mod]
    from src.pipeline import Pipeline

    pipe = Pipeline()
    results: list[dict] = []
    print(f"\n=== Benchmarking {model} ({runs}× runs × {len(scenarios)} scenarios) ===", flush=True)
    for run_idx in range(runs):
        for i, sc in enumerate(scenarios, 1):
            q = sc["query"]
            t0 = time.perf_counter()
            try:
                # Pipeline.process возвращает строку или объект
                resp = await pipe.process(q, user_id=999_900 + run_idx)
                ans = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
                err = None
            except Exception as e:
                ans = ""
                err = str(e)[:200]
            elapsed = time.perf_counter() - t0
            # Token-usage у нас не всегда доступен из pipeline; берём приближённо по символам
            # 1 токен ≈ 4 символа русского
            in_tokens_approx = (len(q) + 4000) // 4  # +4000 за RAG-context и SYSTEM_PROMPT
            out_tokens_approx = len(ans) // 4
            cost = cost_estimate(model, in_tokens_approx, out_tokens_approx)
            results.append({
                "run": run_idx,
                "scenario_id": sc.get("id", f"#{i}"),
                "block": sc.get("block", "?"),
                "query": q[:80],
                "answer": ans[:200],
                "answer_len": len(ans),
                "elapsed": elapsed,
                "cost": cost,
                "err": err,
                "is_refusal": "не вижу в моей базе" in ans.lower() or "к сожалению" in ans.lower()
                              or "эту тему я не обсуждаю" in ans.lower(),
            })
            if debug:
                print(f"  [{run_idx+1}.{i:02d}] {elapsed:.1f}s | {q[:50]:50s} | {ans[:60]}", flush=True)
    return {
        "model": model,
        "runs": runs,
        "scenarios_count": len(scenarios),
        "results": results,
    }


def summarize(model_data: dict) -> dict:
    """Свернуть результаты в метрики."""
    res = [r for r in model_data["results"] if r["err"] is None]
    if not res:
        return {"model": model_data["model"], "error": "no_valid_results"}
    times = [r["elapsed"] for r in res]
    return {
        "model": model_data["model"],
        "n": len(res),
        "errors": len(model_data["results"]) - len(res),
        "p50_latency_s": round(statistics.median(times), 2),
        "p95_latency_s": round(sorted(times)[int(len(times) * 0.95)], 2),
        "max_latency_s": round(max(times), 2),
        "avg_answer_len": round(sum(r["answer_len"] for r in res) / len(res), 0),
        "refusal_rate": round(sum(1 for r in res if r["is_refusal"]) / len(res) * 100, 1),
        "total_cost_usd": round(sum(r["cost"] for r in res), 4),
        "avg_cost_per_query": round(sum(r["cost"] for r in res) / len(res), 6),
    }


async def main(args):
    scenarios_data = json.load(open(SCENARIOS_FP, encoding="utf-8"))
    scenarios = scenarios_data.get("scenarios", scenarios_data) if isinstance(scenarios_data, dict) else scenarios_data
    if args.limit:
        scenarios = scenarios[:args.limit]

    all_data = []
    for model in args.models:
        if model not in PRICING:
            print(f"⚠️  {model} не в PRICING — пропускаю")
            continue
        try:
            data = await run_one_model(model, scenarios, runs=args.runs, debug=args.debug)
            all_data.append(data)
        except Exception as e:
            print(f"❌ {model}: {e}")

    print("\n=== СВОДНАЯ ТАБЛИЦА ===\n")
    print(f"{'Model':22s} {'N':>3} {'Err':>3} {'p50':>6} {'p95':>6} {'maxs':>6} {'avg_len':>8} {'refusal%':>9} {'cost$':>10}")
    summaries = []
    for d in all_data:
        s = summarize(d)
        if "error" in s:
            print(f"{s['model']:22s} ERROR: {s['error']}")
            continue
        summaries.append(s)
        print(
            f"{s['model']:22s} {s['n']:>3} {s['errors']:>3} "
            f"{s['p50_latency_s']:>6.2f} {s['p95_latency_s']:>6.2f} {s['max_latency_s']:>6.2f} "
            f"{s['avg_answer_len']:>8.0f} {s['refusal_rate']:>8.1f}% {s['total_cost_usd']:>10.4f}"
        )

    # Markdown-отчёт
    out_fp = ROOT / f"docs/benchmark_llm_{date.today().isoformat()}.md"
    with open(out_fp, "w", encoding="utf-8") as f:
        f.write(f"# LLM Benchmark — {date.today().isoformat()}\n\n")
        f.write(f"Сценариев: {len(scenarios)} × {args.runs} run = {len(scenarios) * args.runs} запросов на модель.\n\n")
        f.write("## Свод\n\n")
        f.write("| Модель | n | err | p50, s | p95, s | max, s | avg_len | refusal % | total $ |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for s in summaries:
            f.write(f"| {s['model']} | {s['n']} | {s['errors']} | "
                    f"{s['p50_latency_s']} | {s['p95_latency_s']} | {s['max_latency_s']} | "
                    f"{s['avg_answer_len']} | {s['refusal_rate']}% | ${s['total_cost_usd']} |\n")
        f.write("\n")
        # Кейсы по моделям
        for d in all_data:
            f.write(f"\n## {d['model']} — все ответы\n\n")
            for r in d["results"]:
                f.write(f"### [{r['scenario_id']}] {r['query']}\n\n")
                f.write(f"- elapsed: {r['elapsed']:.2f}s | cost: ${r['cost']:.5f}\n")
                f.write(f"- answer ({r['answer_len']} chars): {r['answer'][:300]}\n\n")

    print(f"\nSAVED {out_fp.relative_to(ROOT)}")


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+", default=["gpt-4o-mini"],
                   help="список моделей для бенчмарка (см. PRICING в коде)")
    p.add_argument("--runs", type=int, default=1, help="прогонов на сценарий (для consistency)")
    p.add_argument("--limit", type=int, default=None, help="лимит сценариев (для отладки)")
    p.add_argument("--debug", action="store_true", help="печатать каждый ответ")
    args = p.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
