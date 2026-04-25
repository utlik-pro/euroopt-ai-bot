"""Тесты SourceTagger — маркер источника в ответах на рецепты."""
from src.postprocess.source_tagger import SourceTagger, ResponseSource


def _recipe_hit(score: float = 0.7) -> dict:
    return {
        "id": "recipe_1",
        "text": "Рецепт борща",
        "score": score,
        "metadata": {"category": "recipe", "name": "Борщ"},
    }


def _web_hit() -> dict:
    return {"id": "w1", "text": "...", "url": "https://eda.ru/recipe/borsch"}


def _faq_hit() -> dict:
    return {
        "id": "faq_1",
        "text": "...",
        "score": 0.9,
        "metadata": {"category": "faq"},
    }


def test_internal_source_when_recipe_in_rag():
    t = SourceTagger()
    res = t.tag("Вот рецепт борща...", rag_results=[_recipe_hit()], web_results=[])
    assert res.source == ResponseSource.INTERNAL
    assert "из базы Евроопта" in res.text


def test_external_source_when_only_web():
    t = SourceTagger()
    res = t.tag("Вот рецепт...", rag_results=[], web_results=[_web_hit()])
    assert res.source == ResponseSource.EXTERNAL
    assert "Общий вариант рецепта из интернета" in res.text


def test_mixed_when_both():
    t = SourceTagger()
    res = t.tag("Вот рецепт...", rag_results=[_recipe_hit()], web_results=[_web_hit()])
    assert res.source == ResponseSource.MIXED
    assert "база" in res.text.lower() or "Из базы" in res.text


def test_none_when_no_sources():
    """Без RAG-рецептов и без web — без маркера."""
    t = SourceTagger()
    res = t.tag("Общий ответ.", rag_results=[], web_results=[])
    assert res.source == ResponseSource.NONE
    assert res.text == "Общий ответ."


def test_low_score_recipe_is_not_internal():
    """RAG-рецепт с низким score — не считаем его источником."""
    t = SourceTagger(min_recipe_score=0.55)
    res = t.tag("Рецепт", rag_results=[_recipe_hit(score=0.3)], web_results=[])
    assert res.source == ResponseSource.NONE


def test_faq_does_not_count_as_recipe_source():
    """FAQ-документ — не рецепт. INTERNAL для рецептов не активируется."""
    t = SourceTagger()
    res = t.tag(
        "Что-то про FAQ",
        rag_results=[_faq_hit()],
        web_results=[],
    )
    assert res.source == ResponseSource.NONE


def test_empty_response():
    t = SourceTagger()
    res = t.tag("", rag_results=[_recipe_hit()], web_results=[])
    assert res.source == ResponseSource.NONE
    assert res.text == ""


def test_no_double_tagging():
    """Если у ответа уже есть префикс источника — не добавляем второй."""
    t = SourceTagger()
    already_tagged = "📋 *Рецепт из базы Евроопта.*\n\nВот рецепт..."
    res = t.tag(already_tagged, rag_results=[_recipe_hit()], web_results=[])
    # Эмодзи-префикс встречается ровно один раз
    assert res.text.count("📋") == 1


def test_prefix_starts_with_emoji_for_internal():
    t = SourceTagger()
    res = t.tag("ответ", rag_results=[_recipe_hit()], web_results=[])
    # Должен начинаться с 📋 (эмодзи закладки/файла)
    assert res.text.lstrip().startswith("📋")


def test_prefix_starts_with_emoji_for_external():
    t = SourceTagger()
    res = t.tag("ответ", rag_results=[], web_results=[_web_hit()])
    # Должен начинаться с 🌐 (эмодзи веба)
    assert res.text.lstrip().startswith("🌐")


def test_detect_source_method_directly():
    """detect_source без модификации текста."""
    t = SourceTagger()
    assert t.detect_source([_recipe_hit()], []) == ResponseSource.INTERNAL
    assert t.detect_source([], [_web_hit()]) == ResponseSource.EXTERNAL
    assert t.detect_source([_recipe_hit()], [_web_hit()]) == ResponseSource.MIXED
    assert t.detect_source([], []) == ResponseSource.NONE
    assert t.detect_source([_faq_hit()], []) == ResponseSource.NONE
