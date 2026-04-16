"""Query Rewriting + HyDE для улучшения RAG-поиска.

- Rewriter: переформулирует короткие/двусмысленные запросы в развёрнутые
- HyDE (Hypothetical Document Embeddings): генерирует ожидаемый ответ,
  по нему ищем контекст — даёт лучший матчинг на косвенных запросах.
"""
import structlog

logger = structlog.get_logger()

REWRITE_PROMPT = """Переформулируй короткий вопрос пользователя в развёрнутый поисковый запрос на русском.

Контекст: это вопрос к AI-ассистенту сети магазинов «Евроторг» (Евроопт, Грошык, Хит Дискаунтер).

Правила:
- Оставь ключевые слова (бренды, названия акций, товары)
- Добавь контекст где нужен (например "Грошык" → "сеть магазинов Грошык Евроторг")
- Если запрос уже развёрнутый — верни его без изменений
- Если запрос общий (рецепт, история) — верни его как есть
- НЕ добавляй лишние слова, только уточнение контекста
- Ответ одной строкой, без кавычек

Вопрос: {question}
Поисковый запрос:"""


HYDE_PROMPT = """Представь, что ты уже нашёл идеальный ответ на этот вопрос в базе знаний о сети «Евроторг». Напиши короткий (3-5 предложений) гипотетический ответ как если бы он был в FAQ или описании акции.

Это нужно не для пользователя, а для поиска в векторной базе — чем точнее формулировка, тем лучше матчинг.

Если вопрос не про Евроторг — напиши как ChatGPT бы ответил.

Вопрос: {question}
Гипотетический ответ:"""


async def rewrite_query(question: str, llm_adapter) -> str:
    """Rewrite короткий запрос в развёрнутый. Один LLM-вызов."""
    question = question.strip()
    if len(question) > 80 or len(question.split()) > 10:
        return question  # Уже длинный — не переписываем

    try:
        response = await llm_adapter.generate(
            system="Ты переписываешь короткие запросы пользователей в развёрнутые поисковые запросы.",
            user=REWRITE_PROMPT.format(question=question),
            history=[],
            max_tokens=100,
            temperature=0.1,
        )
        rewritten = (response.text or "").strip().strip('"').strip("'").strip()
        if rewritten and len(rewritten) > len(question):
            logger.info("query_rewritten", original=question[:40], rewritten=rewritten[:80])
            return rewritten
    except Exception as e:
        logger.warning("rewrite_failed", error=str(e))

    return question


async def generate_hyde(question: str, llm_adapter) -> str:
    """HyDE — генерирует гипотетический ответ для поиска."""
    try:
        response = await llm_adapter.generate(
            system="Генерируй гипотетические ответы для поиска в базе знаний.",
            user=HYDE_PROMPT.format(question=question),
            history=[],
            max_tokens=200,
            temperature=0.3,
        )
        hyde = (response.text or "").strip()
        if hyde:
            logger.info("hyde_generated", q=question[:40], hyde_len=len(hyde))
            return hyde
    except Exception as e:
        logger.warning("hyde_failed", error=str(e))

    return question  # fallback — оригинал
