import json
from pathlib import Path

import structlog

from src.rag.engine import RAGEngine

logger = structlog.get_logger()

DATA_DIR = Path("data")


def load_json_data(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def load_faq(rag: RAGEngine):
    """Load FAQ documents into RAG."""
    faq_dir = DATA_DIR / "faq"
    documents = []

    for file in faq_dir.glob("*.json"):
        items = load_json_data(file)
        for i, item in enumerate(items):
            q = item.get("question", "")
            a = item.get("answer", "")
            documents.append({
                "id": f"faq_{file.stem}_{i}",
                "text": f"Вопрос: {q}\nОтвет: {a}",
                "metadata": {"category": "faq", "source": file.name},
            })

    if documents:
        rag.add_documents(documents)
        logger.info("faq_loaded", count=len(documents))


def load_recipes(rag: RAGEngine):
    """Load recipes into RAG."""
    recipes_dir = DATA_DIR / "recipes"
    documents = []

    for file in recipes_dir.glob("*.json"):
        items = load_json_data(file)
        for i, item in enumerate(items):
            name = item.get("name", "")
            ingredients = ", ".join(item.get("ingredients", []))
            steps = "\n".join(f"{j+1}. {s}" for j, s in enumerate(item.get("steps", [])))
            time_min = item.get("time_minutes", "")
            tags = ", ".join(item.get("tags", []))

            text = f"Рецепт: {name}\nИнгредиенты: {ingredients}\nПриготовление:\n{steps}"
            if time_min:
                text += f"\nВремя: {time_min} мин."
            if tags:
                text += f"\nТеги: {tags}"

            documents.append({
                "id": f"recipe_{file.stem}_{i}",
                "text": text,
                "metadata": {"category": "recipe", "name": name, "source": file.name},
            })

    if documents:
        rag.add_documents(documents)
        logger.info("recipes_loaded", count=len(documents))


def load_stores(rag: RAGEngine):
    """Load store information into RAG.

    Поддерживает два формата:
    1. all_stores.json — основной справочник (1040 магазинов из xlsx Евроторга)
       с полями: brand (Евроопт/Хит), format (Маркет/Супер/Гипер/Хит Стандарт/
       Хит-Экспресс/Автолавка/Минимаркет), city, address.
       В RAG metadata кладётся brand для жёсткой фильтрации в search.

    2. euroopt.json (legacy) — старый формат с name/address/hours/network.
       Используется как дополнение, если там есть данные не из xlsx.
    """
    stores_dir = DATA_DIR / "stores"
    documents = []

    for file in stores_dir.glob("*.json"):
        items = load_json_data(file)
        for i, item in enumerate(items):
            # Новый формат (all_stores.json): brand/format/city/address
            if "brand" in item and "format" in item:
                brand = item.get("brand", "Евроопт")
                fmt = item.get("format", "Магазин")
                city = item.get("city", "")
                address = item.get("address", "")
                hours = item.get("hours", "")  # пока нет в xlsx

                # Текст для поиска: бренд и формат явно вписаны, чтобы
                # запросы вида «Хит в Минске» матчились через BM25.
                lines = [
                    f"Магазин сети {brand}",
                    f"Формат: {fmt}",
                    f"Город: {city}",
                    f"Адрес: {address}",
                ]
                if hours:
                    lines.append(f"Режим работы: {hours}")
                text = "\n".join(lines)

                documents.append({
                    "id": f"store_v2_{item.get('id', i)}",
                    "text": text,
                    "metadata": {
                        "category": "store",
                        "brand": brand,
                        "format": fmt,
                        "city": city,
                        "source": file.name,
                    },
                })
            else:
                # Legacy формат (euroopt.json): name/address/hours/network
                name = item.get("name", "")
                address = item.get("address", "")
                hours = item.get("hours", "")
                network = item.get("network", "Евроопт")
                city = item.get("city", "")

                text = f"Магазин: {name}\nАдрес: {address}"
                if city:
                    text += f"\nГород: {city}"
                if hours:
                    text += f"\nРежим работы: {hours}"
                text += f"\nСеть: {network}"

                documents.append({
                    "id": f"store_{file.stem}_{i}",
                    "text": text,
                    "metadata": {
                        "category": "store",
                        "brand": network or "Евроопт",
                        "city": city,
                        "source": file.name,
                    },
                })

    if documents:
        rag.add_documents(documents)
        # Статистика: сколько по каждому бренду
        by_brand: dict[str, int] = {}
        for d in documents:
            b = d["metadata"].get("brand", "?")
            by_brand[b] = by_brand.get(b, 0) + 1
        logger.info("stores_loaded", count=len(documents), by_brand=by_brand)


def load_all():
    """Load all knowledge base data into RAG."""
    rag = RAGEngine()
    load_faq(rag)
    load_recipes(rag)
    load_stores(rag)
    stats = rag.get_stats()
    logger.info("knowledge_base_loaded", **stats)
    return rag


if __name__ == "__main__":
    load_all()
