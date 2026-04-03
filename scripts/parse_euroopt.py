"""Парсер данных с сайтов Евроопт для загрузки в RAG.

Источники:
- evroopt.by — FAQ (контакты), рецепты (видео)
- edostavka.by — акции, каталог товаров с ценами
- groshyk.by — акции Грошык

Использование:
    python3.11 scripts/parse_euroopt.py --all
    python3.11 scripts/parse_euroopt.py --promotions
    python3.11 scripts/parse_euroopt.py --contacts
"""

import json
import re
import sys
from pathlib import Path
from datetime import date

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path("data")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch(url: str) -> BeautifulSoup:
    """Загрузить страницу и вернуть BeautifulSoup."""
    print(f"  Загружаю: {url}")
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_edostavka_promotions():
    """Парсинг акций с edostavka.by."""
    print("\n=== Парсинг акций edostavka.by ===")

    # Страницы акций
    promo_pages = [
        "https://edostavka.by/actions/crush-price",
        "https://edostavka.by/actions/lucky-goods",
        "https://edostavka.by/actions/rybnye-dni",
    ]

    all_promotions = []

    for page_url in promo_pages:
        try:
            soup = fetch(page_url)

            # Ищем карточки товаров — типичные CSS-классы для e-commerce
            cards = soup.select("[class*='product'], [class*='card'], [class*='item'], [class*='good']")

            for card in cards:
                # Ищем название
                name_el = card.select_one("[class*='name'], [class*='title'], h3, h4, a[title]")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name or len(name) < 3:
                    continue

                # Ищем цены
                prices = card.select("[class*='price']")
                price_texts = [p.get_text(strip=True) for p in prices]

                # Пытаемся извлечь числа
                price_numbers = []
                for pt in price_texts:
                    nums = re.findall(r"(\d+[.,]\d{2})", pt)
                    price_numbers.extend(nums)

                promo = {
                    "name": name,
                    "source": page_url,
                    "end_date": "2026-12-31",  # Заглушка
                }

                if len(price_numbers) >= 2:
                    promo["old_price"] = price_numbers[0].replace(",", ".")
                    promo["new_price"] = price_numbers[1].replace(",", ".")
                elif len(price_numbers) == 1:
                    promo["new_price"] = price_numbers[0].replace(",", ".")

                all_promotions.append(promo)

            print(f"  → Найдено товаров: {len(cards)}, с данными: {len(all_promotions)}")

        except Exception as e:
            print(f"  Ошибка {page_url}: {e}")

    # Если не нашли через карточки, пробуем весь текст страницы
    if not all_promotions:
        print("  Карточки не найдены, пробую текстовый парсинг...")
        try:
            soup = fetch("https://edostavka.by/actions")
            # Сохраняем сырой текст для анализа
            raw_path = DATA_DIR / "promotions" / "edostavka_raw.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(soup.get_text(separator="\n", strip=True)[:5000], encoding="utf-8")
            print(f"  → Сохранён сырой текст: {raw_path}")
        except Exception as e:
            print(f"  Ошибка: {e}")

    if all_promotions:
        out = DATA_DIR / "promotions" / "edostavka.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_promotions, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → Сохранено: {out} ({len(all_promotions)} акций)")

    return all_promotions


def parse_contacts():
    """Парсинг контактной информации для FAQ."""
    print("\n=== Парсинг контактов evroopt.by ===")

    contacts_faq = [
        {
            "question": "Как связаться с Евроопт?",
            "answer": "Горячая линия: +375 44 788 88 80 (с 9:00 до 21:00, ежедневно). "
                      "Общий номер: +375 17 289 00 00. "
                      "Email для обращений: obraschenie_01@eurotorg.by"
        },
        {
            "question": "Где находится главный офис Евроторг?",
            "answer": "Главный офис: г. Минск, ул. Монтажников, 2. Почтовый индекс: 220019."
        },
        {
            "question": "Как устроиться на работу в Евроопт?",
            "answer": "Отдел кадров: г. Минск, ул. Монтажников, 2, каб. 101. "
                      "Телефон: +375 17 279-80-80. Email: HR@eurotorg.by. "
                      "Также доступен Telegram-бот: @recruit_euroopt_bot"
        },
        {
            "question": "Как оформить доставку?",
            "answer": "Доставка доступна через сервис Едоставка (edostavka.by). "
                      "Оформите заказ на сайте или в приложении. "
                      "Информация о доставке и оплате: edostavka.by/information/help/delivery-and-payment"
        },
        {
            "question": "Что такое карта Е-плюс?",
            "answer": "Е-плюс — бонусная карта сетей Евроопт, Грошык и Хит Дискаунтер. "
                      "Подробности: evroopt.by/eplus"
        },
    ]

    out = DATA_DIR / "faq" / "contacts.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(contacts_faq, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → Сохранено: {out} ({len(contacts_faq)} вопросов)")

    return contacts_faq


