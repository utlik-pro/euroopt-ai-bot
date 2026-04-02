import time
import structlog

from src.filters.content_filter import check_content
from src.llm.adapter import get_llm_provider_with_fallback, LLMResponse
from src.llm.prompts import SYSTEM_PROMPT
from src.rag.engine import RAGEngine
from src.promotions.engine import PromotionEngine
from src.monitoring.logger import interaction_logger, RequestLog

logger = structlog.get_logger()


class Pipeline:
    def __init__(self):
        self.llm = get_llm_provider_with_fallback()
        self.rag = RAGEngine()
        self.promotions = PromotionEngine()

    async def process(self, user_message: str, user_id: int) -> str:
        """Process user message through the 3-layer pipeline.

        Layer 1: Content filter (block politics, religion, competitors)
        Layer 2: RAG search (if query relates to Euroopt)
        Layer 3: LLM generation

        All interactions are logged for daily reports and analytics.
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

        # Layer 2: RAG search
        rag_results = self.rag.search(user_message)
        log.rag_results_count = len(rag_results)
        log.rag_top_score = rag_results[0]["score"] if rag_results else 0.0
        context = "\n\n".join([r["text"] for r in rag_results]) if rag_results else "Нет релевантной информации в базе знаний."

        # Get relevant promotions
        relevant_promos = self.promotions.get_relevant_promotions(user_message)
        if not relevant_promos:
            relevant_promos = self.promotions.get_top_promotions(limit=3)
        log.promotions_shown = len(relevant_promos)
        promos_text = self.promotions.format_promotions(relevant_promos)

        # Layer 3: LLM generation
        system = SYSTEM_PROMPT.format(context=context, promotions=promos_text)

        try:
            response: LLMResponse = await self.llm.generate(system, user_message)
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
