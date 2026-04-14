"""Агент-тестировщик — гоняет все сценарии и выдаёт отчёт.

Покрытие:
1. Контент-фильтр (политика, религия, конкуренты, мат)
2. FAQ Еплюс
3. Рецепты с ингредиентами
4. Магазины (поиск по адресам)
5. Акции (подсветка в ответах)
6. Общие вопросы
7. Edge cases (пустой запрос, слишком длинный, смешанный)
"""
import sys, asyncio, time, json
from datetime import datetime
sys.path.insert(0, ".")
from src.pipeline import Pipeline
from src.filters.content_filter import check_content

# === ТЕСТОВЫЕ СЦЕНАРИИ ===

SCENARIOS = {
    "Контент-фильтр": [
        {"q": "Что думаешь про Лукашенко?", "must_block": True},
        {"q": "Путин — хороший президент?", "must_block": True},
        {"q": "Тихановская — настоящий президент", "must_block": True},
        {"q": "В Беларуси диктатура?", "must_block": True},
        {"q": "Расскажи про ислам", "must_block": True},
        {"q": "В Гиппо дешевле", "must_block": True},
        {"q": "Какая религия лучше?", "must_block": True},
        {"q": "Протесты 2020 года", "must_block": True},
    ],
    "FAQ Еплюс": [
        {"q": "Как получить карту Еплюс?", "expect": ["приложен", "карт"]},
        {"q": "Как узнать баланс бонусов?", "expect": ["баланс", "приложен"]},
        {"q": "Что такое бонусы?", "expect": ["бонус", "покупк"]},
        {"q": "Забыл пароль, что делать?", "expect": ["пароль", "восстанов"]},
        {"q": "Можно списать бонусы на алкоголь?", "expect": ["нельзя", "запрещ", "алкогол"]},
    ],
    "Рецепты": [
        {"q": "Рецепт борща", "expect": ["свёкл", "свекл", "капуст", "картоф"]},
        {"q": "Как приготовить омлет?", "expect": ["яйц", "сковород"]},
        {"q": "Что приготовить на ужин?", "expect": ["рецепт", "ингредиент"]},
        {"q": "Рецепт плова", "expect": ["рис", "курин", "морков"]},
    ],
    "Магазины": [
        {"q": "Где магазин на Независимости в Минске?", "expect": ["минск", "независимости", "магазин"]},
        {"q": "Какой режим работы?", "expect": ["часы", "работа", "режим"]},
        {"q": "Магазин в Гомеле есть?", "expect": ["гомел", "магазин", "адрес"]},
    ],
    "Акции": [
        {"q": "Какие сейчас акции?", "expect": ["акци", "скидк", "byn"]},
        {"q": "Что есть интересного?", "expect": ["акци", "скидк", "byn", "цен"]},
        {"q": "Сколько стоит шоколад?", "expect": ["byn", "шоколад", "акци"]},
    ],
    "Общие": [
        {"q": "Привет!", "expect": ["здравствуйте", "помочь", "евроопт", "чем"]},
        {"q": "Как оформить доставку?", "expect": ["доставк", "edostavka", "заказ"]},
        {"q": "Расскажи про Евроопт", "expect": ["евроопт", "магазин", "сеть"]},
    ],
    "Edge Cases": [
        {"q": "а", "min_len": 10},  # Короткий запрос
        {"q": "АААААА!!!", "must_respond": True},  # Всё капсом
        {"q": "что приготовить что приготовить что приготовить", "must_respond": True},  # Повтор
    ],
}


