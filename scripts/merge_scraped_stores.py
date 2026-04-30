"""Объединяет свежие данные с сайтов (evroopt.by/shops/, groshyk.by/shops/, hitdiscount.by/shops/)
с существующим data/stores/all_stores.json от Алексея.

Стратегия:
- Евроопт и Хит — берём из спарсенных данных (актуальные на 30.04.2026)
- Грошык — НОВЫЙ источник (раньше было 0)
- Автолавки (55 шт) — оставляем из старого all_stores.json (на сайте их нет)
- На выходе: data/stores/all_stores.json — обновлённый, и data/stores/all_stores_2026-04-30.backup.json — бэкап старого
"""
import json
import shutil
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
STORES = ROOT / "data" / "stores"
SCRAPED = ROOT / "data" / "stores_scraped"

OLD = STORES / "all_stores.json"
BACKUP = STORES / f"all_stores_{date.today().isoformat()}.backup.json"

# Источники свежих данных
EVROOPT_FRESH = SCRAPED / "evroopt_shops_2026-04-30.json"
GROSHYK_FRESH = SCRAPED / "groshyk_shops_2026-04-30.json"
HIT_FRESH = SCRAPED / "hit_shops_2026-04-30.json"


def load_old_stores() -> list[dict]:
    return json.loads(OLD.read_text(encoding="utf-8"))


def load_fresh(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_evroopt(fresh: list[dict], next_id: int) -> list[dict]:
    """Преобразуем спарсенные данные Евроопт в наш формат."""
    out = []
    for s in fresh:
        out.append({
            "id": s["id"],
            "brand": s["brand"],
            "format": s["format"].replace("Евроопт ", ""),  # "Евроопт Маркет" -> "Маркет"
            "format_raw": s["format"],
            "format_group": "from_evroopt.by",
            "city": s["city"],
            "address": s["addr_full"],
            "street": s["street"],
            "house": s["house"],
            "raw_name": s["addr_full"],
            "lat": s.get("lat"),
            "lng": s.get("lng"),
            "schedule": s.get("sch", {}),
            "is_24h": bool(s.get("is_24h")),
            "source": "evroopt.by/shops 2026-04-30",
        })
    return out


def normalize_other(fresh: list[dict], brand: str, format_default: str, start_id: int) -> list[dict]:
    """Грошык / Хит — из DOM-парсинга, без координат и id с сайта."""
    out = []
    next_id = start_id
    for s in fresh:
        out.append({
            "id": next_id,
            "brand": brand,
            "format": format_default,
            "format_raw": format_default,
            "format_group": s.get("region", ""),
            "city": s["city"],
            "address": s["addr_full"],
            "street": s["street"],
            "house": s["house"],
            "raw_name": s["addr_full"],
            "lat": None,
            "lng": None,
            "schedule": {},
            "is_24h": False,
            "source": f"{brand.lower()}.by/shops 2026-04-30",
        })
        next_id += 1
    return out


def main():
    print("=" * 70)
    print("Merge stores: scraped data → all_stores.json")
    print("=" * 70)

    # 1. Бэкап старого
    old = load_old_stores()
    BACKUP.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Бэкап старого all_stores.json → {BACKUP.name} ({len(old)} магазинов)")

    # 2. Извлекаем автолавки из старых данных (на сайте их нет)
    avtolavki = [s for s in old if s.get("format") == "Автолавка"]
    print(f"✓ Автолавки сохраняем из старых данных: {len(avtolavki)}")

    # 3. Загружаем свежие
    evroopt_fresh = load_fresh(EVROOPT_FRESH)
    groshyk_fresh = load_fresh(GROSHYK_FRESH)
    hit_fresh = load_fresh(HIT_FRESH)
    print(f"✓ Загружено свежих: Евроопт {len(evroopt_fresh)}, Грошык {len(groshyk_fresh)}, Хит {len(hit_fresh)}")

    # 4. Нормализуем
    evroopt_norm = normalize_evroopt(evroopt_fresh, 0)
    # Для Грошык/Хит — берём ID за пределами Евроопт + Хит
    hit_norm = normalize_other(hit_fresh, "Хит", "Хит-Экспресс", start_id=900_000)
    groshyk_norm = normalize_other(groshyk_fresh, "Грошык", "Грошык", start_id=950_000)

    # 5. Объединяем
    new = evroopt_norm + hit_norm + groshyk_norm + avtolavki
    print(f"\n✓ Итого после merge: {len(new)} магазинов")
    print(f"  - Евроопт: {sum(1 for s in new if s['brand'] == 'Евроопт' and s['format'] != 'Автолавка')}")
    print(f"  - Хит: {sum(1 for s in new if s['brand'] == 'Хит')}")
    print(f"  - Грошык: {sum(1 for s in new if s['brand'] == 'Грошык')}")
    print(f"  - Автолавки: {sum(1 for s in new if s['format'] == 'Автолавка')}")

    # 6. Сохраняем
    OLD.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Сохранено в {OLD.name}")

    # 7. euroopt.json — ТОЛЬКО свежие данные с сайта (без автолавок, они отдельно)
    euroopt_only = [s for s in new if s["brand"] == "Евроопт" and s["format"] != "Автолавка"]
    (STORES / "euroopt.json").write_text(json.dumps(euroopt_only, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ Сохранено в euroopt.json ({len(euroopt_only)} магазинов, без автолавок)")

    # 8. Стат по городам
    from collections import Counter
    cities = Counter(s["city"] for s in new)
    print(f"\nГородов с магазинами: {len(cities)}")
    print(f"Топ-10 городов: {cities.most_common(10)}")


if __name__ == "__main__":
    main()
