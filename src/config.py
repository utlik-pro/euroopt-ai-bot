from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")

    # LLM — активный провайдер
    llm_provider: str = Field(default="anthropic", env="LLM_PROVIDER")
    llm_model: str = Field(default="claude-sonnet-4-20250514", env="LLM_MODEL")
    llm_temperature: float = Field(default=0.3, env="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=1024, env="LLM_MAX_TOKENS")

    # LLM API Keys — заполнить ключ активного провайдера
    anthropic_api_key: str = Field(default="", env="ANTHROPIC_API_KEY")
    deepseek_api_key: str = Field(default="", env="DEEPSEEK_API_KEY")
    glm_api_key: str = Field(default="", env="GLM_API_KEY")
    google_api_key: str = Field(default="", env="GOOGLE_API_KEY")
    openai_api_key: str = Field(default="", env="OPENAI_API_KEY")
    openrouter_api_key: str = Field(default="", env="OPENROUTER_API_KEY")
    atlas_api_key: str = Field(default="", env="ATLAS_API_KEY")
    qwen_api_key: str = Field(default="", env="QWEN_API_KEY")
    yandexgpt_api_key: str = Field(default="", env="YANDEXGPT_API_KEY")
    yandexgpt_folder_id: str = Field(default="", env="YANDEXGPT_FOLDER_ID")
    gigachat_auth_key: str = Field(default="", env="GIGACHAT_AUTH_KEY")

    # API-релей (для санкционных API из РБ)
    # URL релей-сервиса на Render/Railway (например: https://euroopt-llm-relay.onrender.com)
    relay_url: str = Field(default="", env="RELAY_URL")
    relay_secret: str = Field(default="", env="RELAY_SECRET")
    # Прокси (альтернатива релею)
    llm_proxy_url: str = Field(default="", env="LLM_PROXY_URL")
    # Таймауты
    llm_timeout: int = Field(default=60, env="LLM_TIMEOUT")

    # RAG
    chroma_persist_dir: str = Field(default="./data/chroma", env="CHROMA_PERSIST_DIR")
    embedding_model: str = Field(
        default="intfloat/multilingual-e5-base",
        env="EMBEDDING_MODEL",
    )
    rag_top_k: int = Field(default=5, env="RAG_TOP_K")
    rag_score_threshold: float = Field(default=0.3, env="RAG_SCORE_THRESHOLD")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # Access control (MVP whitelist + approval flow)
    whitelist_enabled: bool = Field(default=True, env="WHITELIST_ENABLED")
    admin_user_ids: str = Field(default="", env="ADMIN_USER_IDS")
    # @username внутренней команды — автоодобрение при первом контакте (залочится на user_id)
    pre_approved_usernames: str = Field(default="", env="PRE_APPROVED_USERNAMES")
    rate_limit_per_hour: int = Field(default=20, env="RATE_LIMIT_PER_HOUR")
    access_denied_message: str = Field(
        default="🔒 Доступ к AI-помощнику ограничен. Ваша заявка отклонена.",
        env="ACCESS_DENIED_MESSAGE",
    )
    access_pending_message: str = Field(
        default=(
            "🕐 Заявка на доступ принята. Ожидайте подтверждения администратора.\n\n"
            "Если срочно — напишите @dmitryutlik."
        ),
        env="ACCESS_PENDING_MESSAGE",
    )
    rate_limit_message: str = Field(
        default="⏳ Слишком много сообщений. Попробуйте через час.",
        env="RATE_LIMIT_MESSAGE",
    )
    non_private_ignore: bool = Field(default=True, env="NON_PRIVATE_IGNORE")

    # Web search (Tavily) — fallback когда RAG слабый
    tavily_api_key: str = Field(default="", env="TAVILY_API_KEY")
    web_search_enabled: bool = Field(default=False, env="WEB_SEARCH_ENABLED")
    web_search_domains: str = Field(
        default="evroopt.by,hitdiscount.by,groshyk.by,igra.evroopt.by,eplus.by,e-dostavka.by",
        env="WEB_SEARCH_DOMAINS",
    )
    web_search_cache_ttl: int = Field(default=21600, env="WEB_SEARCH_CACHE_TTL")
    web_search_max_per_day: int = Field(default=500, env="WEB_SEARCH_MAX_PER_DAY")
    web_fallback_min_results: int = Field(default=2, env="WEB_FALLBACK_MIN_RESULTS")
    web_fallback_min_score: float = Field(default=0.60, env="WEB_FALLBACK_MIN_SCORE")
    enable_query_rewrite: bool = Field(default=True, env="ENABLE_QUERY_REWRITE")

    def pre_approved_usernames_set(self) -> set[str]:
        return {u.strip().lstrip("@").lower() for u in self.pre_approved_usernames.split(",") if u.strip()}

    def admin_user_ids_set(self) -> set[int]:
        result = set()
        for x in self.admin_user_ids.split(","):
            x = x.strip()
            if x.isdigit():
                result.add(int(x))
        return result

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
