# Евроопт AI-помощник — MVP

Telegram-бот для розничных сетей «Евроопт», «Грошык» и «Хит Дискаунтер».
Подсвечивает актуальные акции, отвечает на FAQ, рекомендует рецепты.

## Структура

```
src/
├── bot/          # Telegram-бот (aiogram 3.x)
├── rag/          # RAG-система (embeddings + vector DB)
├── llm/          # LLM-адаптер (Claude/DeepSeek)
├── knowledge/    # Управление базой знаний
├── promotions/   # Промоушн-движок (подсветка акций)
├── filters/      # Контент-фильтр
├── monitoring/   # Логирование и метрики
data/
├── faq/          # FAQ от маркетинга
├── recipes/      # Рецепты
├── promotions/   # Акции
├── stores/       # Список магазинов
config/           # Конфигурация
tests/            # Тесты
scripts/          # Скрипты деплоя и утилиты
docs/             # Документация
```

## Запуск

```bash
cp .env.example .env  # Заполнить API ключи
pip install -r requirements.txt
python -m src.bot.main
```

## Стек

- Python, FastAPI, aiogram 3.x
- Claude API (LLM), ChromaDB (RAG)
- Docker, VPS в контуре РБ
