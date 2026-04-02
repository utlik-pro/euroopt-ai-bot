"""Тест-агент 2: Промоушн-движок.

Проверяет:
- Загрузка акций из JSON
- Поиск релевантных акций по запросу
- Корректное форматирование
- Auto-expire просроченных акций
"""

import sys
sys.path.insert(0, ".")

import json
import pytest
from pathlib import Path
from datetime import date, timedelta
from src.promotions.engine import PromotionEngine


@pytest.fixture
def engine():
    return PromotionEngine()


class TestPromotionLoading:
    def test_loads_sample_promotions(self, engine):
        assert len(engine.promotions) > 0, "Should load sample promotions"

    def test_all_promotions_have_required_fields(self, engine):
        for p in engine.promotions:
            assert "name" in p, f"Missing 'name' in promotion: {p}"
            assert "new_price" in p, f"Missing 'new_price' in promotion: {p}"
            assert "end_date" in p, f"Missing 'end_date' in promotion: {p}"


class TestPromotionSearch:
    def test_finds_beet_for_borsch(self, engine):
        results = engine.get_relevant_promotions("борщ свёкла")
        names = [r["name"] for r in results]
        assert any("Свёкла" in n for n in names), f"Should find beet for borsch, got: {names}"

    def test_finds_chicken_for_dinner(self, engine):
        results = engine.get_relevant_promotions("курица ужин филе")
        names = [r["name"] for r in results]
        assert any("Куриное" in n or "курин" in n.lower() for n in names), f"Should find chicken, got: {names}"

    def test_finds_pasta_ingredients(self, engine):
        results = engine.get_relevant_promotions("спагетти паста карбонара")
        names = [r["name"] for r in results]
        assert any("Спагетти" in n or "Пармезан" in n for n in names), f"Should find pasta/cheese, got: {names}"

    def test_returns_limited_results(self, engine):
        results = engine.get_relevant_promotions("продукты еда", limit=2)
        assert len(results) <= 2

    def test_top_promotions_returns_results(self, engine):
        top = engine.get_top_promotions(limit=3)
        assert len(top) <= 3
        assert len(top) > 0


class TestPromotionFormatting:
    def test_format_with_old_price(self, engine):
        promos = [{"name": "Тест", "old_price": "10.00", "new_price": "7.00", "end_date": "2026-12-31"}]
        text = engine.format_promotions(promos)
        assert "7.00 BYN" in text
        assert "10.00 BYN" in text
        assert "Тест" in text

    def test_format_empty_list(self, engine):
        text = engine.format_promotions([])
        assert text == ""


class TestPromotionExpiry:
    def test_filters_expired_promotions(self, tmp_path):
        """Просроченные акции не должны показываться."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        test_data = [
            {"name": "Expired", "new_price": "1.00", "end_date": yesterday},
            {"name": "Active", "new_price": "2.00", "end_date": tomorrow},
        ]

        # Подменяем директорию
        promo_dir = tmp_path / "promotions"
        promo_dir.mkdir()
        (promo_dir / "test.json").write_text(json.dumps(test_data), encoding="utf-8")

        import src.promotions.engine as pe
        original_dir = pe.PROMOTIONS_DIR
        pe.PROMOTIONS_DIR = promo_dir

        try:
            engine = PromotionEngine()
            names = [p["name"] for p in engine.promotions]
            assert "Active" in names, "Active promotion should be loaded"
            assert "Expired" not in names, "Expired promotion should be filtered"
        finally:
            pe.PROMOTIONS_DIR = original_dir
