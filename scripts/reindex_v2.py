"""Reindex v2 — полная переиндексация с актуальными данными от Евроторга (15.04.2026).

Новое:
- Акции Евроопт 2026 (Красная цена, Выходные оптом, Цены вниз, Бонус-товары, Грильфест)
- Акции Хит Дискаунтер (Цены вниз, Жёлтые пятницы, ХИТ! ЦЕНА)
- Удача в придачу — 28 разделов FAQ + 628 товаров удачи
- Магазины с разметкой сетей Евроопт / Хит (1084 точки)
- Корректные URL: evroopt.by, groshyk.by, hitdiscount.by

Старое (сохраняем):
- FAQ Еплюс (16 вопросов)
- FAQ general + contacts
- Рецепты Алексея + YouTube
- Магазины Минск (28 с часами)
"""
import sys, json, re
from pathlib import Path
sys.path.insert(0, ".")

from docx import Document
import openpyxl
from src.rag.engine import RAGEngine

DATA = Path("data")
docs = []


def add(id_, text, category, **meta):
    docs.append({"id": id_, "text": text, "metadata": {"category": category, **meta}})


# ============ FAQ ============

def load_faq_eplus():
    d = Document(DATA / "faq/faq_eplus.docx")
    n = 0
    for table in d.tables:
        for i, row in enumerate(table.rows[1:]):
            cells = [c.text.strip() for c in row.cells]
            if len(cells) < 2 or not cells[1]:
                continue
            text = cells[1]
            lines = text.split("\n")
            q = lines[0].strip().rstrip("?") + "?"
            a = "\n".join(lines[1:]).strip()
            if not q or not a:
                continue
            full = (
                f"FAQ Еплюс — {q}\n\n"
                f"Вопрос: {q}\n\nОтвет: {a}\n\n"
                f"Ключевые слова: Еплюс карта, как оформить, как получить, "
                f"программа лояльности, бонусы, баллы, начисление, списание, "
                f"виртуальная карта, пластиковая карта, баланс"
            )
            add(f"faq_eplus_{i}", full, "faq", source="faq_eplus", topic="eplus")
            n += 1
    return n


def load_faq_json():
    n = 0
    for fn in ["general.json", "contacts.json", "contacts_full.json"]:
        fp = DATA / "faq" / fn
        if not fp.exists():
            continue
        items = json.load(open(fp, encoding="utf-8"))
        if isinstance(items, dict):
            items = [items]
        for i, it in enumerate(items):
            q = it.get("question") or ""
            a = it.get("answer") or ""
            # Заменяем упоминания e-доставки на evroopt.by (по требованию клиента)
            a = a.replace("e-dostavka.by", "evroopt.by")
            a = a.replace("edostavka.by", "evroopt.by")
            a = a.replace("Едоставка", "Евроопт")
            a = a.replace("e-доставка", "сеть Евроопт")
            a = a.replace("е-доставка", "сеть Евроопт")
            text = "Контакты Евроторг" if "contact" in fn else "Часто задаваемый вопрос"
            text += f"\n\nВопрос: {q}\nОтвет: {a}"
            add(f"faq_{fn}_{i}", text, "faq", source=fn)
            n += 1
    return n


def load_brand_links():
    """Добавить корректные URL сетей (по требованию клиента)."""
    brands = [
        ("Евроопт", "https://evroopt.by/", "гипермаркеты продовольствия в Беларуси"),
        ("Хит Дискаунтер", "https://hitdiscount.by/", "магазины-дискаунтеры сети Евроторг"),
        ("Грошык", "https://groshyk.by/", "сеть магазинов у дома Евроторга"),
    ]
    n = 0
    for brand, url, desc in brands:
        text = (
            f"Сайт сети «{brand}»\n\n"
            f"Официальный сайт: {url}\n"
            f"Описание: {desc} сети «Евроторг».\n\n"
            f"Ключевые слова: {brand}, сайт, ссылка, официальный"
        )
        add(f"brand_link_{brand}", text, "brand", source="manual")
        n += 1
    return n


# ============ Promotions Евроопт (новые!) ============

