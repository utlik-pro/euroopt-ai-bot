"""Тест-агент 1: Контент-фильтр.

Проверяет что бот НЕ обсуждает:
- Политику (любые формы слов)
- Религию
- Конкурентов (Гиппо, Белмаркет, Соседи, Корона)
- Нецензурную лексику

И НЕ блокирует нормальные запросы покупателей.
"""

import sys
sys.path.insert(0, ".")

import pytest
from src.filters.content_filter import check_content, POLITE_REFUSAL


# === ДОЛЖНЫ БЫТЬ ЗАБЛОКИРОВАНЫ ===

class TestBlocksPolitics:
    @pytest.mark.parametrize("msg", [
        "Что думаешь о политике?",
        "Кто наш президент?",
        "Какая партия лучше?",
        "Как насчёт оппозиции?",
        "Будут ли санкции?",
        "Расскажи про протесты",
        "Лукашенко молодец?",
        "А Путин что думает?",
        "Трамп или Байден?",
        "Зеленский герой?",
        "Какие выборы будут?",
        "Что скажешь о государстве?",
    ])
    def test_blocks_politics(self, msg):
        allowed, refusal = check_content(msg)
        assert not allowed, f"Should block political message: '{msg}'"
        assert refusal == POLITE_REFUSAL


class TestBlocksReligion:
    @pytest.mark.parametrize("msg", [
        "Какая религия лучше?",
        "Расскажи про церковь",
        "Где ближайшая мечеть?",
        "Бог существует?",
        "Как правильно молитва?",
        "Ислам или христианство?",
        "Расскажи про буддизм",
        "Что такое иудаизм?",
    ])
    def test_blocks_religion(self, msg):
        allowed, refusal = check_content(msg)
        assert not allowed, f"Should block religious message: '{msg}'"


class TestBlocksCompetitors:
    @pytest.mark.parametrize("msg", [
        "А в Гиппо дешевле",
        "Белмаркет лучше",
        "Пойду в Соседи",
        "В Короне акции круче",
        "Закажу на mile.by",
        "Остров чистоты рядом",
    ])
    def test_blocks_competitors(self, msg):
        allowed, refusal = check_content(msg)
        assert not allowed, f"Should block competitor mention: '{msg}'"


class TestBlocksProfanity:
    @pytest.mark.parametrize("msg", [
        "Ну это пиздец какой-то",
        "Блять, опять цены выросли",
    ])
    def test_blocks_profanity(self, msg):
        allowed, refusal = check_content(msg)
        assert not allowed, f"Should block profanity: '{msg}'"


# === ДОЛЖНЫ БЫТЬ ПРОПУЩЕНЫ ===

class TestAllowsNormalQueries:
    @pytest.mark.parametrize("msg", [
        "Какие акции сегодня?",
        "Что приготовить на ужин?",
        "Как работает доставка?",
        "Где ближайший Евроопт?",
        "Хочу борщ",
        "Сколько стоит молоко?",
        "Режим работы магазина",
        "Как вернуть товар?",
        "Что такое Е-плюс?",
        "Рецепт карбонары",
        "Есть ли безглютеновые продукты?",
        "Можно оплатить картой?",
        "Привет!",
        "Спасибо за помощь",
        "Где купить свёклу?",
        "Подбери продукты на 50 рублей",
        "Что интересного на этой неделе?",
        "Расскажи про программу лояльности",
    ])
    def test_allows_normal_queries(self, msg):
        allowed, refusal = check_content(msg)
        assert allowed, f"Should allow normal query: '{msg}'"
        assert refusal is None


class TestEdgeCases:
    """Пограничные случаи — слова из контекста магазина."""

    @pytest.mark.parametrize("msg", [
        "Корона — это марка пива?",  # "корона" как продукт → блокируется (ложное срабатывание, но безопасно)
        "Купи мне божественный торт",  # "бог" в составе слова
    ])
    def test_edge_cases_documented(self, msg):
        """Edge cases — документируем поведение, не утверждаем правильность."""
        allowed, _ = check_content(msg)
        # Просто логируем, не assert — это для обсуждения с клиентом
        print(f"  Edge case: '{msg}' → allowed={allowed}")
