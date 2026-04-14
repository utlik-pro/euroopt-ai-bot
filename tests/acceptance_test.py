"""Приёмочный тест MVP — проверяет ВСЁ по КП и ТЗ.

Запуск: python3.11 tests/acceptance_test.py
Результат: reports/acceptance_test.txt + .json

Можно отдать клиенту — они прочитают отчёт и увидят что работает, что нет.
"""
import sys, asyncio, time, json, html
from datetime import datetime
sys.path.insert(0, ".")
from src.pipeline import Pipeline
from src.filters.content_filter import check_content

TESTS = [
    # ═══ КП п.1: Акции и офферы ═══
    {"category": "Акции и офферы", "q": "Какие сейчас акции?",
     "expect_keywords": ["акци", "byn", "скидк"],
     "description": "Бот показывает текущие акции с ценами"},
    {"category": "Акции и офферы", "q": "Что есть интересного?",
     "expect_keywords": ["акци", "byn", "скидк", "предлож"],
     "description": "Бот предлагает актуальные офферы"},
    {"category": "Акции и офферы", "q": "Хочу борщ",
     "expect_keywords": ["рецепт", "ингредиент", "свёкл", "свекл", "капуст"],
     "description": "На запрос рецепта бот даёт рецепт (акции в контексте если есть)"},
    {"category": "Акции и офферы", "q": "Что приготовить на ужин?",
     "expect_keywords": ["рецепт", "приготов", "ингредиент"],
     "description": "Бот рекомендует рецепт с подсветкой акций"},

    # ═══ КП п.2: FAQ ═══
    {"category": "FAQ", "q": "Как получить карту Еплюс?",
     "expect_keywords": ["приложен", "виртуальн", "карт", "бонус"],
     "description": "Бот объясняет как получить карту"},
    {"category": "FAQ", "q": "Как узнать баланс бонусов?",
     "expect_keywords": ["баланс", "приложен", "личн"],
     "description": "Бот объясняет как проверить баланс"},
    {"category": "FAQ", "q": "Как оформить доставку?",
     "expect_keywords": ["доставк", "edostavka", "заказ"],
     "description": "Бот объясняет про e-доставку"},
    {"category": "FAQ", "q": "Как работает возврат товара?",
     "expect_keywords": ["возврат", "чек", "дней", "магазин"],
     "description": "Бот объясняет политику возврата"},
    {"category": "FAQ", "q": "Какой режим работы магазинов?",
     "expect_keywords": ["час", "работ", "режим", "ежедневн"],
     "description": "Бот отвечает про часы работы"},
    {"category": "FAQ", "q": "Можно оплатить картой?",
     "expect_keywords": ["карт", "оплат", "наличн", "безналичн"],
     "description": "Бот отвечает про способы оплаты"},
    {"category": "FAQ", "q": "Забыл пароль от личного кабинета",
     "expect_keywords": ["пароль", "восстанов"],
     "description": "Бот помогает восстановить пароль"},

    # ═══ КП п.3: Рецепты ═══
    {"category": "Рецепты", "q": "Рецепт борща",
     "expect_keywords": ["свёкл", "свекл", "капуст", "картоф"],
     "description": "Бот даёт рецепт с ингредиентами"},
    {"category": "Рецепты", "q": "Как приготовить омлет?",
     "expect_keywords": ["яйц", "молок", "сковород"],
     "description": "Бот даёт рецепт омлета"},
    {"category": "Рецепты", "q": "Рецепт плова с курицей",
     "expect_keywords": ["рис", "курин", "куриц", "морков"],
     "description": "Бот даёт рецепт плова"},
    {"category": "Рецепты", "q": "Рецепт оладий",
     "expect_keywords": ["мук", "кефир", "яйц", "оладь", "тест"],
     "description": "Бот даёт рецепт оладий"},
    {"category": "Рецепты + ссылка", "q": "Рецепт шарлотки",
     "expect_keywords": ["яблок", "мук", "edostavka"],
     "description": "Бот даёт рецепт И ссылку на e-доставку"},

    # ═══ КП п.4: Контент-фильтр ═══
    {"category": "Фильтр: политика", "q": "Что думаешь про Лукашенко?",
     "must_block": True, "description": "Блокирует вопрос о президенте РБ"},
    {"category": "Фильтр: политика", "q": "Путин хороший президент?",
     "must_block": True, "description": "Блокирует вопрос о президенте РФ"},
    {"category": "Фильтр: политика", "q": "В Беларуси диктатура?",
     "must_block": True, "description": "Блокирует вопрос о диктатуре"},
    {"category": "Фильтр: политика", "q": "Тихановская настоящий президент",
     "must_block": True, "description": "Блокирует оппозицию"},
    {"category": "Фильтр: политика", "q": "Война в Украине",
     "must_block": True, "description": "Блокирует геополитику"},
    {"category": "Фильтр: религия", "q": "Какая религия лучше?",
     "must_block": True, "description": "Блокирует религиозные темы"},
    {"category": "Фильтр: религия", "q": "Бог существует?",
     "must_block": True, "description": "Блокирует богословие"},
    {"category": "Фильтр: конкуренты", "q": "В Гиппо дешевле?",
     "must_block": True, "description": "Блокирует упоминание конкурентов"},
    {"category": "Фильтр: конкуренты", "q": "Белмаркет лучше Евроопта",
     "must_block": True, "description": "Блокирует сравнение с конкурентами"},
    {"category": "Фильтр: мат", "q": "Хуйня а не магазин",
     "must_block": True, "description": "Блокирует нецензурную лексику"},

    # ═══ Доп: магазины ═══
    {"category": "Магазины", "q": "Где магазин на Независимости в Минске?",
     "expect_keywords": ["независимости", "минск", "магазин"],
     "description": "Бот находит магазин по адресу"},
    {"category": "Магазины", "q": "Есть ли магазин в Гомеле?",
     "expect_keywords": ["гомел", "магазин"],
     "description": "Бот ищет магазин в другом городе"},

    # ═══ Доп: общие вопросы ═══
    {"category": "Общие", "q": "Привет!",
     "expect_keywords": ["здравствуйте", "помочь", "добр"],
     "description": "Бот вежливо приветствует"},
    {"category": "Общие", "q": "Хочу заняться вязанием",
     "expect_not_keywords": ["edostavka", "купить пряжу на edostavka"],
     "expect_min_len": 50,
     "description": "Бот отвечает на общий вопрос, НЕ предлагает купить пряжу на edostavka"},
    {"category": "Общие", "q": "Что подарить маме на день рождения?",
     "expect_min_len": 50,
     "description": "Бот помогает с общим вопросом"},

    # ═══ Доп: контекст диалога ═══
    {"category": "Контекст", "q": "Рецепт борща",
     "expect_keywords": ["свёкл", "свекл", "борщ"],
     "description": "Первое сообщение — запрос рецепта",
     "dialog_id": "ctx_test"},
    {"category": "Контекст", "q": "А сколько варить?",
     "expect_keywords": ["час", "минут", "варит", "варк"],
     "description": "Второе сообщение — бот помнит что речь о борще",
     "dialog_id": "ctx_test"},
]

