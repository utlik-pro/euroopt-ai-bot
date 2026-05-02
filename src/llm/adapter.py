"""LLM-адаптер с поддержкой всех моделей из расчёта стоимости Евроторга.

Модели (от дешёвых к дорогим, стоимость за 1 запрос):
- GLM-4-Flash     < 0,01 коп.   → провайдер: glm
- Gemini 2.0 Flash  0,11 коп.   → провайдер: gemini
- GLM-4            0,07 коп.    → провайдер: glm
- GPT-4o-mini      0,17 коп.    → провайдер: openai
- DeepSeek V3      0,30 коп.    → провайдер: deepseek
- DeepSeek R1      0,61 коп.    → провайдер: deepseek
- Claude Haiku 3.5 1,04 коп.    → провайдер: anthropic
- Gemini 1.5 Pro   1,38 коп.    → провайдер: gemini
- Claude Sonnet 4  3,90 коп.    → провайдер: anthropic

Дополнительные (не в расчёте, но обсуждались):
- qwen (Qwen 2.5) — кандидат для on-premise
- yandexgpt — серверы в РФ/РБ
- gigachat (Сбер) — запасная

Переключение между моделями — через .env (LLM_PROVIDER + LLM_MODEL).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import anthropic
from openai import AsyncOpenAI
import httpx

from src.config import settings


def _get_http_client() -> httpx.AsyncClient:
    """HTTP-клиент с поддержкой прокси для работы из РБ."""
    proxy = settings.llm_proxy_url if settings.llm_proxy_url else None
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=settings.llm_timeout,
        verify=True,
    )


def _get_relay_base_url(provider: str) -> str | None:
    """Если настроен релей — вернуть URL для провайдера.

    Релей на Render проксирует:
      RELAY_URL/v1/anthropic/* → api.anthropic.com/*
      RELAY_URL/v1/openai/*   → api.openai.com/*
      RELAY_URL/v1/gemini/*   → generativelanguage.googleapis.com/*
    """
    if not settings.relay_url:
        return None
    return f"{settings.relay_url.rstrip('/')}/v1/{provider}"


def _get_relay_headers() -> dict:
    """Заголовки для авторизации на релее."""
    if settings.relay_secret:
        return {"Authorization": f"Bearer {settings.relay_secret}"}
    return {}


# Провайдеры, которые ТРЕБУЮТ прокси/релей из РБ
_SANCTIONED_PROVIDERS = {"anthropic", "openai", "gemini"}


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class GenerationOverrides:
    """Опциональные override-параметры для конкретного вызова.

    Используются Intent Router'ом: для фактических запросов temperature=0.0,
    для творческих — выше. seed (где провайдер поддерживает) даёт ещё чуть
    больше воспроизводимости.
    """

    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None


def _resolve_temperature(overrides: GenerationOverrides | None) -> float:
    if overrides is not None and overrides.temperature is not None:
        return overrides.temperature
    return settings.llm_temperature


def _resolve_max_tokens(overrides: GenerationOverrides | None) -> int:
    if overrides is not None and overrides.max_tokens is not None:
        return overrides.max_tokens
    return settings.llm_max_tokens


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> LLMResponse:
        pass


class ClaudeProvider(LLMProvider):
    """Anthropic Claude — основная модель MVP.

    ВНИМАНИЕ: Anthropic заблокирован из РБ.
    Работает через: 1) API-релей на Render, или 2) HTTP-прокси.
    """

    def __init__(self):
        kwargs = {"api_key": settings.anthropic_api_key}

        # Приоритет: релей > прокси > напрямую
        relay_url = _get_relay_base_url("anthropic")
        if relay_url:
            kwargs["base_url"] = relay_url
            kwargs["default_headers"] = _get_relay_headers()
        elif settings.llm_proxy_url:
            kwargs["http_client"] = _get_http_client()

        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> LLMResponse:
        # Anthropic пока не поддерживает seed-параметр стабильно — игнорируем его.
        messages: list[dict] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        response = await self.client.messages.create(
            model=settings.llm_model,
            max_tokens=_resolve_max_tokens(overrides),
            temperature=_resolve_temperature(overrides),
            system=system_prompt,
            messages=messages,
        )
        return LLMResponse(
            text=response.content[0].text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAICompatibleProvider(LLMProvider):
    """Базовый класс для провайдеров с OpenAI-совместимым API.

    DeepSeek, GLM, Qwen и другие используют формат OpenAI Chat Completions.
    """

    def __init__(self, api_key: str, base_url: str, default_model: str,
                 needs_proxy: bool = False, relay_provider: str = ""):
        kwargs = {"api_key": api_key, "base_url": base_url}

        # Для санкционных провайдеров: релей > прокси > напрямую
        if needs_proxy and relay_provider:
            relay_url = _get_relay_base_url(relay_provider)
            if relay_url:
                kwargs["base_url"] = relay_url
                kwargs["default_headers"] = _get_relay_headers()
            elif settings.llm_proxy_url:
                kwargs["http_client"] = _get_http_client()

        self.client = AsyncOpenAI(**kwargs)
        self.default_model = default_model

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> LLMResponse:
        model = self.default_model if settings.llm_model.startswith("claude") else settings.llm_model
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        # OpenAI-compatible: большинство поддерживает seed — пробуем его подложить.
        # Если провайдер не поддерживает — он просто проигнорирует поле.
        kwargs: dict = dict(
            model=model,
            max_tokens=_resolve_max_tokens(overrides),
            temperature=_resolve_temperature(overrides),
            messages=messages,
        )
        if overrides is not None and overrides.seed is not None:
            kwargs["seed"] = overrides.seed
        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        usage = response.usage
        return LLMResponse(
            text=choice.message.content,
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek V3 (0,30 коп./запрос) и R1 (0,61 коп./запрос).

    Китайский провайдер — работает из РБ напрямую, без прокси.
    Модели: deepseek-chat (V3), deepseek-reasoner (R1).
    """

    def __init__(self):
        super().__init__(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            default_model="deepseek-chat",
            needs_proxy=False,
        )


class GLMProvider(OpenAICompatibleProvider):
    """Zhipu AI: glm-4.7-flash (БЕСПЛАТНАЯ!), glm-4.7, glm-4.6v и др.

    Китайский провайдер — работает из РБ напрямую, без прокси.
    Самые дешёвые модели. Без санкционных рисков (КНР).

    Base URL настраивается через GLM_BASE_URL:
      - https://open.bigmodel.cn/api/paas/v4 — прямо в КНР (default)
      - https://api.z.ai/api/paas/v4         — международный домен (быстрее из EU/РБ)
    """

    def __init__(self):
        super().__init__(
            api_key=settings.glm_api_key,
            base_url=settings.glm_base_url,
            default_model="glm-4.7-flash",  # default обновлён на свежую и БЕСПЛАТНУЮ модель
            needs_proxy=False,
        )


class GeminiProvider(OpenAICompatibleProvider):
    """Google Gemini 2.0 Flash (0,11 коп.) и 1.5 Pro (1,38 коп./запрос).

    Работает через API-релей на Render или прокси.
    Модели: gemini-2.0-flash, gemini-1.5-pro.
    """

    def __init__(self):
        super().__init__(
            api_key=settings.google_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            default_model="gemini-2.0-flash",
            needs_proxy=True,
            relay_provider="gemini",
        )


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI GPT-4o-mini (0,17 коп./запрос).

    Работает через API-релей на Render или прокси.
    Модели: gpt-4o-mini, gpt-4o.
    """

    def __init__(self):
        super().__init__(
            api_key=settings.openai_api_key,
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini",
            needs_proxy=True,
            relay_provider="openai",
        )


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter — доступ ко всем моделям через один API ключ.

    Без санкционных рисков (серверы в EU/US, но оплата криптой/картой).
    Модели: любые через OpenRouter (deepseek, claude, gemini, llama и т.д.)
    """

    def __init__(self):
        super().__init__(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            default_model="deepseek/deepseek-chat-v3-0324:free",
            needs_proxy=False,
        )


class AtlasCloudProvider(OpenAICompatibleProvider):
    """Atlas Cloud — агрегатор 300+ моделей, OpenAI-совместимый API.

    DeepSeek, Claude, GPT, Gemini, GLM, Qwen, Kimi — всё через один ключ.
    SLA 99.9%, SOC II compliance.
    """

    def __init__(self):
        super().__init__(
            api_key=settings.atlas_api_key,
            base_url="https://api.atlascloud.ai/v1",
            default_model="openai/gpt-4o-mini",
            needs_proxy=False,
        )


class QwenProvider(OpenAICompatibleProvider):
    """Alibaba Qwen 2.5 — кандидат для on-premise (Фаза 2).

    Китайский провайдер — работает из РБ напрямую, без прокси.
    Модели: qwen-plus, qwen-max, qwen-turbo.
    """

    def __init__(self):
        super().__init__(
            api_key=settings.qwen_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            default_model="qwen-plus",
            needs_proxy=False,
        )


class YandexGPTProvider(LLMProvider):
    """YandexGPT — серверы в РФ/РБ, хороший русский.

    Использует собственный REST API (не OpenAI-совместимый).
    Переключение Claude → YandexGPT: 2–3 рабочих дня.
    """

    def __init__(self):
        self.api_key = settings.yandexgpt_api_key
        self.folder_id = settings.yandexgpt_folder_id

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> LLMResponse:
        model = settings.llm_model if "yandexgpt" in settings.llm_model else "yandexgpt-lite"
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }
        msgs: list[dict] = [{"role": "system", "text": system_prompt}]
        if history:
            for h in history:
                msgs.append({"role": h.get("role", "user"), "text": h.get("content", "")})
        msgs.append({"role": "user", "text": user_message})
        body = {
            "modelUri": f"gpt://{self.folder_id}/{model}",
            "completionOptions": {
                "stream": False,
                "temperature": _resolve_temperature(overrides),
                "maxTokens": str(_resolve_max_tokens(overrides)),
            },
            "messages": msgs,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        result = data["result"]
        alt = result["alternatives"][0]
        usage = result.get("usage", {})
        return LLMResponse(
            text=alt["message"]["text"],
            model=model,
            input_tokens=int(usage.get("inputTextTokens", 0)),
            output_tokens=int(usage.get("completionTokens", 0)),
        )


class GigaChatProvider(LLMProvider):
    """GigaChat (Сбер) — среднее качество, запасной вариант.

    Использует OAuth2 + REST API.
    """

    def __init__(self):
        self.auth_key = settings.gigachat_auth_key
        self._access_token: str | None = None

    async def _get_token(self) -> str:
        if self._access_token:
            return self._access_token
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                headers={
                    "Authorization": f"Basic {self.auth_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": "GIGACHAT_API_PERS"},
            )
            resp.raise_for_status()
            self._access_token = resp.json()["access_token"]
        return self._access_token

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> LLMResponse:
        token = await self._get_token()
        model = settings.llm_model if "GigaChat" in settings.llm_model else "GigaChat"
        msgs: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            msgs.extend(history)
        msgs.append({"role": "user", "content": user_message})
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            resp = await client.post(
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "temperature": _resolve_temperature(overrides),
                    "max_tokens": _resolve_max_tokens(overrides),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            text=choice["message"]["content"],
            model=data.get("model", model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


_PROVIDERS: dict[str, type[LLMProvider]] = {
    # Из расчёта стоимости Евроторга (9 моделей)
    "anthropic": ClaudeProvider,      # Claude Haiku 3.5, Sonnet 4
    "deepseek": DeepSeekProvider,     # DeepSeek V3, R1
    "glm": GLMProvider,              # GLM-4-Flash, GLM-4
    "gemini": GeminiProvider,        # Gemini 2.0 Flash, 1.5 Pro
    "openai": OpenAIProvider,        # GPT-4o-mini
    "openrouter": OpenRouterProvider, # Все модели через один ключ
    "atlas": AtlasCloudProvider,     # Atlas Cloud — 300+ моделей, SLA 99.9%
    # Дополнительные (обсуждались на встрече)
    "qwen": QwenProvider,            # Qwen 2.5 (on-premise кандидат)
    "yandexgpt": YandexGPTProvider,  # YandexGPT
    "gigachat": GigaChatProvider,    # GigaChat (Сбер)
}


# Порядок фоллбэка: если основной провайдер недоступен, пробуем следующий.
# Приоритет: несанкционные (работают из РБ без прокси) → санкционные (нужен прокси).
_FALLBACK_ORDER = ["atlas", "openrouter", "deepseek", "glm", "qwen", "gemini", "openai", "anthropic", "yandexgpt", "gigachat"]


def get_llm_provider(provider_name: str | None = None) -> LLMProvider:
    name = provider_name or settings.llm_provider
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {name}. Available: {list(_PROVIDERS)}")
    return cls()


def get_llm_provider_with_fallback(provider_name: str | None = None) -> LLMProvider:
    """Получить LLM-провайдер с автоматическим фоллбэком.

    Если основной провайдер санкционный и прокси не настроен, автоматически
    переключается на несанкционный. На несанкционном хосте (напр. Render EU)
    задайте FORCE_DIRECT_LLM=true чтобы обойти fallback и идти в openai/
    anthropic/gemini напрямую (там санкции не действуют).
    """
    import os
    import structlog
    logger = structlog.get_logger()

    name = provider_name or settings.llm_provider
    force_direct = os.environ.get("FORCE_DIRECT_LLM", "").lower() in ("true", "1", "yes")

    # Если санкционный провайдер и нет ни прокси, ни релея, ни force_direct — fallback.
    if (name in _SANCTIONED_PROVIDERS
            and not settings.llm_proxy_url
            and not settings.relay_url
            and not force_direct):
        logger.warning(
            "sanctioned_provider_no_proxy",
            provider=name,
            msg=f"Провайдер '{name}' заблокирован из РБ, а прокси не настроен. Ищу альтернативу...",
        )
        for fallback in _FALLBACK_ORDER:
            if fallback not in _SANCTIONED_PROVIDERS and fallback in _PROVIDERS:
                # Проверяем что API ключ заполнен
                key_map = {
                    "atlas": settings.atlas_api_key,
                    "openrouter": settings.openrouter_api_key,
                    "deepseek": settings.deepseek_api_key,
                    "glm": settings.glm_api_key,
                    "qwen": settings.qwen_api_key,
                    "yandexgpt": settings.yandexgpt_api_key,
                    "gigachat": settings.gigachat_auth_key,
                }
                if key_map.get(fallback):
                    logger.info("fallback_provider", original=name, fallback=fallback)
                    return _PROVIDERS[fallback]()

        logger.error("no_available_provider", msg="Нет доступных провайдеров без прокси")

    return _PROVIDERS[name]()


def list_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def list_nonsanctioned_providers() -> list[str]:
    """Провайдеры, которые работают из РБ без прокси."""
    return [p for p in _PROVIDERS if p not in _SANCTIONED_PROVIDERS]
