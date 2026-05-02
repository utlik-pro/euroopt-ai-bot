import os
import time
import structlog

from src.filters.content_filter import check_content
from src.filters.pii_filter import mask_pii
from src.filters.prompt_sanitizer import build_kb_block, build_web_block
from src.llm.adapter import (
    get_llm_provider_with_fallback,
    GenerationOverrides,
    LLMResponse,
)
from src.config import settings
from src.llm.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_LITE
from src.rag.engine import RAGEngine
from src.promotions.engine import PromotionEngine
from src.chat_history import ChatHistory
from src.monitoring.logger import interaction_logger, RequestLog
from src.search.web import get_web_search
from src.search.query_rewriter import rewrite_query
from src.search.typo_normalizer import normalize_typos
from src.search.nbrb import detect_currencies, format_rates_block

# v2 quality components — опциональные, включаются флагами env
from src.canonical import CanonicalMatcher
from src.router import IntentRouter, Intent, detect_brand, detect_city
from src.search.query_normalizer import normalize_query
from src.verify import GroundingVerifier
from src.promotions.mechanic_detector import MechanicDetector
from src.cache import ResponseCache
from src.postprocess import SourceTagger

logger = structlog.get_logger()

ENABLE_REWRITE = os.environ.get("ENABLE_QUERY_REWRITE", "true").lower() == "true"
WEB_FALLBACK_MIN_RESULTS = int(os.environ.get("WEB_FALLBACK_MIN_RESULTS", "2"))
WEB_FALLBACK_MIN_SCORE = float(os.environ.get("WEB_FALLBACK_MIN_SCORE", "0.60"))

# v2 quality flags. По умолчанию выключены — старое поведение сохраняется.
# Включать постепенно в проде через .env, чтобы можно было откатиться.
ENABLE_CANONICAL = os.environ.get("ENABLE_CANONICAL_ANSWERS", "false").lower() == "true"
ENABLE_INTENT_ROUTER = os.environ.get("ENABLE_INTENT_ROUTER", "false").lower() == "true"
ENABLE_QUERY_NORMALIZER = os.environ.get("ENABLE_QUERY_NORMALIZER", "false").lower() == "true"
ENABLE_GROUNDING_VERIFY = os.environ.get("ENABLE_GROUNDING_VERIFY", "false").lower() == "true"
GROUNDING_AUTO_FIX = os.environ.get("GROUNDING_AUTO_FIX", "false").lower() == "true"
# Фильтр RAG по бренду/городу (для блока «магазины»). Закрывает 24.04 P1:
# вопрос про «Евроопт» не должен подмешивать «Хит» и наоборот.
ENABLE_BRAND_FILTER = os.environ.get("ENABLE_BRAND_FILTER", "false").lower() == "true"
# Подмешивание описания механики акции в контекст LLM (Еврошок / Цены вниз / 1+1).
# Закрывает 24.04 P1 «не отличает Еврошок от других механик».
ENABLE_MECHANIC_CONTEXT = os.environ.get("ENABLE_MECHANIC_CONTEXT", "false").lower() == "true"
# Общий кэш ответов «нормализованный вопрос → ответ» с TTL.
# Закрывает 24.04 P2: одинаковый вопрос → одинаковый ответ.
# Per-user долговременная память НЕ делается — это нарушение ТЗ §2 (PII).
ENABLE_RESPONSE_CACHE = os.environ.get("ENABLE_RESPONSE_CACHE", "false").lower() == "true"
# Маркер источника в ответах на рецепты («📋 Из базы / 🌐 Из интернета»).
# Закрывает 24.04 P3: «нужна большая прозрачность источника».
ENABLE_SOURCE_TAGGER = os.environ.get("ENABLE_SOURCE_TAGGER", "false").lower() == "true"
# Явная PII-рамка в начале ответа на сообщения с обнаруженными ПД.
# Закрывает 25.04 п. 6.4: «ответ должен начинаться с безопасной рамки
# "Я не могу обрабатывать персональные данные в чате"».
ENABLE_PII_FRAME = os.environ.get("ENABLE_PII_FRAME", "false").lower() == "true"

