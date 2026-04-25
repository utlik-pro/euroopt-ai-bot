"""Тесты brand & city detector."""
from src.router.brand_detector import detect_brand, detect_city, detect_format


def test_detect_brand_evroopt():
    assert detect_brand("Где ближайший Евроопт?") == "Евроопт"
    assert detect_brand("евроопт в Минске") == "Евроопт"
    assert detect_brand("EUROOPT в Лиде") == "Евроопт"


def test_detect_brand_hit():
    assert detect_brand("где Хит Дискаунтер?") == "Хит"
    assert detect_brand("Хит дискаунтер в Гомеле") == "Хит"
    assert detect_brand("Хит в Минске") == "Хит"


def test_detect_brand_groshyk():
    assert detect_brand("Грошык в Гродно") == "Грошык"
    assert detect_brand("грошик где?") == "Грошык"


def test_detect_brand_none():
    assert detect_brand("какая погода?") is None
    assert detect_brand("сколько стоит молоко?") is None


def test_detect_brand_priority_first_mention():
    """Если упомянуты оба — берём первый."""
    res = detect_brand("Евроопт или Хит — где лучше?")
    assert res == "Евроопт"
    res2 = detect_brand("Хит или Евроопт?")
    assert res2 == "Хит"


def test_detect_brand_does_not_match_in_word():
    """«Хит сезона» — это не наш магазин."""
    assert detect_brand("главный хит сезона — мороженое") is None


def test_detect_city_minsk_variants():
    assert detect_city("магазины в Минске") == "Минск"
    assert detect_city("Менск-2") == "Минск"
    assert detect_city("МИНСК") == "Минск"


def test_detect_city_lida_padezh():
    """«Лиде», «Лиду», «Лиды» — все приводятся к «Лида»."""
    assert detect_city("магазины в Лиде") == "Лида"
    assert detect_city("Евроопт Лида") == "Лида"
    assert detect_city("из Лиды") == "Лида"


def test_detect_city_other_cities():
    assert detect_city("Гомеле") == "Гомель"
    assert detect_city("в Барановичах") == "Барановичи"
    assert detect_city("Витебск-2") == "Витебск"


def test_detect_city_none():
    assert detect_city("какие акции?") is None


def test_detect_format_avtolavka():
    assert detect_format("где автолавка?") == "Автолавка"
    assert detect_format("маршрут автолавки") == "Автолавка"


def test_detect_format_giper():
    assert detect_format("ближайший гипермаркет") == "Гипермаркет"
    assert detect_format("Евроопт гипер") == "Гипермаркет"


def test_detect_format_none():
    assert detect_format("что такое Еплюс") is None


def test_combined_brand_city():
    """Самый частый случай: «Евроопт в Минске»."""
    msg = "где ближайший Евроопт в Лиде?"
    assert detect_brand(msg) == "Евроопт"
    assert detect_city(msg) == "Лида"
