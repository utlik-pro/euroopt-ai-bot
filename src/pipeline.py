import os
import time
import structlog

from src.filters.content_filter import check_content
from src.filters.prompt_sanitizer import build_kb_block, build_web_block
from src.llm.adapter import get_llm_provider_with_fallback, LLMResponse
from src.llm.prompts import SYSTEM_PROMPT
from src.rag.engine import RAGEngine
from src.promotions.engine import PromotionEngine
from src.chat_history import ChatHistory
from src.monitoring.logger import interaction_logger, RequestLog
from src.search.web import get_web_search
from src.search.query_rewriter import rewrite_query

logger = structlog.get_logger()

ENABLE_REWRITE = os.environ.get("ENABLE_QUERY_REWRITE", "true").lower() == "true"
WEB_FALLBACK_MIN_RESULTS = int(os.environ.get("WEB_FALLBACK_MIN_RESULTS", "2"))
WEB_FALLBACK_MIN_SCORE = float(os.environ.get("WEB_FALLBACK_MIN_SCORE", "0.60"))

# Явные promo-триггеры: слова, которые однозначно про акции/скидки Евроторга.
# Эти слова перебивают fresh_data даже если в вопросе есть «сегодня».
FRESH_PROMO_STRONG = (
    "акци", "скидк", "скидочн", "ценопад", "распродаж",
    "красная цена", "чёрная пятниц", "черная пятниц",
    "чёрные цен", "чёрная цен", "черные цен", "черная цен",
    "цены вниз",
)

# Старый алиас для обратной совместимости тестов test_fresh_promo_trigger.py —
# включает явные promo + временные маркеры (без явного promo может быть не promo).
FRESH_PROMO_SUBSTRINGS = FRESH_PROMO_STRONG + (
    "сегодня", "сейчас", "на этой неделе", "эта недел", "в этом месяц",
    "актуальн", "свеж", "нов",
)


def needs_fresh_promo(msg: str) -> bool:
    """Пользователь спрашивает про актуальные цены/акции → форсим web search.

    Триггерят либо явные слова (акция, скидка, ценопад), либо временные маркеры
    (сегодня, сейчас) — но сами по себе временные маркеры не означают promo,
    проверяй приоритет с needs_fresh_data() в pipeline.
    """
    if not msg:
        return False
    low = msg.lower()
    return any(s in low for s in FRESH_PROMO_SUBSTRINGS)


def has_strong_promo_signal(msg: str) -> bool:
    """Явный сигнал promo (акция/скидка/ценопад) — перебивает fresh_data."""
    if not msg:
        return False
    low = msg.lower()
    return any(s in low for s in FRESH_PROMO_STRONG)


# Бренды/темы Евроторга — если вопрос содержит хоть одно, он «про нас»
# и web search должен идти по доменам евроопта (а не в общий интернет).
EUROTORG_BRAND_SUBSTRINGS = (
    "евроопт", "грошык", "хит дискаунтер", "хитдис", "еплюс", "e-plus",
    "ямигом", "я мигом", "едоставка", "е-доставка", "e-dostavka", "evroopt",
    "евроторг", "магазин", "карта лояльн", "бонусн", "еўраопт",
)


def is_eurotorg_question(msg: str) -> bool:
    """Вопрос содержит бренд/тему Евроторга? (тогда web → по доменам бренда)."""
    if not msg:
        return False
    low = msg.lower()
    return any(s in low for s in EUROTORG_BRAND_SUBSTRINGS)


# Триггеры «нужны свежие данные из интернета даже если вопрос общий».
# Используется только для решения «идти ли в Tavily общий интернет» — если RAG
# сам знает ответ, LLM отвечает без Tavily (экономим квоту).
FRESH_DATA_SUBSTRINGS = (
    "погод", "прогноз", "курс валют", "курс доллар", "курс евро", "курс рубл",
    "обменный курс", "сколько время", "который час", "сколько сейчас",
    "новост", "что в мире", "что произошл", "пробк", "ситуация на дорог",
    "сегодня", "сейчас", "недавно", "свеж", "актуальн",
)


def needs_fresh_data(msg: str) -> bool:
    """Нужны ли актуальные данные из интернета (погода, курс, время, новости)."""
    if not msg:
        return False
    low = msg.lower()
    return any(s in low for s in FRESH_DATA_SUBSTRINGS)


