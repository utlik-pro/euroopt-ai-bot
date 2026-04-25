"""Тесты CanonicalMatcher — гарантия 100% повторяемости на критичных FAQ."""
from src.canonical import CanonicalMatcher


def _matcher() -> CanonicalMatcher:
    return CanonicalMatcher()


def test_loads_yaml_and_has_answers():
    m = _matcher()
    assert m.stats()["total_answers"] >= 5, "должны быть загружены каноны из YAML"


def test_match_eplus_pay_99_percent_direct():
    m = _matcher()
    hit = m.match("можно ли оплатить весь чек бонусами?")
    assert hit is not None
    assert hit.id == "eplus_pay_99_percent"
    assert "99%" in hit.answer
    assert "2 копейки" in hit.answer


def test_match_eplus_pay_paraphrase():
    m = _matcher()
    # Перефраз — должен сматчиться через token_set_ratio
    hit = m.match("сколько процентов от чека можно оплатить бонусами Еплюс?")
    assert hit is not None
    assert hit.id == "eplus_pay_99_percent"


def test_match_card_lost():
    m = _matcher()
    hit = m.match("я потерял карту Еплюс что мне делать")
    assert hit is not None
    assert hit.id == "eplus_card_lost"
    assert "виртуальную" in hit.answer.lower()


def test_match_login_lk():
    m = _matcher()
    hit = m.match("забыл пароль от личного кабинета")
    assert hit is not None
    assert hit.id == "eplus_login"


def test_match_transfer_bonuses():
    m = _matcher()
    hit = m.match("как перенести бонусы на новую карту")
    assert hit is not None
    assert hit.id == "eplus_transfer_bonuses"
    assert "форму обратной связи" in hit.answer.lower()


def test_match_hotline():
    m = _matcher()
    hit = m.match("дайте телефон поддержки")
    assert hit is not None
    assert hit.id == "hotline"
    assert "+375 44 788 88 80" in hit.answer


def test_no_match_unrelated_query():
    m = _matcher()
    # Совсем другой вопрос — не должен сматчиться
    hit = m.match("какая сейчас погода в Минске?")
    assert hit is None


def test_no_match_empty():
    m = _matcher()
    assert m.match("") is None
    assert m.match("   ") is None


def test_canonical_returns_same_answer_twice():
    """Главное свойство: канонический ответ — детерминирован."""
    m = _matcher()
    a1 = m.match("оплата бонусами Еплюс сколько процентов?")
    a2 = m.match("сколько % бонусами можно оплатить чек?")
    assert a1 is not None and a2 is not None
    assert a1.id == a2.id
    assert a1.answer == a2.answer  # тот же текст до символа


def test_card_price_canonical():
    m = _matcher()
    hit = m.match("сколько стоит карта Еплюс?")
    assert hit is not None
    assert hit.id == "card_price"
    assert "99 копеек" in hit.answer
    assert "Виртуальная карта — бесплатно" in hit.answer or "бесплатно" in hit.answer.lower()
