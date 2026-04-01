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
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        env="EMBEDDING_MODEL",
    )
    rag_top_k: int = Field(default=5, env="RAG_TOP_K")
    rag_score_threshold: float = Field(default=0.3, env="RAG_SCORE_THRESHOLD")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