class Pipeline:
    def __init__(self):
        self.llm = get_llm_provider_with_fallback()
        self.rag = RAGEngine()
        self.promotions = PromotionEngine()
        self.history = ChatHistory(max_messages=20, ttl_minutes=60)
        self.web = get_web_search()

    async def process(self, user_message: str, user_id: int) -> str:
        """Process user message through the 4-layer pipeline.

        Layer 1: Content filter (block politics, religion, competitors)
        Layer 2: Query rewrite (short → expanded)
        Layer 3: RAG search + web search fallback
        Layer 4: LLM generation with chat history
        """
        start_time = time.monotonic()
        log = RequestLog(user_id=user_id, user_message=user_message)

        # Layer 1: Content filter
        is_allowed, refusal = check_content(user_message)
        if not is_allowed:
            log.content_filtered = True
            log.filter_reason = "blocked"
            log.bot_response = refusal
            log.response_time_ms = int((time.monotonic() - start_time) * 1000)
            interaction_logger.log_request(log)
            return refusal

        # Layer 2: Query rewrite (только для коротких запросов)
        search_query = user_message
        if ENABLE_REWRITE and len(user_message.split()) <= 8:
            try:
                search_query = await rewrite_query(user_message, self.llm)
            except Exception as e:
                logger.warning("rewrite_skipped", err=str(e))

        # Layer 3a: RAG search
        rag_results = self.rag.search(search_query)
        log.rag_results_count = len(rag_results)
        log.rag_top_score = rag_results[0]["score"] if rag_results else 0.0

        # Layer 3b: Web search — решаем стоит ли идти в Tavily и куда (бренд/общий).
        # Принцип: content-фильтр = единственный запрет; всё остальное — отвечаем
        # полноценно. Web search берём только когда RAG слабый ИЛИ нужна свежесть
        # (акции/погода/курс). Для Евроторг-тем → фильтр по доменам, иначе → общий.
        web_results: list[dict] = []
        top_score = rag_results[0]["score"] if rag_results else 0.0
        fresh_promo = needs_fresh_promo(user_message)
        fresh_data = needs_fresh_data(user_message)
        eurotorg_q = is_eurotorg_question(user_message)

        rag_weak = len(rag_results) < WEB_FALLBACK_MIN_RESULTS or top_score < WEB_FALLBACK_MIN_SCORE
        # Идём в web если: (а) акции Евроторга; (б) нужны свежие данные; (в) RAG слабый.
        need_web = self.web.enabled and (fresh_promo or fresh_data or rag_weak)

        if need_web:
            try:
                # Приоритеты:
                #   1. Явный promo-сигнал (акция/скидка) — всегда promo-ветка
                #   2. fresh_data (погода/курс/новости без явного promo) — общий интернет
                #   3. fresh_promo по временному маркеру (сегодня/сейчас) — promo-ветка
                #   4. eurotorg_q — по доменам Евроторга
                #   5. всё остальное — общий интернет
                strong_promo = has_strong_promo_signal(user_message)
                if fresh_data and not strong_promo:
                    # Свежие данные (погода/курс/новости): общий интернет, без бренд-фильтра.
                    web_query = user_message.replace("?", "").strip()
                    use_eurotorg_domains = False
                    reason = "fresh_data"
                elif fresh_promo:
                    # Promo: по доменам Евроторга с нормализацией «Евроопте» → «Евроопт».
                    import re as _re
                    base = _re.sub(r"[?!]+", "", user_message)
                    base = _re.sub(r"\b[Ее]врооп\w*", "Евроопт", base)
                    web_query = base.strip()
                    if "евроопт" not in web_query.lower():
                        web_query = f"{web_query} Евроопт"
                    if "акци" not in web_query.lower() and "скидк" not in web_query.lower():
                        web_query = f"акции {web_query}"
                    use_eurotorg_domains = True
                    reason = "fresh_promo"
                elif eurotorg_q:
                    # Слабый RAG + вопрос про Евроторг: по доменам Евроторга с rewritten query.
                    web_query = search_query
                    use_eurotorg_domains = True
                    reason = "weak_rag_eurotorg"
                else:
                    # Слабый RAG + общий вопрос: общий интернет.
                    web_query = user_message.replace("?", "").strip()
                    use_eurotorg_domains = False
                    reason = "weak_rag_general"

                web_results = self.web.search(
                    web_query,
                    max_results=3,
                    include_general=not use_eurotorg_domains,
                )
                if web_results:
                    logger.info(
                        "web_fallback_used",
                        query=web_query[:60],
                        results=len(web_results),
                        reason=reason,
                        domains="eurotorg" if use_eurotorg_domains else "general",
                    )
            except Exception as e:
                logger.warning("web_fallback_error", err=str(e))
                web_results = []

        # Контекст собирается через санитайзер: XML-теги + нейтрализация injection
        kb_block = build_kb_block(rag_results)
        web_block = build_web_block(web_results[:3]) if web_results else ""
        context = kb_block + ("\n\n" + web_block if web_block else "")

        # Get relevant promotions
        relevant_promos = self.promotions.get_relevant_promotions(user_message)
        if not relevant_promos:
            relevant_promos = self.promotions.get_top_promotions(limit=3)
        log.promotions_shown = len(relevant_promos)
        promos_text = self.promotions.format_promotions(relevant_promos)

        # Layer 3: LLM generation with history
        system = SYSTEM_PROMPT.format(context=context, promotions=promos_text)

        # Get chat history for this user
        chat_history = self.history.get(user_id)

        try:
            response: LLMResponse = await self.llm.generate(
                system, user_message, history=chat_history
            )

            # Save to history
            self.history.add(user_id, "user", user_message)
            self.history.add(user_id, "assistant", response.text)

            log.bot_response = response.text
            log.llm_provider = type(self.llm).__name__
            log.llm_model = response.model
            log.input_tokens = response.input_tokens
            log.output_tokens = response.output_tokens
            log.response_time_ms = int((time.monotonic() - start_time) * 1000)
            interaction_logger.log_request(log)
            return response.text
        except Exception as e:
            error_msg = (
                "Извините, произошла временная ошибка. "
                "Пожалуйста, попробуйте ещё раз через несколько секунд. 🙏"
            )
            log.error = str(e)
            log.bot_response = error_msg
            log.response_time_ms = int((time.monotonic() - start_time) * 1000)
            interaction_logger.log_request(log)
            logger.error("llm_error", user_id=user_id, error=str(e))
            return error_msg
