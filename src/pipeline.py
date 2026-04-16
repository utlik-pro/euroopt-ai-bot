import os
import time
import structlog

from src.filters.content_filter import check_content
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
        context_parts = [r["text"] for r in rag_results]

        # Layer 3b: Web search fallback (если RAG вернул мало)
        web_context = ""
        if self.web.enabled and len(rag_results) < WEB_FALLBACK_MIN_RESULTS:
            try:
                web_results = self.web.search(search_query, max_results=3)
                if web_results:
                    web_context = "\n\n---\nАктуальная информация с сайтов Евроторга:\n" + "\n".join(
                        f"[{r['title']}]({r['url']})\n{r['content'][:500]}"
                        for r in web_results[:3]
                    )
                    logger.info("web_fallback_used", query=search_query[:50], results=len(web_results))
            except Exception as e:
                logger.warning("web_fallback_error", err=str(e))

        context = "\n\n".join(context_parts) if context_parts else "Нет релевантной информации в базе знаний."
        if web_context:
            context += web_context

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
