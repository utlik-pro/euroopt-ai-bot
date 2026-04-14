"""Бенчмарк всех LLM через Atlas Cloud — все 9 моделей из расчёта + бонусные."""
import sys, json, time, asyncio
from datetime import datetime
from pathlib import Path
sys.path.insert(0, ".")
from openai import AsyncOpenAI
from src.rag.engine import RAGEngine
from src.promotions.engine import PromotionEngine
from src.llm.prompts import SYSTEM_PROMPT

ATLAS_KEY = "apikey-309ab8dc4f8d45479746d3f7b01e8a5a"
ATLAS_URL = "https://api.atlascloud.ai/v1"

MODELS = {
    # Бюджетные
    "GLM-4.6": "zai-org/GLM-4.6",
    "GLM-5-Turbo": "zai-org/glm-5-turbo",
    "Gemini 2.0 Flash": "google/gemini-2.0-flash",
    "GPT-4o-mini": "openai/gpt-4o-mini",
    "DeepSeek V3.2": "deepseek-ai/deepseek-v3.2",
    # Средние
    "DeepSeek R1": "deepseek-ai/deepseek-r1-0528",
    "Claude Haiku 4.5": "anthropic/claude-haiku-4.5-20251001",
    "Gemini 2.5 Pro": "google/gemini-2.5-pro",
    # Премиум
    "Claude Sonnet 4": "anthropic/claude-sonnet-4-20250514",
    # Бонус
    "Qwen3 235B": "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "Kimi K2": "moonshotai/Kimi-K2-Instruct",
}

QUERIES = [
    {"id": "promo_dinner", "name": "Акции + ужин", "query": "Что приготовить на ужин?",
     "check": ["акци", "скидк", "byn", "руб", "цен"]},
    {"id": "faq_eplus", "name": "FAQ Еплюс", "query": "Как получить карту Еплюс?",
     "check": ["приложен", "виртуальн", "бонус", "карт"]},
    {"id": "recipe", "name": "Рецепт борща", "query": "Рецепт борща",
     "check": ["свёкл", "свекл", "капуст", "картоф", "ингредиент"]},
    {"id": "store", "name": "Магазин Минск", "query": "Где ближайший Евроопт в Минске?",
     "check": ["минск", "адрес", "ул.", "пр-т", "независимости", "магазин"]},
    {"id": "promos_list", "name": "Список акций", "query": "Какие сейчас акции?",
     "check": ["акци", "скидк", "byn", "руб"]},
]

async def test_model(client, name, mid, rag, promos):
    res = {"model": name, "model_id": mid, "tests": [], "passed": 0, "failed": 0, "errors": 0, "avg_time_ms": 0, "total_tokens": 0}
    times = []
    for q in QUERIES:
        t = {"id": q["id"], "name": q["name"], "query": q["query"]}
        rr = rag.search(q["query"], n_results=5)
        ctx = "\n\n".join([r["text"] for r in rr]) if rr else "Нет данных."
        rp = promos.get_relevant_promotions(q["query"])
        if not rp: rp = promos.get_top_promotions(limit=3)
        pt = promos.format_promotions(rp)
        system = SYSTEM_PROMPT.format(context=ctx, promotions=pt)
        start = time.monotonic()
        try:
            resp = await client.chat.completions.create(model=mid, max_tokens=1024, temperature=0.3,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": q["query"]}])
            ms = int((time.monotonic() - start) * 1000)
            times.append(ms)
            answer = resp.choices[0].message.content or ""
            tokens = (resp.usage.prompt_tokens + resp.usage.completion_tokens) if resp.usage else 0
            res["total_tokens"] += tokens
            ok = any(w in answer.lower() for w in q["check"])
            t.update({"answer": answer[:500], "passed": ok, "time_ms": ms, "tokens": tokens})
            if ok: res["passed"] += 1
            else: res["failed"] += 1
        except Exception as e:
            ms = int((time.monotonic() - start) * 1000)
            t.update({"error": str(e)[:200], "passed": False, "time_ms": ms})
            res["errors"] += 1
        res["tests"].append(t)
    res["avg_time_ms"] = int(sum(times) / len(times)) if times else 0
    return res

async def main():
    print("=" * 70)
    print("БЕНЧМАРК LLM — Atlas Cloud — AI-помощник Евроопт")
    print(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Моделей: {len(MODELS)}, тестов: {len(QUERIES)}")
    print("=" * 70)
    client = AsyncOpenAI(api_key=ATLAS_KEY, base_url=ATLAS_URL)
    rag = RAGEngine()
    promos = PromotionEngine()
    print(f"RAG: {rag.get_stats()['total_documents']} docs, Акции: {len(promos.promotions)}\n")
    all_res = []
    for name, mid in MODELS.items():
        print(f"\n--- {name} ({mid}) ---")
        r = await test_model(client, name, mid, rag, promos)
        all_res.append(r)
        for t in r["tests"]:
            s = "✅" if t.get("passed") else "❌"
            if t.get("error"): s = "💥"
            print(f"  {s} {t['name']}: {t.get('time_ms',0)}ms")
        print(f"  >> {r['passed']}/{len(QUERIES)}, {r['avg_time_ms']}ms avg")
    print("\n" + "=" * 70)
    print(f"{'Модель':<20} {'Pass':>5} {'Fail':>5} {'Err':>5} {'Avg ms':>8} {'Tokens':>8}")
    print("-" * 70)
    for r in all_res:
        print(f"{r['model']:<20} {r['passed']:>5} {r['failed']:>5} {r['errors']:>5} {r['avg_time_ms']:>8} {r['total_tokens']:>8}")
    out = Path("reports/benchmark_atlas.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_res, f, ensure_ascii=False, indent=2)
    rpt = Path("reports/benchmark_atlas.txt")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write(f"БЕНЧМАРК LLM — Atlas Cloud\nДата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*70}\n\n")
        f.write(f"{'Модель':<20} {'Pass':>5} {'Fail':>5} {'Err':>5} {'Avg ms':>8} {'Tokens':>8}\n")
        f.write("-" * 70 + "\n")
        for r in all_res:
            f.write(f"{r['model']:<20} {r['passed']:>5} {r['failed']:>5} {r['errors']:>5} {r['avg_time_ms']:>8} {r['total_tokens']:>8}\n")
        f.write("\n\nДетали:\n" + "="*70 + "\n")
        for r in all_res:
            f.write(f"\n## {r['model']} ({r['model_id']})\n")
            for t in r["tests"]:
                s = "PASS" if t.get("passed") else "FAIL"
                if t.get("error"): s = "ERROR"
                f.write(f"  [{s}] {t['name']}: {t['query']}\n")
                if t.get("answer"): f.write(f"  {t['answer'][:250]}\n\n")
                if t.get("error"): f.write(f"  ERR: {t['error']}\n\n")
    print(f"\nJSON: {out}\nОтчёт: {rpt}")

if __name__ == "__main__":
    asyncio.run(main())
