"""Тесты GroundingVerifier — детекция галлюцинаций по конкретике."""
from src.verify import GroundingVerifier


def _v() -> GroundingVerifier:
    return GroundingVerifier()


def test_safe_phone_passes():
    """Каноническая горячая линия — всегда ок, даже если её нет в источниках."""
    v = _v()
    res = v.verify(
        "Звоните на горячую линию +375 44 788 88 80.",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is True


def test_hallucinated_phone_flagged():
    """Любой другой номер, которого нет в источниках, — флагируется."""
    v = _v()
    res = v.verify(
        "Свяжитесь по номеру +375 17 123 45 67",
        kb_text="Магазины Евроопт работают круглосуточно",
        web_text="",
    )
    assert res.is_grounded is False
    assert any(i.kind == "phone" for i in res.issues)


def test_safe_percent_99_passes():
    v = _v()
    res = v.verify("Бонусами можно оплатить до 99% покупки.", kb_text="", web_text="")
    assert res.is_grounded is True


def test_grounded_price_from_kb():
    """Цена, упомянутая в KB, — допустима."""
    v = _v()
    res = v.verify(
        "Карта стоит 99 копеек.",
        kb_text="Пластиковая карта Еплюс — 99 копеек на кассе",
        web_text="",
    )
    assert res.is_grounded is True


def test_hallucinated_time_flagged():
    """Конкретные часы работы, которых нет в источниках, — галлюцинация."""
    v = _v()
    res = v.verify(
        "Магазин работает с 09:00 до 22:00",
        kb_text="Гипермаркеты Евроопт работают по адресу...",
        web_text="",
    )
    assert res.is_grounded is False
    assert any(i.kind == "time" for i in res.issues)


def test_grounded_time_from_kb():
    v = _v()
    res = v.verify(
        "Магазин работает с 8:00 до 23:00",
        kb_text="Типовой режим гипермаркетов: 8:00-23:00",
        web_text="",
    )
    assert res.is_grounded is True


def test_auto_fix_replaces_phone():
    v = GroundingVerifier(auto_fix=True)
    res = v.verify(
        "Свяжитесь по номеру +375 17 123 45 67",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is False
    # При auto_fix фейковый телефон заменяется на безопасный
    assert "+375 44 788 88 80" in res.cleaned_text
    assert "+375 17 123 45 67" not in res.cleaned_text


def test_empty_response():
    v = _v()
    res = v.verify("", kb_text="anything", web_text="anything")
    assert res.is_grounded is True


def test_no_concrete_facts_passes():
    """Ответ без конкретных чисел/телефонов — всегда ок."""
    v = _v()
    res = v.verify(
        "Подробности уточняйте на сайте евроопт.",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is True
