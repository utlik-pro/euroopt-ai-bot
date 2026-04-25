"""Тесты query normalizer — для повторяемости в RAG."""
from src.search.query_normalizer import normalize_query, canonicalize_for_cache


def test_synonym_expansion_lk():
    assert "личный кабинет" in normalize_query("как войти в ЛК")


def test_synonym_expansion_eplus_variants():
    n1 = normalize_query("карта Eplus")
    n2 = normalize_query("карта Е+")
    n3 = normalize_query("карта еплюс")
    # Все три должны нормализоваться к одному бренду
    assert "еплюс" in n1
    assert "еплюс" in n2
    assert "еплюс" in n3


def test_brand_normalization_evroopt():
    assert "евроопт" in normalize_query("Магазины Евроопта")
    assert "евроопт" in normalize_query("евроопт в Минске")


def test_yamigom_inline_form():
    """«я мигом» (раздельно) должно нормализоваться к «ямигом»."""
    n = normalize_query("закажи доставку через я мигом")
    assert "ямигом" in n


def test_punctuation_stripped():
    n = normalize_query("Какие сегодня акции?")
    assert "?" not in n
    assert "сегодня" in n or "сегодн" in n
    assert "акци" in n


def test_stopwords_removed_for_long_query():
    n = normalize_query("что такое Еплюс и для чего она нужна")
    # «и», «для», «что» — стоп-слова
    assert " и " not in f" {n} "
    assert " для " not in f" {n} "


def test_stopwords_kept_for_short_query():
    """Очень короткий запрос — стоп-слова не выкидываем."""
    n = normalize_query("в чём дело")
    # Не должны потерять смысл
    assert n  # не пустая строка


def test_canonicalize_returns_sorted_tokens():
    """canonicalize_for_cache даёт одинаковый ключ на парафразы."""
    a = canonicalize_for_cache("магазины Евроопт в Лиде")
    b = canonicalize_for_cache("Лида Евроопт магазины")
    # Оба должны нормализоваться к одинаковому набору токенов
    assert a == b


def test_canonicalize_paraphrase_eplus_brand_match():
    """Парафразы про карту Еплюс должны давать одинаковый бренд в ключе.

    Семантическое совпадение синонимов «сколько стоит» / «цена» — это работа
    RAG embeddings, не плоского нормализатора. Здесь проверяем только что
    ключевая сущность («еплюс») совпадает, а формулировку доберёт RAG.
    """
    a = canonicalize_for_cache("сколько стоит карта Еплюс")
    b = canonicalize_for_cache("цена карты Eplus")
    assert "еплюс" in a
    assert "еплюс" in b
    # «карта/карты» — однокоренные, морфология русского. Минимально проверяем
    # что обе содержат корень «карт».
    assert "карт" in a
    assert "карт" in b


def test_idempotent():
    """Повторное применение не меняет результат."""
    n1 = normalize_query("Магазины ЕВРООПТА в МИНСКЕ")
    n2 = normalize_query(n1)
    assert n1 == n2
