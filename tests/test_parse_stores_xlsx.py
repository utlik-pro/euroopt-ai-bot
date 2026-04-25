"""Тесты парсера xlsx-справочника магазинов."""
import json
from pathlib import Path

import pytest

from scripts.parse_stores_xlsx import parse_address, parse_xlsx, INPUT_XLSX, OUTPUT_JSON


def test_parse_address_minsk():
    addr = parse_address("33 Магазин г.Минск,ул.Рафиева,27:(1 974 819)[10.2019] EDI")
    assert addr["city"] == "Минск"
    assert "Рафиева" in addr["street"]
    assert addr["house"] == "27"


def test_parse_address_gp_settlement():
    addr = parse_address("146 АГ г.п.Бобр, ул.Советская, 2: (958 /1357) EDI")
    assert addr["city"] == "Бобр"
    assert "Советская" in addr["street"]


def test_parse_address_village():
    addr = parse_address("224 АГ д.Крево, ул.Сморгонская,4: (587/534) EDI")
    assert addr["city"] == "Крево"


def test_parse_address_agro_no_dot():
    addr = parse_address("190 Магазин аг Ратомка, ул.Минская, 10Б: (5 001) EDI")
    assert addr["city"] == "Ратомка"


def test_parse_address_full_string():
    addr = parse_address("33 Магазин г.Минск,ул.Рафиева,27:(1 974 819)[10.2019] EDI")
    # Полный адрес — то, что между «г.» и «:(»
    assert "Минск" in addr["address"]
    assert "Рафиева" in addr["address"]


def test_parse_address_empty():
    addr = parse_address("")
    assert addr["city"] == ""
    assert addr["street"] == ""


@pytest.mark.skipif(not INPUT_XLSX.exists(), reason="xlsx not present")
def test_parse_full_xlsx_yields_majority():
    """Парсер должен распарсить >90% строк."""
    stores = parse_xlsx()
    assert len(stores) >= 900, f"распарсилось {len(stores)} — слишком мало"


@pytest.mark.skipif(not INPUT_XLSX.exists(), reason="xlsx not present")
def test_xlsx_yields_brands_and_autolavki():
    stores = parse_xlsx()
    brands = {s["brand"] for s in stores}
    formats = {s["format"] for s in stores}
    assert "Евроопт" in brands
    assert "Хит" in brands
    assert "Автолавка" in formats


@pytest.mark.skipif(not OUTPUT_JSON.exists(), reason="json not generated yet")
def test_output_json_has_required_fields():
    """Если файл уже сгенерирован — проверяем структуру записей."""
    data = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 900
    for s in data[:10]:
        assert "brand" in s
        assert "format" in s
        assert "city" in s
        assert "address" in s


@pytest.mark.skipif(not OUTPUT_JSON.exists(), reason="json not generated yet")
def test_output_no_brand_mixing():
    """Магазины Хит-формата → бренд Хит, не Евроопт. И наоборот."""
    data = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    for s in data:
        if "хит" in (s.get("format") or "").lower():
            assert s["brand"] == "Хит", (
                f"Магазин формата {s['format']} помечен как {s['brand']}"
            )
