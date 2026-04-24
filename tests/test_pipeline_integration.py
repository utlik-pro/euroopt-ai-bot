"""Тест-агент 5: Интеграционный тест Pipeline.

Проверяет полный цикл обработки запроса:
1. Контент-фильтр отсекает запрещённые темы
2. RAG находит релевантные документы
3. Промоушн-движок подбирает акции
4. LLM генерирует ответ (мокнутый для тестов)

Для тестов с реальной LLM — см. tests/test_quality.py
"""

import sys
sys.path.insert(0, ".")

import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from src.pipeline import Pipeline
from src.llm.adapter import LLMResponse
from src.filters.content_filter import POLITE_REFUSAL


@pytest.fixture
def mock_llm_response():
    return LLMResponse(
        text="Вот отличный рецепт борща! Сейчас свёкла по акции — 2.25 BYN.",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
    )


@pytest.fixture
def pipeline_with_mock_llm(mock_llm_response):
    pipeline = Pipeline()
    pipeline.llm = AsyncMock()
    pipeline.llm.generate = AsyncMock(return_value=mock_llm_response)
    return pipeline


class TestPipelineFiltering:
    def test_blocks_politics(self, pipeline_with_mock_llm):
        result = asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process("Расскажи о политике", user_id=1)
        )
        assert result == POLITE_REFUSAL
        # LLM НЕ должен вызываться для заблокированных запросов
        pipeline_with_mock_llm.llm.generate.assert_not_called()

    def test_blocks_competitors(self, pipeline_with_mock_llm):
        result = asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process("А в Гиппо дешевле?", user_id=1)
        )
        assert result == POLITE_REFUSAL
        pipeline_with_mock_llm.llm.generate.assert_not_called()


class TestPipelineNormalFlow:
    def test_processes_normal_query(self, pipeline_with_mock_llm):
        result = asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process("Какие акции сегодня?", user_id=1)
        )
        assert "борщ" in result.lower() or "акци" in result.lower()
        pipeline_with_mock_llm.llm.generate.assert_called_once()

    def test_processes_recipe_query(self, pipeline_with_mock_llm):
        result = asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process("Хочу борщ", user_id=2)
        )
        pipeline_with_mock_llm.llm.generate.assert_called_once()
        # Проверяем что system prompt содержит контекст
        call_args = pipeline_with_mock_llm.llm.generate.call_args
        system_prompt = call_args[0][0]
        assert "Актуальные акции" in system_prompt or "акци" in system_prompt.lower()

    def test_llm_receives_rag_context(self, pipeline_with_mock_llm):
        asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process("Как работает доставка?", user_id=3)
        )
        call_args = pipeline_with_mock_llm.llm.generate.call_args
        system_prompt = call_args[0][0]
        # RAG должен найти FAQ о доставке и передать в промпт
        assert "доставк" in system_prompt.lower()


class TestPipelinePIIMasking:
    """ДС №1 к Договору 2703/26-01, п. 2.1.1: данные пользователя не должны
    попадать в LLM, внешний поиск и логи. Телефоны магазинов, ФИО публичных
    лиц и прочая публичная информация — остаются в ответах как есть."""

    def test_user_message_masked_before_llm(self, pipeline_with_mock_llm):
        """В LLM попадает маскированный текст пользователя, не сырой."""
        asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process(
                "мой тел +375 29 123-45-67, заказ готов?", user_id=10,
            )
        )
        call_args = pipeline_with_mock_llm.llm.generate.call_args
        # Второй позиционный аргумент — user_message
        passed_user_msg = call_args[0][1]
        assert "375" not in passed_user_msg, f"Сырой телефон ушёл в LLM: {passed_user_msg}"
        assert "[телефон]" in passed_user_msg

    def test_llm_response_passes_through_unchanged(self):
        """Ответ LLM с публичными данными (тел. магазина) идёт пользователю КАК ЕСТЬ."""
        pipeline = Pipeline()
        pipeline.llm = AsyncMock()
        pipeline.llm.generate = AsyncMock(return_value=LLMResponse(
            text="Позвоните на горячую линию +375 17 239 00 00",
            model="test-model",
            input_tokens=10, output_tokens=10,
        ))
        result = asyncio.get_event_loop().run_until_complete(
            pipeline.process("где горячая линия?", user_id=11)
        )
        # Публичный телефон компании должен дойти до пользователя неизменным.
        assert "+375 17 239 00 00" in result
        assert "[телефон]" not in result

    def test_log_has_no_user_pii(self, pipeline_with_mock_llm, tmp_path, monkeypatch):
        """В JSONL-логе нет сырых ПДн пользователя."""
        from src.monitoring import logger as logger_mod
        monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path)
        logger_mod.interaction_logger._get_log_file = lambda: tmp_path / "test.jsonl"

        asyncio.get_event_loop().run_until_complete(
            pipeline_with_mock_llm.process(
                "я Иван Петров, email ivan.petrov@mail.ru", user_id=12,
            )
        )
        log_file = tmp_path / "test.jsonl"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        # Пользовательские ПДн не должны оказаться в логе
        assert "ivan.petrov@mail.ru" not in content, "email пользователя в логе!"
        assert "Иван" not in content, "имя пользователя в логе!"
        assert "Петров" not in content, "фамилия пользователя в логе!"
        # Метки типов — есть
        assert "[email]" in content or "pii_detected_input" in content


class TestPipelineErrorHandling:
    def test_handles_llm_error_gracefully(self):
        pipeline = Pipeline()
        pipeline.llm = AsyncMock()
        pipeline.llm.generate = AsyncMock(side_effect=Exception("API error"))

        result = asyncio.get_event_loop().run_until_complete(
            pipeline.process("Привет", user_id=1)
        )
        assert "ошибка" in result.lower()
        assert "попробуйте" in result.lower()
