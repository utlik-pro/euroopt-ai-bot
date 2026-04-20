"""Тесты триггера принудительного web search для вопросов про актуальные акции.

Убеждаемся что:
- Общие вопросы про акции/скидки → триггер сработал
- Вопросы про FAQ/Еплюс/рецепты → триггер НЕ сработал (xlsx от 15.04 + RAG норм)
"""
import sys
sys.path.insert(0, ".")

import pytest

from src.pipeline import needs_fresh_promo


class TestShouldTriggerFreshPromo:
    @pytest.mark.parametrize("msg", [
        "Какие акции сейчас?",
        "Какие скидки на этой неделе?",
        "Что по ценопаду в Евроопте?",
        "Есть ли распродажа?",
        "Какие акции актуальны сегодня?",
        "Покажи свежие предложения",
        "Что новое в акциях?",
        "Красная цена на что сейчас?",
        "Чёрная пятница в Евроопте когда?",
        "Цены вниз на молочку?",
        "Есть ли скидка на куриное филе?",
        "Какая скидка на хлеб в апреле?",
    ])
    def test_promo_questions_trigger(self, msg):
        assert needs_fresh_promo(msg), f"Должен триггерить: {msg!r}"


class TestShouldNotTriggerFreshPromo:
    @pytest.mark.parametrize("msg", [
        "Как работает Еплюс?",
        "Где ближайший магазин в Минске?",
        "Что такое Мигом?",
        "Как сварить борщ?",
        "Сколько бонусов на моей карте?",
        "Режим работы магазина на Независимости",
        "Что приготовить на ужин за 30 минут?",
        "Как оплатить онлайн заказ?",
    ])
    def test_non_promo_questions_dont_trigger(self, msg):
        assert not needs_fresh_promo(msg), f"НЕ должен триггерить: {msg!r}"


class TestEdgeCases:
    def test_empty_message(self):
        assert not needs_fresh_promo("")

    def test_none(self):
        assert not needs_fresh_promo(None)

    def test_mixed_case(self):
        assert needs_fresh_promo("АКЦИИ ЭТОЙ НЕДЕЛИ")

    def test_russian_plural_forms(self):
        # склонения слова «акция»
        for form in ["акция", "акции", "акций", "акцию", "акцией"]:
            assert needs_fresh_promo(f"какая {form}?"), form
