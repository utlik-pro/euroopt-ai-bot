"""Тесты re-ranker для RAG."""
from src.rag.reranker import LiteReranker, get_reranker, _tokenize, _stems


def _hit(id_: str, text: str, score: float = 0.5) -> dict:
    return {"id": id_, "text": text, "score": score, "metadata": {}}


def test_lite_reranker_keeps_relevant_first():
    """Документ с большим overlap по словам должен идти выше."""
    rr = LiteReranker()
    hits = [
        _hit("h1", "погода в минске сегодня солнечно", score=0.5),
        _hit("h2", "акции на колбасу и сыр в Евроопте", score=0.55),  # выше по orig
        _hit("h3", "адреса магазинов Евроопт в Минске", score=0.5),
    ]
    out = rr.rerank("где Евроопт в Минске?", hits)
    # h3 должен быть первым (overlap по «евроопт» и «минск»)
    assert out[0]["id"] == "h3"


def test_lite_reranker_brand_bonus():
    """Документ с упоминанием того же бренда получает бонус."""
    rr = LiteReranker()
    hits = [
        _hit("h1", "магазин по адресу проспект", score=0.7),
        _hit("h2", "Хит дискаунтер на улице Логойской", score=0.6),
    ]
    out = rr.rerank("где Хит в Минске?", hits)
    # h2 хоть и с меньшим orig, но имеет бренд-бонус
    assert out[0]["id"] == "h2"


def test_lite_reranker_empty_hits():
    rr = LiteReranker()
    assert rr.rerank("query", []) == []


def test_lite_reranker_empty_query():
    """Пустой запрос — не падаем, возвращаем top_k без изменений."""
    rr = LiteReranker()
    hits = [_hit("h1", "text"), _hit("h2", "text2")]
    out = rr.rerank("", hits, top_k=1)
    assert len(out) == 1


def test_lite_reranker_top_k_limits_output():
    rr = LiteReranker()
    hits = [_hit(f"h{i}", f"document about evroopt {i}", score=0.5) for i in range(5)]
    out = rr.rerank("евроопт", hits, top_k=2)
    assert len(out) == 2


def test_lite_reranker_writes_score():
    """rerank_score должен появиться в каждом hit."""
    rr = LiteReranker()
    hits = [_hit("h1", "евроопт минск", score=0.5)]
    out = rr.rerank("евроопт минск", hits)
    assert "rerank_score" in out[0]
    assert out[0]["rerank_score"] > 0


def test_get_reranker_lite():
    r = get_reranker("lite")
    assert isinstance(r, LiteReranker)


def test_get_reranker_off():
    assert get_reranker("off") is None
    assert get_reranker("disabled") is None


def test_tokenize_strips_stopwords():
    tokens = _tokenize("где находится Евроопт в Минске?")
    # «в», «и» — стоп-слова, удалены
    assert "в" not in tokens
    assert "евроопт" in tokens
    assert "минске" in tokens


def test_stems_handle_morphology():
    """«магазин/магазины/магазинов» дают одинаковый stem."""
    s1 = _stems(["магазин"])
    s2 = _stems(["магазины"])
    s3 = _stems(["магазинов"])
    # Все трое имеют общий stem (первые 5 символов)
    assert len(s1 & s2) > 0
    assert len(s1 & s3) > 0


def test_lite_reranker_factual_query():
    """На фактический FAQ-вопрос документ с прямым ответом должен подняться."""
    rr = LiteReranker()
    hits = [
        _hit("rec", "Рецепт борща: свёкла, капуста, картофель", score=0.6),
        _hit("faq", "Бонусами Еплюс можно оплатить до 99% покупки, минимум 2 копейки", score=0.5),
        _hit("promo", "Акции на колбасу в Евроопте", score=0.55),
    ]
    out = rr.rerank("сколько процентов бонусами Еплюс", hits)
    assert out[0]["id"] == "faq"
