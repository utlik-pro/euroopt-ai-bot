"""Тесты MechanicDetector — определение механики акции из запроса."""
from src.promotions.mechanic_detector import MechanicDetector


def _d() -> MechanicDetector:
    return MechanicDetector()


def test_loads_mechanics_from_json():
    d = _d()
    assert len(d.mechanics) >= 10


def test_detect_evroshok():
    d = _d()
    m = d.detect("какие сейчас акции Еврошок?")
    assert m is not None
    assert m.id == "evroshok"
    # Заказчик 28.04: «Еврошок — это акция Е-доставки, не Евроопта офлайн».
    assert m.network == "Е-доставка"


def test_detect_evroshok_alias_lowercase():
    d = _d()
    m = d.detect("еврошоковые цены сегодня")
    assert m is not None
    assert m.id == "evroshok"


def test_detect_chernaya_pyatnitsa_variants():
    """«Чёрная пятница», «черная пятница», «пятница чёрных цен» — все ведут к одному."""
    d = _d()
    for msg in [
        "чёрная пятница на этой неделе",
        "когда черные цены?",
        "пятница чёрных цен — что почем?",
    ]:
        m = d.detect(msg)
        assert m is not None, f"не сматчилось: {msg}"
        assert m.id == "chyornaya_pyatnitsa", f"{msg} → {m.id}"


def test_detect_one_plus_one():
    """«1+1» — особый случай: символ + не word-char, нужны спец-границы."""
    d = _d()
    m = d.detect("есть ли акция 1+1 на молоко?")
    assert m is not None
    assert m.id == "one_plus_one"


def test_detect_eplus_bonuses():
    d = _d()
    m = d.detect("на этой неделе удвоенные бонусы Еплюс?")
    assert m is not None
    assert m.id == "eplus_bonuses"


def test_detect_spectseny_not_evroshok():
    """«Спеццены» — отдельная механика, не должна детектиться как Еврошок."""
    d = _d()
    m = d.detect("на какие товары спеццены?")
    assert m is not None
    assert m.id == "spectseny"


def test_detect_red_price():
    d = _d()
    m = d.detect("где красные ценники в Евроопте?")
    assert m is not None
    assert m.id == "krasnaya_tsena"


def test_detect_first_mention_priority():
    """Если в запросе несколько механик — берём первую по позиции."""
    d = _d()
    m = d.detect("Еврошок или Спеццены — что выгоднее?")
    assert m is not None
    assert m.id == "evroshok"


def test_detect_none_for_unrelated():
    d = _d()
    assert d.detect("какая погода в Минске?") is None
    assert d.detect("где ближайший магазин?") is None


def test_detect_none_for_empty():
    d = _d()
    assert d.detect("") is None
    assert d.detect(None) is None  # type: ignore[arg-type]


def test_detect_all_returns_unique():
    """detect_all возвращает все упомянутые механики без дублей."""
    d = _d()
    mechanics = d.detect_all("Еврошок и Спеццены — в чём разница?")
    assert len(mechanics) >= 2
    ids = [m.id for m in mechanics]
    assert "evroshok" in ids
    assert "spectseny" in ids
    # Не должно быть дублей
    assert len(ids) == len(set(ids))


def test_get_by_network():
    d = _d()
    evroopt = d.get_by_network("Евроопт")
    hit = d.get_by_network("Хит")
    groshyk = d.get_by_network("Грошык")
    assert len(evroopt) >= 8
    assert len(hit) >= 1
    assert len(groshyk) >= 1


def test_get_by_id():
    d = _d()
    m = d.get_by_id("evroshok")
    assert m is not None
    assert m.name == "Еврошок"
    assert d.get_by_id("nonexistent") is None


def test_format_brief_contains_essential_info():
    d = _d()
    m = d.get_by_id("eplus_bonuses")
    assert m is not None
    brief = m.format_brief()
    assert "Еплюс" in brief or "бонус" in brief.lower()
    assert m.landing_url in brief
    assert m.network in brief