def load_promotions_evroopt():
    fp = DATA / "promotions_new/evroopt.xlsx"
    wb = openpyxl.load_workbook(fp)
    n = 0
    # Лист "Все акции" — описание кампаний
    ws = wb["Все акции"]
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
        name, date, desc, leaflet, page_url = row
        if not name:
            continue
        text = (
            f"Акция «Евроопт»: {name}\n"
            f"Период: {date or '—'}\n"
            f"Описание: {desc or '—'}\n"
            f"Ссылка на каталог: {page_url or '—'}\n"
            f"Листовка: {leaflet or '—'}\n\n"
            f"Ключевые слова: акция Евроопт, {name}, скидки, промо, листовка"
        )
        add(f"evroopt_promo_{i}", text, "promotion", source="evroopt_xlsx", promo_name=str(name)[:50])
        n += 1

    # Листы с каталогами товаров
    catalogs = {
        "Каталог Красная цена": ("Красная цена", ["Код товара", "Наименование товара", "Цена без скидки", "Цена на скидки"]),
        "Каталог бонус товары": ("Бонус-товары", ["Код товара", "Наименование товара", "Цена", "Вернем рублей", "Вернем бонусов"]),
        "Каталог Грильфест": ("Грильфест", ["Код товара", "Наименование товара", "Цена"]),
        "Каталог товаров Цены Вниз": ("Цены вниз", ["Код товара", "Наименование товара", "Цена без карты", "Цена (акция)"]),
    }
    for sheet_name, (promo_name, cols) in catalogs.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        current_category = None
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
            cells = [c for c in row if c is not None]
            if not cells:
                continue
            # Если только одна ячейка и это текст — это категория-разделитель
            if len([c for c in cells if c]) == 1 and isinstance(cells[0], str):
                current_category = cells[0]
                continue
            code = str(row[0]) if row[0] else ""
            name = str(row[1]) if len(row) > 1 and row[1] else ""
            if not name:
                continue
            price_info = ""
            if promo_name == "Красная цена":
                old, new = row[2], row[3] if len(row) > 3 else None
                price_info = f"обычная цена: {old}, по акции: {new} руб"
            elif promo_name == "Бонус-товары":
                price = row[2]
                rub_back = row[3] if len(row) > 3 else None
                bonus_back = row[4] if len(row) > 4 else None
                price_info = f"цена: {price} руб, вернём: {rub_back} руб / {bonus_back} бонусов"
            elif promo_name == "Грильфест":
                price_info = f"цена: {row[2]} руб"
            elif promo_name == "Цены вниз":
                price_info = f"без карты: {row[2]} / с акцией: {row[3]} руб"

            text = (
                f"Товар акции «{promo_name}» (Евроопт):\n"
                f"Название: {name}\n"
                f"{price_info}\n"
                f"{f'Категория: {current_category}' if current_category else ''}\n"
                f"Код: {code}\n\n"
                f"Ключевые слова: {promo_name}, акция Евроопт, скидка, товар"
            )
            add(f"evroopt_{promo_name}_{i}", text, "promotion_item",
                source="evroopt_xlsx", promo=promo_name, product=name[:60])
            n += 1
    return n


# ============ Promotions Хит Дискаунтер ============

def load_promotions_hit():
    fp = DATA / "promotions_new/hitdiscount.xlsx"
    wb = openpyxl.load_workbook(fp)
    n = 0
    # Все акции
    ws = wb["Все акции"]
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
        name, date, desc, leaflet, page_url = row
        if not name:
            continue
        text = (
            f"Акция «Хит Дискаунтер»: {str(name).strip()}\n"
            f"Период: {date or '—'}\n"
            f"Описание: {desc or '—'}\n"
            f"Ссылка на каталог: {page_url or '—'}\n\n"
            f"Ключевые слова: акция Хит, {name}, скидки, промо"
        )
        add(f"hit_promo_{i}", text, "promotion", source="hit_xlsx",
            promo_name=str(name)[:50], brand="Хит")
        n += 1

    # Каталог цены вниз
    if "Каталог цены вниз" in wb.sheetnames:
        ws = wb["Каталог цены вниз"]
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
            code, name, period, price = row[:4]
            if not name:
                continue
            text = (
                f"Товар акции «Цены вниз» в «Хит Дискаунтер»:\n"
                f"Название: {name}\n"
                f"Цена с картой: {price} руб\n"
                f"Период: {period}\n"
                f"Код: {code}\n\n"
                f"Ключевые слова: Хит, Цены вниз, акция, скидка"
            )
            add(f"hit_priceDown_{i}", text, "promotion_item",
                source="hit_xlsx", promo="Цены вниз", brand="Хит", product=str(name)[:60])
            n += 1
    return n


