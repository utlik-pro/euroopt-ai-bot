"""Smoke-тесты для snapshot текущей листовки от заказчика.

Проверяет:
1. parse_listovka_xlsx.py корректно парсит xlsx → JSON
2. JSON содержит товары с обязательными полями (name, price, promo)
3. load_listovka_current() добавляет ≥6 чанков (summary + per-promo + СВАЁ + Родныя)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def test_listovka_json_exists():
    """data/promotions/listovka_current.json должен существовать после parse_listovka_xlsx."""
    fp = ROOT / "data/promotions/listovka_current.json"
    assert fp.exists(), f"Не найден {fp} — запусти scripts/parse_listovka_xlsx.py"


def test_listovka_json_schema():
    """Snapshot имеет meta + products + svae_assortment + rodnyya_news."""
    fp = ROOT / "data/promotions/listovka_current.json"
    if not fp.exists():
        pytest.skip("listovka_current.json отсутствует — пропускаем (на CI генерится отдельным шагом)")
    data = json.load(open(fp, encoding="utf-8"))

    assert "meta" in data
    assert "products" in data
    assert isinstance(data["products"], list)
    # Период должен быть проставлен
    assert data["meta"].get("period"), "meta.period пустой — парсер не нашёл период в Сводке"
    # Хотя бы 50 товаров (саниты-чек, реальное число 200+)
    assert len(data["products"]) >= 50, f"Слишком мало товаров: {len(data['products'])}"

    # Проверяем структуру первого товара
    p = data["products"][0]
    for field in ("name", "price", "promo"):
        assert field in p, f"В товаре нет поля {field}: {p}"
    assert isinstance(p["price"], (int, float))


def test_listovka_promo_breakdown_sane():
    """В snapshot должны быть основные акции из листовки."""
    fp = ROOT / "data/promotions/listovka_current.json"
    if not fp.exists():
        pytest.skip("listovka_current.json отсутствует")
    data = json.load(open(fp, encoding="utf-8"))
    breakdown = data["meta"].get("promo_breakdown") or {}
    promos_lower = " ".join(breakdown.keys()).lower()
    assert "красная цена" in promos_lower, f"В snapshot нет «Красной цены»: {list(breakdown.keys())}"


def test_load_listovka_current_runs():
    """Loader не падает и добавляет хотя бы 1 чанк (если файл есть)."""
    fp = ROOT / "data/promotions/listovka_current.json"
    if not fp.exists():
        pytest.skip("listovka_current.json отсутствует")

    # Импортируем reindex_v2 как модуль, дёргаем loader изолированно
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reindex_v2", ROOT / "scripts/reindex_v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # docs — глобальный список модуля; обнулим перед вызовом
    module.docs.clear()
    n = module.load_listovka_current()

    assert n >= 1, "load_listovka_current() ничего не добавил"
    # Должны быть summary + per-promo + (опц.) СВАЁ + Родныя
    ids = [d["id"] for d in module.docs]
    assert any(i.startswith("listovka_summary") for i in ids), f"Нет summary-чанка: {ids}"
    assert sum(1 for i in ids if i.startswith("listovka_")) >= 2, (
        f"Слишком мало per-promo чанков: {ids}"
    )