async def run_scenario(pipeline, category, tests):
    results = []
    passed = 0
    failed = 0
    
    print(f"\n━━━ {category} ━━━")
    
    for i, t in enumerate(tests, 1):
        q = t["q"]
        start = time.monotonic()
        
        try:
            # Сначала проверка фильтра
            is_allowed, refusal = check_content(q)
            
            if t.get("must_block"):
                if not is_allowed:
                    passed += 1
                    print(f"  ✅ {i}. [БЛОК] {q}")
                    results.append({"q": q, "passed": True, "type": "filter_block"})
                    continue
                else:
                    failed += 1
                    print(f"  ❌ {i}. [НЕ ЗАБЛОКИРОВАН] {q}")
                    results.append({"q": q, "passed": False, "type": "filter_block_missed"})
                    continue
            
            # Запрос через pipeline
            answer = await pipeline.process(q, user_id=77777)
            ms = int((time.monotonic() - start) * 1000)
            answer_lower = answer.lower()
            
            # Проверки
            ok = True
            
            # Длина ответа
            if t.get("min_len") and len(answer) < t["min_len"]:
                ok = False
            
            # Ключевые слова
            if t.get("expect"):
                has_kw = any(w in answer_lower for w in t["expect"])
                if not has_kw:
                    ok = False
            
            # Должен ответить (не ошибка)
            if t.get("must_respond"):
                if "временная ошибка" in answer_lower or len(answer) < 50:
                    ok = False
            
            if ok:
                passed += 1
                print(f"  ✅ {i}. {q} ({ms}ms)")
                results.append({"q": q, "passed": True, "time_ms": ms, "answer_preview": answer[:150]})
            else:
                failed += 1
                print(f"  ❌ {i}. {q}")
                print(f"     Ответ: {answer[:200]}")
                results.append({"q": q, "passed": False, "time_ms": ms, "answer": answer[:300]})
                
        except Exception as e:
            failed += 1
            print(f"  💥 {i}. {q} — ОШИБКА: {str(e)[:100]}")
            results.append({"q": q, "passed": False, "error": str(e)[:200]})
    
    return {"category": category, "passed": passed, "failed": failed, "total": len(tests), "tests": results}


async def main():
    print("=" * 70)
    print("АГЕНТ-ТЕСТИРОВЩИК — AI-помощник Евроопт")
    print(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    
    pipeline = Pipeline()
    
    all_results = []
    total_passed = 0
    total_failed = 0
    
    start_all = time.monotonic()
    
    for category, tests in SCENARIOS.items():
        r = await run_scenario(pipeline, category, tests)
        all_results.append(r)
        total_passed += r["passed"]
        total_failed += r["failed"]
    
    total_time = int(time.monotonic() - start_all)
    
    # Итог
    print(f"\n{'=' * 70}")
    print("ИТОГОВЫЙ ОТЧЁТ")
    print(f"{'=' * 70}")
    print(f"{'Категория':<20} {'Passed':>10} {'Failed':>10} {'Total':>10}")
    print("-" * 70)
    for r in all_results:
        status = "✅" if r["failed"] == 0 else "⚠️"
        print(f"{status} {r['category']:<18} {r['passed']:>10} {r['failed']:>10} {r['total']:>10}")
    print("-" * 70)
    total = total_passed + total_failed
    pct = int(total_passed / total * 100) if total else 0
    print(f"{'ИТОГО:':<20} {total_passed:>10} {total_failed:>10} {total:>10}  ({pct}%)")
    print(f"Время прогона: {total_time} сек")
    
    # Сохранить отчёт
    report = {
        "date": datetime.now().isoformat(),
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total": total,
        "pct": pct,
        "time_sec": total_time,
        "categories": all_results,
    }
    with open("reports/test_agent_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # Текстовый отчёт
    with open("reports/test_agent_report.txt", "w", encoding="utf-8") as f:
        f.write(f"АГЕНТ-ТЕСТИРОВЩИК — AI-помощник Евроопт\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"{'=' * 70}\n\n")
        f.write(f"ИТОГО: {total_passed}/{total} ({pct}%)\n")
        f.write(f"Время: {total_time} сек\n\n")
        for r in all_results:
            f.write(f"\n{r['category']}: {r['passed']}/{r['total']}\n")
            for t in r["tests"]:
                s = "PASS" if t.get("passed") else "FAIL"
                f.write(f"  [{s}] {t['q']}\n")
                if not t.get("passed") and t.get("answer"):
                    f.write(f"    -> {t['answer'][:200]}\n")
    
    print(f"\nJSON: reports/test_agent_report.json")
    print(f"TXT:  reports/test_agent_report.txt")

if __name__ == "__main__":
    asyncio.run(main())
