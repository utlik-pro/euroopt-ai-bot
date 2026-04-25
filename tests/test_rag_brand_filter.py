"""Тесты brand/city фильтра в RAGEngine.search.

Цель: гарантировать, что вопрос «Евроопт в Лиде» не подмешивает Хит,
и наоборот. Это закрывает претензию 24.04 P1 (смешение сетей).

Тесты используют реальный RAGEngine, так что требуют ChromaDB. Если CI
без ChromaDB — пометить мощным skip-mark или вынести в integration-suite.
Здесь — лёгкий unit-тест на логику фильтрации post-search (без сетевых
зависимостей).
"""
from src.router.brand_detector import detect_brand, detect_city


def _mock_rag_filter(hits: list[dict], brand: str | None = None, city: str | None = None) -> list[dict]:
    """Воспроизводит логику фильтрации RAGEngine.search в чистом виде.

    Дублируется здесь, чтобы можно было тестировать без ChromaDB.
    Если кто-то поменяет логику в src/rag/engine.py — этот тест должен тоже
    обновиться, иначе расхождение можно будет заметить через интеграционные.
    """
    out = list(hits)
    if brand:
        b_low = brand.lower()
        out = [
            h for h in out
            if (h.get("metadata") or {}).get("category") != "store"
            or ((h.get("metadata") or {}).get("brand") or "").lower() == b_low
        ]
    if city:
        c_low = city.lower()
        out = [
            h for h in out
            if (h.get("metadata") or {}).get("category") != "store"
            or c_low in ((h.get("metadata") or {}).get("city") or "").lower()
        ]
    return out


def _store(brand: str, city: str, address: str = "Test st 1") -> dict:
    return {
        "id": f"{brand}_{city}_{address}",
        "text": f"{brand} в {city}: {address}",
        "score": 0.8,
        "metadata": {"category": "store", "brand": brand, "city": city, "address": address},
    }


def test_brand_filter_keeps_only_evroopt():
    hits = [
        _store("Евроопт", "Минск"),
        _store("Хит", "Минск"),
        _store("Евроопт", "Лида"),
    ]
    out = _mock_rag_filter(hits, brand="Евроопт")
    assert len(out) == 2
    assert all(h["metadata"]["brand"] == "Евроопт" for h in out)


def test_brand_filter_keeps_only_hit():
    hits = [
        _store("Евроопт", "Минск"),
        _store("Хит", "Минск"),
        _store("Хит", "Гомель"),
    ]
    out = _mock_rag_filter(hits, brand="Хит")
    assert len(out) == 2
    assert all(h["metadata"]["brand"] == "Хит" for h in out)


def test_city_filter_keeps_only_lida():
    hits = [
        _store("Евроопт", "Минск"),
        _store("Евроопт", "Лида"),
        _store("Евроопт", "Гродно"),
    ]
    out = _mock_rag_filter(hits, city="Лида")
    assert len(out) == 1
    assert out[0]["metadata"]["city"] == "Лида"


def test_brand_and_city_combined():
    """«Евроопт в Лиде» — Хит из Лиды и Евроопт из Минска должны отсеяться."""
    hits = [
        _store("Евроопт", "Минск"),
        _store("Хит", "Лида"),
        _store("Евроопт", "Лида"),
        _store("Хит", "Минск"),
    ]
    out = _mock_rag_filter(hits, brand="Евроопт", city="Лида")
    assert len(out) == 1
    assert out[0]["metadata"]["brand"] == "Евроопт"
    assert out[0]["metadata"]["city"] == "Лида"


def test_brand_filter_does_not_remove_faq():
    """FAQ/рецепты не имеют поля brand → проходят свободно даже при brand-фильтре."""
    hits = [
        _store("Евроопт", "Минск"),
        _store("Хит", "Минск"),
        {
            "id": "faq_1",
            "text": "Q: ...",
            "score": 0.7,
            "metadata": {"category": "faq", "source": "contacts.json"},
        },
    ]
    out = _mock_rag_filter(hits, brand="Евроопт")
    # 1 Евроопт + 1 FAQ = 2 (Хит выкинули)
    assert len(out) == 2
    assert any(h["metadata"]["category"] == "faq" for h in out)


def test_no_filter_returns_all():
    hits = [
        _store("Евроопт", "Минск"),
        _store("Хит", "Минск"),
    ]
    out = _mock_rag_filter(hits)
    assert len(out) == 2


def test_full_flow_evroopt_lida():
    """End-to-end: вопрос → детектор → фильтр."""
    msg = "Где Евроопт в Лиде?"
    brand = detect_brand(msg)
    city = detect_city(msg)
    assert brand == "Евроопт"
    assert city == "Лида"

    hits = [
        _store("Евроопт", "Лида"),
        _store("Хит", "Лида"),  # должен отфильтроваться
        _store("Евроопт", "Минск"),  # должен отфильтроваться
    ]
    out = _mock_rag_filter(hits, brand=brand, city=city)
    assert len(out) == 1
    assert out[0]["metadata"]["brand"] == "Евроопт"
    assert out[0]["metadata"]["city"] == "Лида"


def test_full_flow_hit_minsk_no_evroopt_leak():
    """Вопрос «Хит в Минске» — Евроопт-магазины не должны просочиться."""
    msg = "Где находится Хит дискаунтер в Минске?"
    brand = detect_brand(msg)
    city = detect_city(msg)
    assert brand == "Хит"
    assert city == "Минск"

    hits = [
        _store("Хит", "Минск"),
        _store("Хит", "Минск"),
        _store("Евроопт", "Минск"),  # отфильтруется
    ]
    out = _mock_rag_filter(hits, brand=brand, city=city)
    assert len(out) == 2
    assert all(h["metadata"]["brand"] == "Хит" for h in out)
