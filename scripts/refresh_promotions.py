"""Авто-обновление акций с сайтов Евроторга через Tavily.

Запускается либо по cron на Render (Job), либо вручную:
    python3 scripts/refresh_promotions.py

Результат: обновлённые чанки «акции» в RAG + сохранение в data/promotions_auto/*.json
"""
import sys, json, os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, ".")
from src.search.web import get_web_search
from src.rag.engine import RAGEngine

OUT_DIR = Path("data/promotions_auto")
OUT_DIR.mkdir(parents=True, exist_ok=True)

QUERIES = {
    "evroopt_main": ("site:evroopt.by текущие акции и скидки", ["evroopt.by"]),
    "evroopt_krasnaya": ("site:evroopt.by Красная цена каталог", ["evroopt.by"]),
    "evroopt_tseny_vniz": ("site:evroopt.by Цены вниз", ["evroopt.by"]),
    "evroopt_bonus": ("site:evroopt.by бонус-товары вернём бонусами", ["evroopt.by"]),
    "hit_akcii": ("site:hitdiscount.by акции цены вниз", ["hitdiscount.by"]),
    "hit_pyatnicy": ("site:hitdiscount.by жёлтые пятницы субботы", ["hitdiscount.by"]),
    "eplus_news": ("site:evroopt.by Еплюс новости бонусы акции", ["evroopt.by", "eplus.by"]),
    "udacha": ("site:igra.evroopt.by Удача в придачу призы туры", ["igra.evroopt.by"]),
}


def main():
    web = get_web_search()
    if not web.enabled:
        print("ERROR: web search disabled (set TAVILY_API_KEY + WEB_SEARCH_ENABLED=true)")
        return 1

    rag = RAGEngine()
    added = 0
    snapshot = {"ts": datetime.now().isoformat(), "queries": {}}

    for key, (query, domains) in QUERIES.items():
        print(f"\n🔍 {key}: {query}")
        results = web.search(query, max_results=5, domains=domains)
        print(f"   результатов: {len(results)}")
        snapshot["queries"][key] = results

        for i, r in enumerate(results):
            text = (
                f"Актуальная акция (с сайта {r['url']}):\n"
                f"Заголовок: {r.get('title','')}\n\n"
                f"{r.get('content','')}\n\n"
                f"Источник: {r['url']}\n\n"
                f"Ключевые слова: актуальная акция, сейчас, {key}"
            )
            doc_id = f"web_{key}_{i}"
            rag.add_documents([{
                "id": doc_id,
                "text": text,
                "metadata": {"category": "promotion_web", "source": "auto_refresh",
                             "url": r.get("url", ""), "refreshed": snapshot["ts"]},
            }])
            added += 1

    # Сохраняем снэпшот
    fn = OUT_DIR / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    fn.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Добавлено в RAG: {added} чанков")
    print(f"💾 Сохранён снэпшот: {fn}")
    print(f"📊 Всего документов в RAG: {rag.collection.count()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
