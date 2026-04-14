"""Тест рецептов — проверяем что бот даёт рецепты с ингредиентами и подсвечивает акции."""
import sys, asyncio, time, json
sys.path.insert(0, ".")
from src.pipeline import Pipeline

RECIPE_QUERIES = [
    {"q": "Рецепт борща", "check": ["свёкл", "свекл", "капуст", "картоф", "морков"]},
    {"q": "Как приготовить омлет?", "check": ["яйц", "яиц", "молок", "сковород"]},
    {"q": "Рецепт шарлотки с яблоками", "check": ["яблок", "мук", "сахар", "яйц"]},
    {"q": "Что приготовить из курицы?", "check": ["курин", "куриц", "филе", "ножк"]},
    {"q": "Простой рецепт на ужин", "check": ["ингредиент", "минут", "приготов"]},
    {"q": "Как сварить гороховый суп?", "check": ["горох", "картоф", "варит"]},
    {"q": "Рецепт оладий на кефире", "check": ["кефир", "мук", "яйц", "оладь"]},
    {"q": "Что приготовить на завтрак?", "check": ["завтрак", "каш", "яйц", "омлет", "сырник", "блин"]},
    {"q": "Рецепт плова с курицей", "check": ["рис", "курин", "куриц", "морков", "лук"]},
    {"q": "Как сделать винегрет?", "check": ["свёкл", "свекл", "картоф", "огурц", "горош"]},
]

async def main():
    p = Pipeline()
    passed = 0
    failed = 0
    has_promo = 0
    has_edostavka = 0
    results = []

    for i, rq in enumerate(RECIPE_QUERIES, 1):
        start = time.monotonic()
        try:
            answer = await p.process(rq["q"], user_id=99998)
            ms = int((time.monotonic() - start) * 1000)
            al = answer.lower()
            
            ok = any(w in al for w in rq["check"])
            promo = any(w in al for w in ["акци", "скидк", "byn", "руб", "цен"])
            edost = any(w in al for w in ["e-доставк", "edostavka", "доставк"])
            
            if ok: passed += 1
            else: failed += 1
            if promo: has_promo += 1
            if edost: has_edostavka += 1
            
            status = "✅" if ok else "❌"
            promo_s = "🔥" if promo else "  "
            edost_s = "🔗" if edost else "  "
            print(f"{status} {promo_s} {edost_s} {i:2d}. {rq['q']} ({ms}ms)")
            if not ok:
                print(f"      Ответ: {answer[:200]}")
            results.append({"q": rq["q"], "passed": ok, "has_promo": promo, "has_edostavka": edost, "time_ms": ms, "answer": answer[:400]})
        except Exception as e:
            failed += 1
            print(f"💥       {i:2d}. {rq['q']} — {str(e)[:100]}")
            results.append({"q": rq["q"], "passed": False, "error": str(e)[:200]})

    print(f"\n===== РЕЦЕПТЫ =====")
    print(f"Качество ответов: {passed}/{len(RECIPE_QUERIES)}")
    print(f"С акциями:        {has_promo}/{len(RECIPE_QUERIES)}")
    print(f"Со ссылкой e-дост: {has_edostavka}/{len(RECIPE_QUERIES)}")
    
    with open("reports/test_recipes.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

asyncio.run(main())