# ============ Удача в придачу ============

def load_udacha():
    fp = DATA / "udacha/igra.evroopt УВП.xlsx"
    wb = openpyxl.load_workbook(fp)
    n = 0

    # FAQ-разделы УВП
    ws = wb["Основное разделы"]
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
        topic, desc, url = row[:3]
        if not topic:
            continue
        text = (
            f"«Удача в придачу!» (акция Евроторг): {topic}\n\n"
            f"{desc or ''}\n\n"
            f"Ссылка: {url or 'https://igra.evroopt.by/'}\n\n"
            f"Ключевые слова: Удача в придачу, УВП, игра, призы, туры, бонусы, Евроопт"
        )
        add(f"udacha_topic_{i}", text, "udacha", source="udacha_xlsx")
        n += 1

    # Товары удачи по сетям
    for sheet_name in wb.sheetnames:
        if not sheet_name.startswith("Товары удачи"):
            continue
        network = sheet_name.replace("Товары удачи ", "")
        ws = wb[sheet_name]
        items = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            code = row[0]
            name = row[1] if len(row) > 1 else None
            if code and name:
                items.append(f"{name}")
        if not items:
            continue
        # Один большой чанк на сеть (товаров много, заголовков мало)
        chunk_size = 20
        for chunk_i in range(0, len(items), chunk_size):
            chunk = items[chunk_i:chunk_i + chunk_size]
            text = (
                f"Товары «Удачи в придачу!» — {network} ({chunk_i+1}–{chunk_i+len(chunk)} из {len(items)}):\n\n"
                + "\n".join(f"• {x}" for x in chunk)
                + "\n\nЭти товары участвуют в розыгрыше «Удача в придачу!».\n"
                + f"Ключевые слова: Удача в придачу, товары удачи, {network}"
            )
            add(f"udacha_items_{network}_{chunk_i}", text, "udacha_items",
                source="udacha_xlsx", network=network)
            n += 1
    return n


# ============ Магазины с разметкой Евроопт / Хит ============

def load_stores_with_brand():
    """Новый файл с полем 'Тип склада(Общий)' → Евроопт / Хит."""
    fp = DATA / "stores_new/Список ТО ЕТ Хит с форматами.xlsx"
    wb = openpyxl.load_workbook(fp)
    ws = wb.active
    n = 0
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 1):
        sklad, name, fmt, type_, brand = row[:5]
        if not name:
            continue
        m = re.search(r"Магазин\s+(.*?)(?::\(|$)", str(name))
        addr = m.group(1).strip() if m else str(name)
        if not addr:
            continue
        text = (
            f"Магазин {brand or 'сети Евроторг'}\n"
            f"Адрес: {addr}\n"
            f"Формат: {fmt or '—'}\n"
            f"Тип: {type_ or '—'}\n\n"
            f"Ключевые слова: магазин {brand or 'Евроторг'}, адрес"
        )
        add(f"store_branded_{i}", text, "store",
            source="stores_branded", brand=str(brand) if brand else "Евроторг")
        n += 1
    return n


def load_stores_minsk_full():
    """Магазины Минск с часами работы (из старого json)."""
    fp = DATA / "stores/euroopt.json"
    if not fp.exists():
        return 0
    items = json.load(open(fp, encoding="utf-8"))
    n = 0
    for i, st in enumerate(items):
        name = st.get("name", "Евроопт")
        addr = st.get("address", "")
        city = st.get("city", "Минск")
        hrs = st.get("hours", "")
        text = (
            f"Магазин Евроопт в {city}\n"
            f"Название: {name}\n"
            f"Адрес: {city}, {addr}\n"
            f"Часы работы: {hrs or 'уточните на evroopt.by'}\n\n"
            f"Ключевые слова: адрес Евроопт {city}, время работы, график"
        )
        add(f"store_minsk_hours_{i}", text, "store",
            source="minsk_json", brand="Евроопт", city=city)
        n += 1
    return n


# ============ Рецепты ============

