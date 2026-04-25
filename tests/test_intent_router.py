"""Тесты IntentRouter — классификация запросов и параметры генерации."""
from src.router import IntentRouter, Intent


def _r() -> IntentRouter:
    return IntentRouter()


def test_currency_detected():
    r = _r()
    res = r.classify("какой сейчас курс доллара?")
    assert res.intent == Intent.CURRENCY
    assert res.allow_web is True
    assert res.deterministic is True
    assert res.temperature == 0.0


def test_promotions_detected():
    r = _r()
    res = r.classify("какие сейчас акции в Евроопте?")
    assert res.intent == Intent.PROMOTIONS
    assert res.allow_web is True


def test_eplus_detected():
    r = _r()
    res = r.classify("как восстановить пароль от Еплюс?")
    assert res.intent == Intent.EPLUS
    assert res.deterministic is True
    assert res.temperature == 0.0  # фактологический интент


def test_recipes_get_higher_temperature():
    r = _r()
    res = r.classify("посоветуй рецепт борща")
    assert res.intent == Intent.RECIPES
    assert res.temperature >= 0.4  # для рецептов нужна живость
    assert res.deterministic is False


def test_smalltalk():
    r = _r()
    res = r.classify("привет!")
    assert res.intent == Intent.SMALLTALK
    assert res.temperature >= 0.3


def test_unknown_falls_to_general():
    r = _r()
    res = r.classify("расскажи интересный факт о космосе")
    assert res.intent == Intent.GENERAL


def test_empty_query():
    r = _r()
    res = r.classify("")
    assert res.intent == Intent.GENERAL
    assert res.confidence == 0.0


def test_stores_detection():
    r = _r()
    res = r.classify("где ближайший магазин Евроопт в Минске?")
    assert res.intent == Intent.STORES
    assert res.require_rag is True


def test_delivery():
    r = _r()
    res = r.classify("хочу заказать доставку через Ямигом")
    assert res.intent == Intent.DELIVERY


def test_factual_intents_are_deterministic():
    """Все фактологические интенты должны иметь temperature 0.0–0.1."""
    r = _r()
    factual_queries = [
        ("оплата бонусами Еплюс", Intent.EPLUS),
        ("какие сегодня акции", Intent.PROMOTIONS),
        ("курс евро", Intent.CURRENCY),
        ("адрес магазина в Лиде", Intent.STORES),
    ]
    for q, expected in factual_queries:
        res = r.classify(q)
        assert res.intent == expected, f"{q} → {res.intent} (ожидался {expected})"
        assert res.temperature <= 0.1, f"{q}: temperature {res.temperature} слишком высокая"
