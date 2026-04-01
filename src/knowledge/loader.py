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
    """Load store information into RAG."""
    stores_dir = DATA_DIR / "stores"
    documents = []

    for file in stores_dir.glob("*.json"):
        items = load_json_data(file)
        for i, item in enumerate(items):
            name = item.get("name", "")
            address = item.get("address", "")
            hours = item.get("hours", "")
            network = item.get("network", "")

            text = f"Магазин: {name}\nАдрес: {address}\nРежим работы: {hours}\nСеть: {network}"
            documents.append({
                "id": f"store_{file.stem}_{i}",
                "text": text,
                "metadata": {"category": "store", "network": network, "source": file.name},
            })

    if documents:
        rag.add_documents(documents)
        logger.info("stores_loaded", count=len(documents))


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
