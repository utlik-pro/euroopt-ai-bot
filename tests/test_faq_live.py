"""Тест FAQ — все 16 вопросов Еплюс через LLM."""
import sys, asyncio, time, json
sys.path.insert(0, ".")
from src.pipeline import Pipeline

FAQ_QUERIES = [
    {"q": "Что такое бонусы и как их тратить?", "check": ["бонус", "списа", "покупк"]},
    {"q": "Как узнать мой баланс бонусов?", "check": ["баланс", "приложен", "личн"]},
    {"q": "Почему на карте стало меньше бонусов?", "check": ["списа", "сгоре", "покупк"]},
    {"q": "Как получить карту Еплюс?", "check": ["приложен", "виртуальн", "пластик"]},
    {"q": "Как сделать виртуальную карту?", "check": ["приложен", "скача", "виртуальн"]},
    {"q": "Виртуальная карта отличается от пластиковой?", "check": ["отлича", "функци", "одинаков"]},
    {"q": "Не могу войти в личный кабинет, что делать?", "check": ["пароль", "восстанов", "поддержк"]},
    {"q": "Забыл пароль, как быть?", "check": ["восстанов", "сброс", "пароль"]},
    {"q": "Как перенести карту в другой личный кабинет?", "check": ["перенес", "привяза", "кабинет"]},
    {"q": "Карта не работает, стёрлась, что делать?", "check": ["замен", "обрати", "магазин"]},
    {"q": "Я потерял карту, что делать?", "check": ["заблокир", "восстанов", "новую"]},
    {"q": "Можно разблокировать карту?", "check": ["разблокир", "обрати", "поддержк"]},
    {"q": "Где мои коды Удача в придачу?", "check": ["код", "удач", "приложен"]},
    {"q": "Почему мне начислили мало бонусов?", "check": ["бонус", "начисл", "категор"]},
    {"q": "Почему нет игровых кодов?", "check": ["код", "игров", "условия"]},
    {"q": "Можно ли списать бонусы на сигареты или алкоголь?", "check": ["нельзя", "запрещ", "алкогол", "табак", "сигарет"]},
]

async def main():
    p = Pipeline()
    passed = 0
    failed = 0
    results = []
    
    for i, faq in enumerate(FAQ_QUERIES, 1):
        start = time.monotonic()
        try:
            answer = await p.process(faq["q"], user_id=99999)
            ms = int((time.monotonic() - start) * 1000)
            ok = any(w in answer.lower() for w in faq["check"])
            status = "PASS" if ok else "FAIL"
            if ok: passed += 1
            else: failed += 1
            print(f"{'✅' if ok else '❌'} {i:2d}. {faq['q']}")
            if not ok:
                print(f"     Ответ: {answer[:200]}")
            results.append({"q": faq["q"], "passed": ok, "time_ms": ms, "answer": answer[:300]})
        except Exception as e:
            failed += 1
            print(f"💥 {i:2d}. {faq['q']} — ОШИБКА: {str(e)[:100]}")
            results.append({"q": faq["q"], "passed": False, "error": str(e)[:200]})
    
    print(f"\n===== FAQ ИТОГО: {passed}/{len(FAQ_QUERIES)} =====")
    
    with open("reports/test_faq.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

asyncio.run(main())
