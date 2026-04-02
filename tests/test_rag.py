"""Тест-агент 3: RAG-система.

Проверяет:
- Загрузка документов
- Релевантность поиска (находит правильные ответы)
- Категории работают
- Пустая база не падает
"""

import sys
sys.path.insert(0, ".")

import pytest
from src.rag.engine import RAGEngine
from src.knowledge.loader import load_faq, load_recipes


@pytest.fixture(scope="module")
def rag_with_data():
    """RAG с загруженными тестовыми данными."""
    rag = RAGEngine()
    load_faq(rag)
    load_recipes(rag)
    return rag


class TestRAGLoading:
    def test_loads_documents(self, rag_with_data):
        stats = rag_with_data.get_stats()
        assert stats["total_documents"] > 0, "Should have documents loaded"

    def test_faq_and_recipes_loaded(self, rag_with_data):
        stats = rag_with_data.get_stats()
        # 5 FAQ + 3 рецепта = 8
        assert stats["total_documents"] >= 8, f"Expected ≥8 docs, got {stats['total_documents']}"


class TestRAGSearch:
    def test_finds_delivery_faq(self, rag_with_data):
        results = rag_with_data.search("как работает доставка")
        assert len(results) > 0, "Should find delivery FAQ"
        assert "доставк" in results[0]["text"].lower()

    def test_finds_payment_faq(self, rag_with_data):
        results = rag_with_data.search("какие способы оплаты")
        assert len(results) > 0, "Should find payment FAQ"

    def test_finds_eplus_faq(self, rag_with_data):
        results = rag_with_data.search("что такое программа лояльности Е-плюс")
        assert len(results) > 0, "Should find E-plus FAQ"

    def test_finds_borsch_recipe(self, rag_with_data):
        results = rag_with_data.search("борщ классический рецепт свёкла капуста")
        assert len(results) > 0, "Should find borsch recipe"
        # Проверяем что хотя бы один результат связан с рецептами или борщом
        found_recipe = any("рецепт" in r["text"].lower() or "борщ" in r["text"].lower() for r in results)
        assert found_recipe, f"Should find borsch recipe in results, got: {[r['text'][:50] for r in results]}"

    def test_finds_carbonara_recipe(self, rag_with_data):
        results = rag_with_data.search("паста карбонара")
        assert len(results) > 0, "Should find carbonara recipe"

    def test_returns_limited_results(self, rag_with_data):
        results = rag_with_data.search("еда", n_results=2)
        assert len(results) <= 2

    def test_results_have_scores(self, rag_with_data):
        results = rag_with_data.search("доставка")
        for r in results:
            assert "score" in r
            assert 0 <= r["score"] <= 1

    def test_results_sorted_by_relevance(self, rag_with_data):
        results = rag_with_data.search("доставка")
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"], "Results should be sorted by relevance"


class TestRAGEdgeCases:
    def test_empty_query(self, rag_with_data):
        results = rag_with_data.search("")
        # Не должен падать
        assert isinstance(results, list)

    def test_irrelevant_query(self, rag_with_data):
        results = rag_with_data.search("квантовая физика нейтрино")
        # Может вернуть результаты с низким score, но не должен падать
        assert isinstance(results, list)

    def test_very_long_query(self, rag_with_data):
        long_query = "Я хочу приготовить что-нибудь вкусное на ужин для всей семьи " * 20
        results = rag_with_data.search(long_query)
        assert isinstance(results, list)