def load_recipes_docx():
    fp = DATA / "recipes/recipes_alexey.docx"
    if not fp.exists():
        return 0
    d = Document(fp)

    # Парсим по блокам "Название рецепта — X порций — калорийность — Ингредиенты ... — Шаги"
    # Ищем по маркерам "Порции:", "Ингредиенты:", "Приготовление:", "Шаг"
    current = None
    recipes = []
    for p in d.paragraphs:
        text = p.text.strip()
        if not text:
            if current and len(current.get("lines", [])) > 3:
                # Пустая строка — возможно разделитель
                pass
            continue
        # Начало нового рецепта — короткая строка без маркеров
        if (
            len(text) < 60 and
            not text.startswith(("Шаг", "•", "-", "–", "Ингредиенты", "Приготовление", "Порции", "Калорийность", "Время"))
            and not re.match(r"^\d+[.\)]", text)
            and ":" not in text
            and len(text.split()) <= 6
            and any(c.isupper() for c in text[:3])
        ):
            if current and len(current["lines"]) > 3:
                recipes.append(current)
            current = {"name": text, "lines": []}
            continue
        if current:
            current["lines"].append(text)
    if current and len(current["lines"]) > 3:
        recipes.append(current)

    n = 0
    for i, r in enumerate(recipes):
        name = r["name"]
        if len(name) < 4:
            continue
        body = "\n".join(r["lines"])
        text = (
            f"Рецепт: {name}\n\n{body}\n\n"
            f"Ключевые слова: рецепт, {name}, как приготовить, ингредиенты"
        )
        add(f"recipe_v2_{i}", text, "recipe", source="recipes_alexey", name=name)
        n += 1
    return n


def load_recipes_json():
    n = 0
    for fn in ["sample.json", "youtube.json"]:
        fp = DATA / "recipes" / fn
        if not fp.exists():
            continue
        items = json.load(open(fp, encoding="utf-8"))
        if isinstance(items, dict):
            items = [items]
        for i, r in enumerate(items):
            name = r.get("name") or r.get("title", "Рецепт")
            ingr = r.get("ingredients", [])
            steps = r.get("steps", [])
            url = r.get("url", "")
            text = f"Рецепт: {name}\n"
            if ingr:
                text += "Ингредиенты:\n" + "\n".join(f"- {i}" for i in ingr) + "\n"
            if steps:
                text += "Приготовление:\n" + "\n".join(f"{j+1}. {s}" for j, s in enumerate(steps))
            if url:
                text += f"\nВидео: {url}"
            text += f"\n\nКлючевые слова: рецепт, {name}"
            add(f"recipe_{fn}_{i}", text, "recipe", source=fn, name=name)
            n += 1
    return n


def main():
    print("=== Парсинг источников ===")
    counts = {
        "FAQ Еплюс": load_faq_eplus(),
        "FAQ JSON (с заменой e-доставки→evroopt)": load_faq_json(),
        "Brand links (evroopt/groshyk/hitdiscount)": load_brand_links(),
        "🆕 Акции Евроопт (Красная цена, Цены вниз, Бонусы, Грильфест)": load_promotions_evroopt(),
        "🆕 Акции Хит Дискаунтер": load_promotions_hit(),
        "🆕 Удача в придачу (FAQ + товары удачи)": load_udacha(),
        "🆕 Магазины с брендом (Евроопт/Хит)": load_stores_with_brand(),
        "Магазины Минск с часами": load_stores_minsk_full(),
        "Рецепты DOCX": load_recipes_docx(),
        "Рецепты JSON": load_recipes_json(),
    }
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  ИТОГО: {len(docs)} документов")

    print("\n=== Загрузка в RAG (новая коллекция v3) ===")
    rag = RAGEngine()
    try:
        rag.client.delete_collection("euroopt_knowledge_v3")
        print("  старая коллекция удалена")
    except Exception:
        pass
    rag = RAGEngine()
    for i in range(0, len(docs), 50):
        rag.add_documents(docs[i:i+50])
    print(f"  загружено: {rag.collection.count()}")

    print("\n=== Тест поиска ===")
    for q in [
        "Что такое Красная цена?", "какие акции в Хит?", "товары удачи",
        "Цены вниз мясо", "магазин Хит в Минске", "призы в 214 туре",
        "Какая скидка на капусту?", "какие бонус-товары?",
    ]:
        results = rag.search(q, n_results=2)
        print(f"\nQ: {q} ({len(results)})")
        for r in results[:2]:
            print(f"  score={r.get('score',0):.3f} | {(r.get('text') or '')[:120]}")


if __name__ == "__main__":
    main()
