"""Тесты ResponseCache — общий кэш ответов с TTL для повторяемости."""
import time

from src.cache import ResponseCache


def test_basic_set_get():
    c = ResponseCache(ttl_seconds=60)
    c.put("Какие сегодня акции?", "ответ Х")
    # «сегодня» — ephemeral marker, кэш должен пропустить
    assert c.get("Какие сегодня акции?") is None


def test_basic_set_get_non_ephemeral():
    c = ResponseCache(ttl_seconds=60)
    c.put("Сколько процентов бонусов начисляется?", "0,5% и 1%")
    assert c.get("Сколько процентов бонусов начисляется?") == "0,5% и 1%"


def test_normalization_paraphrase():
    """Парафразы одного вопроса должны давать один кэш-ключ."""
    c = ResponseCache(ttl_seconds=60)
    c.put("магазины Евроопт в Лиде", "адреса A, B, C")
    # Перестановка слов → тот же ключ через canonicalize_for_cache
    assert c.get("Лида Евроопт магазины") == "адреса A, B, C"


def test_pii_placeholders_not_cached():
    """Сообщения с PII-плейсхолдерами не кэшируются."""
    c = ResponseCache(ttl_seconds=60)
    msg = "Мой номер [телефон], позвоните пожалуйста"
    c.put(msg, "ответ X")
    assert c.get(msg) is None
    # Проверяем что skip учтён
    assert c.stats()["skips"] >= 1


def test_pii_email_placeholder():
    c = ResponseCache(ttl_seconds=60)
    c.put("Мой [email] — что делать?", "ответ")
    assert c.get("Мой [email] — что делать?") is None


def test_ephemeral_marker_today():
    c = ResponseCache(ttl_seconds=60)
    c.put("какая погода сегодня?", "X")
    # Должно вернуть None, не кладём
    assert c.get("какая погода сегодня?") is None


def test_ephemeral_marker_now():
    c = ResponseCache(ttl_seconds=60)
    c.put("что сейчас в Евроопте?", "X")
    assert c.get("что сейчас в Евроопте?") is None


def test_ttl_expiry():
    """Через TTL запись должна сама удалиться."""
    c = ResponseCache(ttl_seconds=0)  # моментально протухает
    c.put("Сколько стоит карта Еплюс?", "99 копеек")
    # Любой get после нулевого TTL → miss
    time.sleep(0.001)
    assert c.get("Сколько стоит карта Еплюс?") is None


def test_lru_eviction():
    c = ResponseCache(ttl_seconds=60, max_entries=3)
    c.put("вопрос один про карту", "A")
    c.put("вопрос два про бонусы", "B")
    c.put("вопрос три про оплату", "C")
    c.put("вопрос четыре про доставку", "D")  # должен вытеснить «один»
    assert c.get("вопрос один про карту") is None
    assert c.get("вопрос четыре про доставку") == "D"


def test_lru_touches_recent_entries():
    """get() обновляет позицию (LRU)."""
    c = ResponseCache(ttl_seconds=60, max_entries=3)
    c.put("вопрос один про карту", "A")
    c.put("вопрос два про бонусы", "B")
    c.put("вопрос три про оплату", "C")
    c.get("вопрос один про карту")  # делаем «один» свежим
    c.put("вопрос четыре про доставку", "D")  # теперь должен вылететь «два»
    assert c.get("вопрос один про карту") == "A"
    assert c.get("вопрос два про бонусы") is None


def test_empty_message_skipped():
    c = ResponseCache(ttl_seconds=60)
    c.put("", "X")
    assert c.get("") is None


def test_empty_response_not_stored():
    c = ResponseCache(ttl_seconds=60)
    c.put("Сколько стоит карта Еплюс?", "")
    assert c.get("Сколько стоит карта Еплюс?") is None


def test_clear():
    c = ResponseCache(ttl_seconds=60)
    c.put("Вопрос про начисление", "A")
    c.clear()
    assert c.get("Вопрос про начисление") is None


def test_stats_track_hits_misses():
    c = ResponseCache(ttl_seconds=60)
    c.put("Сколько процентов начисляется бонусов?", "X")
    c.get("Сколько процентов начисляется бонусов?")  # hit
    c.get("Совершенно другой вопрос про начисление")  # miss
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5


def test_stats_size():
    c = ResponseCache(ttl_seconds=60, max_entries=10)
    c.put("Первый каноничный вопрос", "A")
    c.put("Второй каноничный вопрос", "B")
    assert c.stats()["size"] == 2
