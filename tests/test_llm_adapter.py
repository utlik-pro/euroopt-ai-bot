"""Тест-агент 4: LLM-адаптер.

Проверяет:
- Все провайдеры зарегистрированы
- Переключение между провайдерами
- Корректная обработка ошибок
"""

import sys
sys.path.insert(0, ".")

import pytest
from src.llm.adapter import (
    get_llm_provider,
    list_providers,
    ClaudeProvider,
    DeepSeekProvider,
    GLMProvider,
    GeminiProvider,
    OpenAIProvider,
    QwenProvider,
    YandexGPTProvider,
    GigaChatProvider,
    LLMResponse,
)


class TestProviderRegistry:
    def test_all_9_providers_registered(self):
        providers = list_providers()
        expected = ["anthropic", "deepseek", "glm", "gemini", "openai", "qwen", "yandexgpt", "gigachat"]
        for p in expected:
            assert p in providers, f"Provider '{p}' should be registered"

    def test_anthropic_returns_claude(self):
        provider = get_llm_provider("anthropic")
        assert isinstance(provider, ClaudeProvider)

    def test_deepseek_returns_deepseek(self):
        provider = get_llm_provider("deepseek")
        assert isinstance(provider, DeepSeekProvider)

    def test_glm_returns_glm(self):
        provider = get_llm_provider("glm")
        assert isinstance(provider, GLMProvider)

    def test_gemini_returns_gemini(self):
        provider = get_llm_provider("gemini")
        assert isinstance(provider, GeminiProvider)

    def test_openai_returns_openai(self):
        provider = get_llm_provider("openai")
        assert isinstance(provider, OpenAIProvider)

    def test_qwen_returns_qwen(self):
        provider = get_llm_provider("qwen")
        assert isinstance(provider, QwenProvider)

    def test_yandexgpt_returns_yandexgpt(self):
        provider = get_llm_provider("yandexgpt")
        assert isinstance(provider, YandexGPTProvider)

    def test_gigachat_returns_gigachat(self):
        provider = get_llm_provider("gigachat")
        assert isinstance(provider, GigaChatProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_provider("nonexistent")


class TestLLMResponse:
    def test_response_dataclass(self):
        r = LLMResponse(text="hello", model="test", input_tokens=10, output_tokens=20)
        assert r.text == "hello"
        assert r.model == "test"
        assert r.input_tokens == 10
        assert r.output_tokens == 20