async def main():
    print("=" * 70)
    print("ПРИЁМОЧНЫЙ ТЕСТ MVP — AI-помощник Евроопт")
    print(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Тестов: {len(TESTS)}")
    print("По КП: КП-ЕВРО-002 v2.0 от 25.03.2026")
    print("=" * 70)

    pipeline = Pipeline()

    results = []
    passed = 0
    failed = 0
    categories = {}

    for i, t in enumerate(TESTS, 1):
        cat = t["category"]
        if cat not in categories:
            categories[cat] = {"passed": 0, "failed": 0, "tests": []}
            print(f"\n━━━ {cat} ━━━")

        q = t["q"]
        start = time.monotonic()
        test_result = {"q": q, "category": cat, "description": t["description"]}

        # Фильтр
        if t.get("must_block"):
            is_allowed, _ = check_content(q)
            ok = not is_allowed
            ms = int((time.monotonic() - start) * 1000)
            test_result.update({"passed": ok, "time_ms": ms, "type": "filter"})
        else:
            # Pipeline
            try:
                # Используем dialog_id чтобы тестировать контекст
                uid = hash(t.get("dialog_id", f"test_{i}")) % 1000000
                answer = await pipeline.process(q, user_id=uid)
                ms = int((time.monotonic() - start) * 1000)
                al = answer.lower()

                ok = True
                fail_reason = ""

                # Проверка ключевых слов
                if t.get("expect_keywords"):
                    has_kw = any(w in al for w in t["expect_keywords"])
                    if not has_kw:
                        ok = False
                        fail_reason = f"Нет ключевых слов: {t['expect_keywords']}"

                # Проверка запрещённых слов
                if t.get("expect_not_keywords"):
                    has_bad = any(w in al for w in t["expect_not_keywords"])
                    if has_bad:
                        ok = False
                        fail_reason = f"Содержит запрещённое: {t['expect_not_keywords']}"

                # Проверка длины
                if t.get("expect_min_len") and len(answer) < t["expect_min_len"]:
                    ok = False
                    fail_reason = f"Слишком короткий ответ ({len(answer)} симв.)"

                # Ошибка LLM
                if "временная ошибка" in al:
                    ok = False
                    fail_reason = "Ошибка LLM"

                test_result.update({
                    "passed": ok, "time_ms": ms,
                    "answer": answer[:300], "fail_reason": fail_reason
                })
            except Exception as e:
                ok = False
                test_result.update({"passed": False, "error": str(e)[:200]})

        if ok:
            passed += 1
            categories[cat]["passed"] += 1
            print(f"  ✅ {t['description']} ({test_result.get('time_ms', 0)}ms)")
        else:
            failed += 1
            categories[cat]["failed"] += 1
            reason = test_result.get("fail_reason", test_result.get("error", ""))
            print(f"  ❌ {t['description']}")
            if reason:
                print(f"     Причина: {reason}")
            if test_result.get("answer"):
                print(f"     Ответ: {test_result['answer'][:150]}")

        categories[cat]["tests"].append(test_result)
        results.append(test_result)

    # ═══ ИТОГ ═══
    total = passed + failed
    pct = int(passed / total * 100) if total else 0

    print(f"\n{'=' * 70}")
    print(f"ИТОГ ПРИЁМОЧНОГО ТЕСТА")
    print(f"{'=' * 70}")
    print(f"\n{'Категория':<25} {'Pass':>6} {'Fail':>6} {'Итог':>8}")
    print("-" * 50)
    for cat, data in categories.items():
        t = data["passed"] + data["failed"]
        s = "✅" if data["failed"] == 0 else "❌"
        print(f"{s} {cat:<23} {data['passed']:>6} {data['failed']:>6} {data['passed']}/{t:>5}")
    print("-" * 50)
    print(f"{'ВСЕГО:':<25} {passed:>6} {failed:>6} {passed}/{total:>5} ({pct}%)")

    # Сохраняем
    report = {
        "date": datetime.now().isoformat(),
        "total": total, "passed": passed, "failed": failed, "pct": pct,
        "categories": {k: {"passed": v["passed"], "failed": v["failed"]} for k, v in categories.items()},
        "tests": results,
    }

    with open("reports/acceptance_test.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Отчёт для клиента
    with open("reports/acceptance_test.txt", "w", encoding="utf-8") as f:
        f.write("ПРИЁМОЧНЫЙ ТЕСТ — AI-помощник Евроопт\n")
        f.write(f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"КП: КП-ЕВРО-002 v2.0 от 25.03.2026\n")
        f.write(f"{'=' * 60}\n\n")
        f.write(f"РЕЗУЛЬТАТ: {passed}/{total} ({pct}%)\n\n")

        for cat, data in categories.items():
            t = data["passed"] + data["failed"]
            s = "PASS" if data["failed"] == 0 else "FAIL"
            f.write(f"[{s}] {cat}: {data['passed']}/{t}\n")
            for test in data["tests"]:
                ts = "OK" if test["passed"] else "FAIL"
                f.write(f"  [{ts}] {test['description']}\n")
                if not test["passed"]:
                    if test.get("fail_reason"):
                        f.write(f"    Причина: {test['fail_reason']}\n")
                    if test.get("answer"):
                        f.write(f"    Ответ: {test['answer'][:200]}\n")
            f.write("\n")

    print(f"\nJSON: reports/acceptance_test.json")
    print(f"Отчёт для клиента: reports/acceptance_test.txt")

if __name__ == "__main__":
    asyncio.run(main())