def parse_groshyk_promotions():
    """Парсинг акций Грошык."""
    print("\n=== Парсинг акций groshyk.by ===")

    promo_pages = [
        "https://groshyk.by/navinka/",
        "https://groshyk.by/yashche-tannej-lending/",
    ]

    all_promotions = []

    for page_url in promo_pages:
        try:
            soup = fetch(page_url)
            cards = soup.select("[class*='product'], [class*='card'], [class*='item']")
            print(f"  → {page_url}: найдено {len(cards)} элементов")

            for card in cards:
                name_el = card.select_one("[class*='name'], [class*='title'], h3, h4")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if name and len(name) > 3:
                    all_promotions.append({
                        "name": name,
                        "source": page_url,
                        "end_date": "2026-12-31",
                    })
        except Exception as e:
            print(f"  Ошибка {page_url}: {e}")

    if all_promotions:
        out = DATA_DIR / "promotions" / "groshyk.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_promotions, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → Сохранено: {out} ({len(all_promotions)} акций)")

    return all_promotions


def parse_recipes_from_youtube():
    """Сохраняем ссылки на рецепты с YouTube канала Евроопт."""
    print("\n=== Рецепты (YouTube ссылки) ===")

    recipes = [
        {"name": "Облепиховый чизкейк", "url": "https://youtu.be/0wotaJLZxXU", "tags": ["десерт", "без сахара"]},
        {"name": "Баунти без сахара", "url": "https://youtu.be/FWKMxTyEvcw", "tags": ["десерт", "без сахара"]},
        {"name": "Лёгкий салат с индейкой", "url": "https://youtu.be/2j-7D1_1j7E", "tags": ["салат", "быстрый", "15 минут"]},
        {"name": "Идеальная сёмга", "url": "https://youtu.be/1aBqQd8f_0k", "tags": ["рыба", "горячее"]},
        {"name": "Горячее без тяжести", "url": "https://youtu.be/dS0ZovQC6lk", "tags": ["горячее", "лёгкое"]},
        {"name": "Оливье в новом формате", "url": "https://youtu.be/wZpm4WaS_54", "tags": ["салат", "новый год"]},
        {"name": "Сливочно-чесночная курица за 30 минут", "url": "https://youtube.com/shorts/ezUdw9BpGLY", "tags": ["ужин", "быстрый", "курица"]},
        {"name": "Два блюда из фарша", "url": "https://youtube.com/shorts/zoap7mxdUKQ", "tags": ["ужин", "фарш", "быстрый"]},
        {"name": "Холодник — хит лета", "url": "https://youtu.be/pE-yT5UrqXY", "tags": ["суп", "лето", "холодное"]},
        {"name": "Сочный люля-кебаб из говядины", "url": "https://youtu.be/0mEsq6fqAnY", "tags": ["горячее", "мясо", "гриль"]},
    ]

    out = DATA_DIR / "recipes" / "youtube.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recipes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → Сохранено: {out} ({len(recipes)} рецептов)")

    return recipes


def main():
    args = set(sys.argv[1:])

    if not args or "--all" in args:
        parse_contacts()
        parse_edostavka_promotions()
        parse_groshyk_promotions()
        parse_recipes_from_youtube()
    else:
        if "--contacts" in args:
            parse_contacts()
        if "--promotions" in args:
            parse_edostavka_promotions()
            parse_groshyk_promotions()
        if "--recipes" in args:
            parse_recipes_from_youtube()

    print("\n=== Готово ===")
    print("Для загрузки в RAG: python3.11 -c 'from src.knowledge.loader import load_all; load_all()'")


if __name__ == "__main__":
    main()
