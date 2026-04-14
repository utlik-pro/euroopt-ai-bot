"""Бенчмарк всех LLM-моделей из расчёта стоимости Евроторга."""
import sys, json, time, asyncio
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from openai import AsyncOpenAI
from src.config import settings
from src.rag.engine import RAGEngine
from src.promotions.engine import PromotionEngine
from src.llm.prompts import SYSTEM_PROMPT

MODELS = {
    "DeepSeek V3": "deepseek/deepseek-chat",
    "DeepSeek R1": "deepseek/deepseek-r1",
    "GPT-4o-mini": "openai/gpt-4o-mini",
    "Claude Haiku 3.5": "anthropic/claude-3.5-haiku",
    "Gemini 2.0 Flash": "google/gemini-2.0-flash-001",
    "Claude Sonnet 4": "anthropic/claude-sonnet-4",
    "Qwen Plus": "qwen/qwen-plus",
}

QUERIES = [
    {"id": "promo", "name": "Акции в ответе", "query": "Что приготовить на ужин?",
     "check_words": ["акци", "скидк", "byn", "руб", "цен"]},
    {"id": "faq", "name": "FAQ Еплюс", "query": "Как получить карту Еплюс?",
     "check_words": ["приложен", "виртуальн", "бонус", "карт"]},
    {"id": "recipe", "name": "Рецепт", "query": "Рецепт борща",
     "check_words": ["свёкл", "свекл", "капуст", "картоф", "ингредиент"]},
    {"id": "store", "name": "Магазин", "query": "Где ближайший Евроопт в Минске?",
     "check_words": ["минск", "адрес", "ул.", "пр-т", "независимости"]},
    {"id": "general", "name": "Текущие акции", "query": "Какие сейчас акции?",
     "check_words": ["акци", "скидк", "byn", "руб"]},
]

async def test_model(client, model_name, model_id, rag, promos):
    results = {"model": model_name, "model_id": model_id, "tests": [], "passed": 0, "failed": 0, "errors": 0, "avg_time_ms": 0, "total_tokens": 0}
    times = []
    for q in QUERIES:
        t = {"id": q["id"], "name": q["name"], "query": q["query"]}
        rag_results = rag.search(q["query"], n_results=5)
        context = "\n\n".join([r["text"] for r in rag_results]) if rag_results else "Нет данных."
        rel_promos = promos.get_relevant_promotions(q["query"])
        if not rel_promos:
            rel_promos = promos.get_top_promotions(limit=3)
        promos_text = promos.format_promotions(rel_promos)
        system = SYSTEM_PROMPT.format(context=context, promotions=promos_text)
        start = time.monotonic()
        try:
            resp = await client.chat.completions.create(model=model_id, max_tokens=1024, temperature=0.3,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": q["query"]}])
            ms = int((time.monotonic() - start) * 1000)
            times.append(ms)
            answer = resp.choices[0].message.content or ""
            tokens = (resp.usage.prompt_tokens + resp.usage.completion_tokens) if resp.usage else 0
            results["total_tokens"] += tokens
            passed = any(w in answer.lower() for w in q["check_words"])
            t.update({"answer": answer[:500], "passed": passed, "time_ms": ms, "tokens": tokens})
            if passed: results["passed"] += 1
            else: results["failed"] += 1
        except Exception as e:
            ms = int((time.monotonic() - start) * 1000)
            t.update({"error": str(e)[:200], "passed": False, "time_ms": ms})
            results["errors"] += 1
        results["tests"].append(t)
    results["avg_time_ms"] = int(sum(times) / len(times)) if times else 0
    return results

async def main():
    print("=" * 70)
    print("БЕНЧМАРК LLM-МОДЕЛЕЙ — AI-помощник Евроопт")
    print(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Моделей: {len(MODELS)}, тестов на модель: {len(QUERIES)}")
    print("=" * 70)
    client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
    rag = RAGEngine()
    promos = PromotionEngine()
    print(f"RAG: {rag.get_stats()['total_documents']} документов")
    print(f"Акции: {len(promos.promotions)} активных\n")
    all_results = []
    for model_name, model_id in MODELS.items():
        print(f"\n--- {model_name} ({model_id}) ---")
        result = await test_model(client, model_name, model_id, rag, promos)
        all_results.append(result)
        for t in result["tests"]:
            s = "✅" if t.get("passed") else "❌"
            if t.get("error"): s = "💥"
            print(f"  {s} {t['name']}: {t.get('time_ms', 0)}ms")
        print(f"  >> {result['passed']}/{len(QUERIES)} passed, {result['avg_time_ms']}ms avg")
    print("\n" + "=" * 70)
    print(f"{'Модель':<20} {'Pass':>5} {'Fail':>5} {'Err':>5} {'Avg ms':>8} {'Tokens':>8}")
    print("-" * 70)
    for r in all_results:
        print(f"{r['model']:<20} {r['passed']:>5} {r['failed']:>5} {r['errors']:>5} {r['avg_time_ms']:>8} {r['total_tokens']:>8}")
    out = Path("reports/benchmark_models.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nJSON: {out}")
    rpt = Path("reports/benchmark_models.txt")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write(f"БЕНЧМАРК LLM — AI-помощник Евроопт\nДата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*70}\n\n")
        for r in all_results:
            f.write(f"## {r['model']} ({r['model_id']})\nPassed: {r['passed']}/{len(QUERIES)}, Avg: {r['avg_time_ms']}ms, Tokens: {r['total_tokens']}\n\n")
            for t in r["tests"]:
                s = "PASS" if t.get("passed") else "FAIL"
                if t.get("error"): s = "ERROR"
                f.write(f"  [{s}] {t['name']}: {t['query']}\n")
                if t.get("answer"): f.write(f"  Ответ: {t['answer'][:300]}\n\n")
                if t.get("error"): f.write(f"  Ошибка: {t['error']}\n\n")
    print(f"Отчёт: {rpt}")

if __name__ == "__main__":
    asyncio.run(main())