PII_FRAME_PREFIX = (
    "🔒 Я не могу обрабатывать персональные данные в чате. "
    "Пожалуйста, не отправляйте номера телефонов, карт, документов, "
    "адреса и ФИО.\n\n"
)


def _has_pii_frame_signal(text: str) -> bool:
    """Уже ли в ответе есть PII-рамка в начале."""
    if not text:
        return False
    head = text.lower().lstrip()[:200]
    return any(
        sig in head for sig in (
            "не могу обрабатыв",
            "не могу принимать",
            "не могу повторять",
            "не сохраня",
            "персональные данные",
        )
    )

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
        # v2 quality components — инициализируем всегда, активируем флагами в process()
        self.canonical = CanonicalMatcher() if ENABLE_CANONICAL else None
        self.intent_router = IntentRouter() if ENABLE_INTENT_ROUTER else None
        self.grounding_verifier = (
            GroundingVerifier(auto_fix=GROUNDING_AUTO_FIX)
            if ENABLE_GROUNDING_VERIFY
            else None
        )
        self.mechanic_detector = MechanicDetector() if ENABLE_MECHANIC_CONTEXT else None
        self.response_cache = ResponseCache() if ENABLE_RESPONSE_CACHE else None
        self.source_tagger = SourceTagger() if ENABLE_SOURCE_TAGGER else None

    async def process(self, user_message: str, user_id: int) -> str:
        """Process user message through the 4-layer pipeline.

        Layer 1: Content filter (block politics, religion, competitors)
        Layer 2: Query rewrite (short → expanded)
        Layer 3: RAG search + web search fallback
        Layer 4: LLM generation with chat history
        """
        start_time = time.monotonic()
        # ДС №1 к Договору 2703/26-01, п. 2.1.1: в лог user_message пишем УЖЕ
        # маскированным — сырые ПДн пользователя не должны попадать в JSONL.
        masked_user_message, pii_in_types = mask_pii(user_message)
        log = RequestLog(
            user_id=user_id,
            user_message=masked_user_message,
            pii_detected_input=pii_in_types,
        )
        if pii_in_types:
            logger.info(
                "pii_detected_input",
                user_id=user_id,
                types=pii_in_types,
                count=len(pii_in_types),
            )

        # Layer 1: Content filter (по маскированному тексту — фильтру ПДн не нужны,
        # а заблокированные темы всё равно детектируются по ключевым словам).
        is_allowed, refusal = check_content(masked_user_message)
        if not is_allowed:
            log.content_filtered = True
            log.filter_reason = "blocked"
            log.bot_response = refusal
            log.response_time_ms = int((time.monotonic() - start_time) * 1000)
            interaction_logger.log_request(log)
            return refusal

        # После контент-фильтра ВСЯ дальнейшая работа — с masked_user_message.
        # Приложение №1 к ДС №1: маскирование ДО передачи во внешние сервисы
        # (LLM, web search). Оригинал пользователь видит у себя, мы его нигде не
        # сохраняем и никуда не отправляем.
        user_message = masked_user_message

        # v2 Layer 1.45: Response cache — общий кэш «нормализованный
        # вопрос → ответ» с TTL=1ч (по умолчанию). На одинаковый вопрос
        # двух разных пользователей в течение TTL отдаём один и тот же
        # ответ. Закрывает 24.04 P2 (расхождение Наташа/Лёша).
        # ВАЖНО: кэш ОБЩИЙ, не привязан к user_id; per-user долговременная
        # память запрещена ТЗ §2 (PII).
        if self.response_cache is not None:
            cached = self.response_cache.get(user_message)
            if cached is not None:
                # Если в исходном были ПДн — добавляем PII-рамку и к кэш-хиту тоже
                cached_out = cached
                if (
                    ENABLE_PII_FRAME
                    and pii_in_types
                    and not _has_pii_frame_signal(cached_out)
                ):
                    cached_out = PII_FRAME_PREFIX + cached_out
                self.history.add(user_id, "user", user_message)
                self.history.add(user_id, "assistant", cached_out)
                log.bot_response = cached_out
                log.llm_provider = "cache"
                log.llm_model = "response_cache"
                log.response_time_ms = int((time.monotonic() - start_time) * 1000)
                interaction_logger.log_request(log)
                return cached_out

        # v2 Layer 1.5: Canonical answers — гарантия 100% повторяемости
        # на критичных FAQ. Если запрос совпадает с известным шаблоном
        # (вход в ЛК, утеря карты, оплата 99% и т.п.) — отдаём готовый
        # ответ, минуя RAG/LLM. Это закрывает претензии заказчика по
        # повторяемости (отчёт 24.04, P2).
        if self.canonical is not None:
            canonical_hit = self.canonical.match(user_message)
            if canonical_hit is not None:
                answer = canonical_hit.answer
                # PII-рамка перед каноническим ответом, если в исходном
                # сообщении были ПД. Заказчик 25.04 п. 6.4.
                if (
                    ENABLE_PII_FRAME
                    and pii_in_types
                    and not _has_pii_frame_signal(answer)
                ):
                    answer = PII_FRAME_PREFIX + answer
                self.history.add(user_id, "user", user_message)
                self.history.add(user_id, "assistant", answer)
                log.bot_response = answer
                log.llm_provider = "canonical"
                log.llm_model = canonical_hit.id
                log.response_time_ms = int((time.monotonic() - start_time) * 1000)
                interaction_logger.log_request(log)
                logger.info(
                    "canonical_answer_served",
                    user_id=user_id,
                    canonical_id=canonical_hit.id,
                    category=canonical_hit.category,
                )
                return answer

        # v2 Layer 1.6: Intent routing — определяем тип запроса для адаптивных
        # параметров генерации (temperature, require_rag, allow_web).
        intent_result = None
        if self.intent_router is not None:
            intent_result = self.intent_router.classify(user_message)
            logger.info(
                "intent_classified",
                user_id=user_id,
                intent=intent_result.intent.value,
                confidence=intent_result.confidence,
                temperature=intent_result.temperature,
            )

        # Layer 2a: нормализация опечаток для search (оригинал user_message не меняем!)
        # «скидкиии» → «скидки», «едаставка» → «едоставка» и т.п.
        normalized = normalize_typos(user_message)
        if normalized != user_message:
            logger.info("typo_normalized", original=user_message[:60], normalized=normalized[:60])

        # v2 Layer 2a': канонизация запроса для повторяемости.
        # Развёртывание синонимов и сокращений: «ЛК» → «личный кабинет»,
        # «магазины в Лиде» → «магазины Лида». Разные формулировки одного
        # и того же вопроса дают одинаковые embeddings → одинаковый ранкинг
        # → одинаковый ответ. Отчёт 24.04 P2: повторяемость.
        if ENABLE_QUERY_NORMALIZER:
            canonized = normalize_query(normalized, strip_stopwords=False)
            if canonized and canonized != normalized.lower():
                logger.info(
                    "query_canonized",
                    original=normalized[:60],
                    canonized=canonized[:60],
                )
                normalized = canonized

        # Layer 2b: Query rewrite (только для коротких запросов)
        search_query = normalized
        if ENABLE_REWRITE and len(normalized.split()) <= 8:
            try:
                search_query = await rewrite_query(normalized, self.llm)
            except Exception as e:
                logger.warning("rewrite_skipped", err=str(e))

        # Layer 3a: RAG search.
        # v2: для запросов про магазины применяем brand/city фильтр —
        # «Евроопт» и «Хит» не должны мешаться в одной выдаче.
        rag_brand: str | None = None
        rag_city: str | None = None
        if ENABLE_BRAND_FILTER and intent_result is not None and intent_result.intent == Intent.STORES:
            rag_brand = detect_brand(user_message)
            rag_city = detect_city(user_message)
            if rag_brand or rag_city:
                logger.info(
                    "stores_filter",
                    brand=rag_brand or "any",
                    city=rag_city or "any",
                    query=user_message[:60],
                )

        rag_results = self.rag.search(search_query, brand=rag_brand, city=rag_city)
        log.rag_results_count = len(rag_results)
        log.rag_top_score = rag_results[0]["score"] if rag_results else 0.0

        # v2 Layer 3a': детектируем конкретную механику акции из вопроса.
        # Если найдена — потом подмешаем её описание в kb_block, чтобы LLM
        # не путала Еврошок со «Спеццены», 1+1 с бонусами Еплюс и т.д.
        detected_mechanic = None
        if self.mechanic_detector is not None:
            detected_mechanic = self.mechanic_detector.detect(user_message)

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
                    # Для курсов валют форсим контекст Беларуси + BYN, чтобы Tavily
                    # нашёл нужные цифры (а не курс к RUB).
                    low = web_query.lower()
                    if "курс" in low and any(c in low for c in ["доллар", "евро", "юан", "usd", "eur"]):
                        if "беларус" not in low and "byn" not in low and "бел руб" not in low:
                            web_query = f"{web_query} Беларусь BYN nbrb"
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

                # ДС №1 п. 2.1.2: обезличивание запроса ПЕРЕД внешним поиском.
                # rewrite_query мог раскрыть что-то из маскированного входа,
                # поэтому прогоняем ещё раз. Маскер идемпотентен: [телефон]
                # на входе остаётся [телефон] на выходе.
                web_query, web_pii_types = mask_pii(web_query)
                if web_pii_types:
                    logger.info(
                        "pii_masked_web_query",
                        types=web_pii_types,
                        count=len(web_pii_types),
                    )

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

        # Если пользователь спросил про курс валют — подмешиваем в контекст
        # точные цифры от НБРБ (прямой API, детерминированно).
        nbrb_block = ""
        currencies = detect_currencies(user_message)
        if currencies:
            try:
                nbrb_block = format_rates_block(currencies)
                if nbrb_block:
                    logger.info("nbrb_rates_added", currencies=currencies)
            except Exception as e:
                logger.warning("nbrb_error", err=str(e))

        # Контекст собирается через санитайзер: XML-теги + нейтрализация injection
        kb_block = build_kb_block(rag_results)
        web_block = build_web_block(web_results[:3]) if web_results else ""

        # v2: подмешиваем описание упомянутой механики в kb_block — это
        # каноническое описание из data/promotions/mechanics.json, гарантирует
        # что LLM не выдумает определение «Еврошока» и не подменит его «Спеццены».
        if detected_mechanic is not None:
            mech_block = (
                f"\n<mechanic_definition id=\"{detected_mechanic.id}\" "
                f"network=\"{detected_mechanic.network}\">\n"
                f"{detected_mechanic.format_brief()}\n"
                f"</mechanic_definition>"
            )
            kb_block = kb_block + mech_block
            logger.info(
                "mechanic_context_added",
                id=detected_mechanic.id,
                name=detected_mechanic.name,
            )

        # Собираем <web_context> с NBRB + Tavily (если есть).
        if nbrb_block or web_block:
            sources = []
            if nbrb_block:
                sources.append(nbrb_block)
            if web_block:
                # web_block уже обёрнут в <web_context>...</web_context> — вынимаем содержимое
                inner = web_block.replace("<web_context>", "").replace("</web_context>", "").strip()
                if inner:
                    sources.append(inner)
            context = kb_block + "\n\n<web_context>\n" + "\n".join(sources) + "\n</web_context>"
        else:
            context = kb_block

        # Get relevant promotions
        relevant_promos = self.promotions.get_relevant_promotions(user_message)
        if not relevant_promos:
            relevant_promos = self.promotions.get_top_promotions(limit=3)
        log.promotions_shown = len(relevant_promos)
        promos_text = self.promotions.format_promotions(relevant_promos)

        # Layer 3: LLM generation with history
        # Переключатель промпта: USE_LITE_PROMPT=true ИЛИ LLM_PROVIDER=groq → SYSTEM_PROMPT_LITE
        # (Groq free tier 8K TPM не выдержит полный промпт ~10K токенов).
        # Откат: убрать env-переменную или сменить LLM_PROVIDER → автоматически вернётся SYSTEM_PROMPT.
        _use_lite = (
            os.environ.get("USE_LITE_PROMPT", "").lower() in ("true", "1", "yes")
            or settings.llm_provider == "groq"
        )
        prompt_template = SYSTEM_PROMPT_LITE if _use_lite else SYSTEM_PROMPT
        system = prompt_template.format(context=context, promotions=promos_text)

        # Get chat history for this user
        chat_history = self.history.get(user_id)

        # v2: подбираем overrides на основе intent — фактологические запросы
        # получают temperature=0.0 (детерминизм + повторяемость), творческие
        # (рецепты, smalltalk) — повыше. Если intent_router выключен — None,
        # тогда адаптер использует settings.llm_temperature.
        gen_overrides: GenerationOverrides | None = None
        if intent_result is not None:
            gen_overrides = GenerationOverrides(
                temperature=intent_result.temperature,
                # seed для повторяемости (поддерживается DeepSeek/OpenAI)
                seed=42 if intent_result.deterministic else None,
            )

        try:
            # Сигнатуру вызова сохраняем минимальной (history был и раньше,
            # overrides добавляем только когда intent_router реально что-то
            # вернул) — это не ломает старые тесты с моками LLM.
            if gen_overrides is not None:
                response: LLMResponse = await self.llm.generate(
                    system, user_message, history=chat_history, overrides=gen_overrides,
                )
            else:
                response = await self.llm.generate(
                    system, user_message, history=chat_history,
                )

            # v2: Grounding verify — после генерации проверяем, не появилось ли
            # в ответе фактов (телефонов, цен, времени), которых нет в источниках.
            # При auto_fix=True заменяем безопасными формулировками.
            if self.grounding_verifier is not None:
                vr = self.grounding_verifier.verify(
                    response.text, kb_text=kb_block, web_text=web_block,
                )
                if not vr.is_grounded:
                    log.filter_reason = (
                        f"grounding:{','.join(sorted({i.kind for i in vr.issues}))}"
                    )
                    if GROUNDING_AUTO_FIX and vr.cleaned_text and vr.cleaned_text != response.text:
                        logger.info(
                            "grounding_auto_fixed",
                            user_id=user_id,
                            issues=len(vr.issues),
                        )
                        response.text = vr.cleaned_text

            # Ответ LLM НЕ маскируется: телефоны магазинов, адреса, ФИО
            # публичных лиц — публичная информация, должна быть в ответе.
            # ПДн пользователя физически не могут эхнуться — LLM видела
            # только замаскированный вход (masked_user_message) и RAG/web.

            # v2: PII-рамка в начале ответа LLM, если во входе были ПД.
            # Закрывает 25.04 п. 6.4 «явно показывать срабатывание PII-фильтра».
            if (
                ENABLE_PII_FRAME
                and pii_in_types
                and not _has_pii_frame_signal(response.text)
            ):
                response.text = PII_FRAME_PREFIX + response.text

            # v2: маркер источника для рецептов — пользователь видит,
            # пришёл ответ из базы Евроопта или из общего интернета.
            # Применяется только для intent=RECIPES, чтобы не загромождать
            # FAQ/Stores/Promo (там источники и так строгие).
            if (
                self.source_tagger is not None
                and intent_result is not None
                and intent_result.intent == Intent.RECIPES
            ):
                tag_result = self.source_tagger.tag(
                    response.text,
                    rag_results=rag_results,
                    web_results=web_results,
                )
                response.text = tag_result.text

            # history также хранит маскированный user_message (чтобы в следующих
            # ходах LLM не получила сырые ПДн из прошлых сообщений).
            self.history.add(user_id, "user", user_message)
            self.history.add(user_id, "assistant", response.text)

            # v2: кладём в общий кэш для повторяемости. PII-сообщения и
            # запросы с временными маркерами кэш сам пропустит.
            if self.response_cache is not None:
                self.response_cache.put(user_message, response.text)

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
            # str(e) может содержать эхо user_message из LLM-SDK
            # (например, «invalid request: …{текст}…»). Маскируем.
            safe_error, _ = mask_pii(str(e))
            log.error = safe_error
            log.bot_response = error_msg
            log.response_time_ms = int((time.monotonic() - start_time) * 1000)
            interaction_logger.log_request(log)
            logger.error("llm_error", user_id=user_id, error=safe_error)
            return error_msg
