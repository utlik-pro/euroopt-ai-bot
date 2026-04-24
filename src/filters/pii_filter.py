"""PII-фильтр: обнаружение и маскирование персональных данных.

Реализует ДС №1 к Договору 2703/26-01 (п. 2.1.1 + Приложение №1).

Модель защиты: данные, которые прислал ПОЛЬЗОВАТЕЛЬ в своём сообщении, не
должны попасть ни в LLM, ни во внешний поиск (Tavily), ни в JSONL-логи.
Публичная информация — телефоны магазинов, адреса точек, ФИО публичных лиц —
НЕ маскируется: бот свободно использует её в RAG-контексте и ответах.

Точки применения (в pipeline.py):
  1. Вход в LLM — `masked_user_message`
  2. Web-запрос в Tavily — `mask_pii(web_query)`
  3. history чата — сохраняется маскированная версия user_message
  4. Поле `user_message` в JSONL-логе — маскированное
  5. Bot-handler (src/bot/main.py) — log_message("in", ...) и structlog превью

Точки, где ПДн НЕ маскируются (намеренно):
  - response.text от LLM — идёт пользователю как есть
  - bot_response в JSONL — как есть
  - RAG-документы и web-результаты в system prompt — как есть

Типы ПДн: телефон (+375/8029/intl), email, банк. карта (Luhn),
карта лояльности, паспорт РБ, ID РБ, физ. адрес, ФИО, дата рождения.

Два слоя детекции:
  1. **Regex** — быстрый, детерминированный. Работает всегда.
  2. **NER (natasha)** — для ФИО без маркеров и без типовых суффиксов
     («Влад Сидоров звонил»). Ленивая инициализация (~400 МБ RAM, ~2 сек).
     Отключается через `PII_USE_NER=false`. Без natasha — только regex.

Использование:
    from src.filters.pii_filter import mask_pii
    masked, types = mask_pii(text)
    if types:
        logger.info("pii_detected", types=types)  # значения НЕ логируем
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Переключатели NER
_NER_ENABLED = os.environ.get("PII_USE_NER", "true").lower() == "true"
# Минимальная длина ФИО от NER (защита от шума «я», «он»).
_NER_MIN_LEN = 3


# ==================== Плейсхолдеры ====================
# Стабильные метки — LLM должен понимать, что это редактированное поле.
PLACEHOLDERS = {
    "phone": "[телефон]",
    "email": "[email]",
    "card": "[номер_карты]",
    "passport": "[паспорт]",
    "id_by": "[ID]",
    "address": "[адрес]",
    "fio": "[ФИО]",
    "date": "[дата]",
}


# ==================== Regex-паттерны ====================

# Телефоны РБ:
#   +375 29 123-45-67, +375(29)123-45-67, 375 29 1234567, 8029 123-45-67
#   Мобильные: 25, 29, 33, 44; городские: 17 (Минск) и др.
_PHONE_BY_INTL = re.compile(
    r"\+?\s*375[\s\-\(\)]*(?:17|25|29|33|44)[\s\-\(\)]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
)
_PHONE_BY_LOCAL = re.compile(
    r"\b8[\s\-]?0?[\s\-]?(?:17|25|29|33|44)[\s\-\(\)]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
)
# Международные (не-РБ): +XX ... минимум 10 цифр после плюса
_PHONE_INTL = re.compile(
    r"\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}(?:[\s\-]?\d{1,4})?"
)

# Email — RFC-подобный, без перфекционизма.
_EMAIL = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9._%+\-]{0,63}@[A-Za-z0-9][A-Za-z0-9\-]{0,62}(?:\.[A-Za-z0-9\-]{1,62}){1,3}\b"
)

# Банковские карты: 13–19 цифр, сгруппированные пробелами/дефисами или сплошные.
# Проверяем Luhn, чтобы не маскировать случайные числа (дата, артикул).
_CARD_CANDIDATE = re.compile(
    r"\b(?:\d[\s\-]?){12,18}\d\b"
)

# Карта лояльности Еплюс / магазина: 10–16 цифр подряд, без разделителей
# (иначе пересечётся с банковской). Маркерные фразы помогают.
_LOYALTY_MARKED = re.compile(
    r"(?i)(?:карт[аеыу]?\s*(?:лояльн\w*|еплюс|e\-?plus|евроопт\w*|клуб\w*)?[:\s№#]*)"
    r"(\d{10,16})"
)
_LOYALTY_BARE = re.compile(r"\b\d{10,13}\b")  # применяется только после маркера

# Паспорт РБ (серия + номер): 2 буквы (кирилл./лат.) + 7 цифр.
# Пример: MP1234567, МР1234567, HB9876543, АВ1234567.
_PASSPORT_BY = re.compile(
    r"\b[A-ZА-ЯЁ]{2}\s?\d{7}\b"
)
# Маркерная форма: "паспорт MP1234567" — захватываем даже если буквы латиницей с цифрами.
_PASSPORT_MARKED = re.compile(
    r"(?i)(?:паспорт\w*[:\s№#]*)([A-ZА-ЯЁa-zа-яё]{2}\s?\d{7})"
)

# Идентификационный номер РБ (14 символов): 7 цифр + буква + 3 цифры + 2 буквы + 1 цифра.
# Пример: 1234567A123PB1. Регистр букв латиница/кириллица.
_ID_RB = re.compile(
    r"\b\d{7}[A-ZА-ЯЁ]\d{3}[A-ZА-ЯЁ]{2}\d\b"
)

# Физический адрес: маркер (ул./проспект/переулок/бульвар) + название + номер дома.
# Опционально с квартирой. Именно маркер — якорь, чтобы не ловить "продукт N".
# \b перед маркером — иначе «ш» попадает внутри «наш», «пр.» внутри «пр. (и т.п.)»
# и регекс схватывает всё подряд.
_ADDRESS = re.compile(
    r"(?ix)"
    r"\b"
    r"(?:"
    r"ул\.?|улиц[аы]|пр(?:осп)?\.|проспект[ауе]?|пер\.|переул(?:ок|ка|ке)|"
    r"бул\.?|бульвар[ауе]?|ш\.|шоссе|наб\.?|набережн\w*|пл\.?|площад[ьи]|"
    r"мкр\.?|микрорайон\w*|тракт"
    r")\s+"
    r"[А-ЯЁа-яёA-Za-z][\w\-\.]{1,40}(?:\s+[А-ЯЁа-яёA-Za-z\-\d\.]{1,40}){0,4}"
    r"(?:[,\s]+д(?:ом|\.)?\s*\d+[а-яёА-ЯЁa-z\-/]*)?"
    r"(?:[,\s]+кв(?:артира|\.)?\s*\d+[а-яёА-ЯЁa-z\-/]*)?"
)

# ФИО по маркеру («меня зовут …», «ФИО: …», «фамилия …»).
# Допускаем от 1 до 3 слов подряд — «фамилия Иванов» тоже ПДн.
_FIO_MARKED = re.compile(
    r"(?ix)"
    r"(?:меня\s+зовут|зовут\s+меня|моё\s+имя|мо[её]\s+ф\.?и\.?о\.?|"
    r"ф\.?\s*и\.?\s*о\.?[:\s]+|фамилия(?:\s+имя\s+отчество)?[:\s]+|"
    r"имя[:\s]+|пациент[:\s]+|клиент[:\s]+)"
    r"\s*"
    r"([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2})"
)
# ФИО по суффиксам фамилий (-ов/-ев/-ин/-ын/-ский/-цкий/-ко/-ук/-юк/-ич/-аш/-ян/-ая/-ова).
# Требуем минимум 2 слова подряд, одно из которых «похоже на фамилию».
_RU_SURNAME_SUFFIXES = (
    "ов", "ова", "ев", "ева", "ёв", "ёва",
    "ин", "ина", "ын", "ына",
    "ский", "ская", "цкий", "цкая", "ко", "ук", "юк",
    "ич", "ыч", "енко", "ян", "ец",
)
_FIO_BY_SURNAME = re.compile(
    r"\b([А-ЯЁ][а-яё]{1,20}(?:" + "|".join(_RU_SURNAME_SUFFIXES) + r"))"
    r"\s+([А-ЯЁ][а-яё]{1,20})"
    r"(?:\s+([А-ЯЁ][а-яё]{1,20}))?\b"
)

# Дата рождения: маркер + дата в формате дд.мм.гггг или дд/мм/гггг.
_DOB = re.compile(
    r"(?ix)"
    r"(?:дата\s+рожд\w*|д\.?р\.?|дата\s+рождения|родил(?:ся|ась)\s+)"
    r"[:\s]*"
    r"(\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4})"
)

# Слова-исключения, которые начинаются с заглавной и могут попасть под ФИО.
# Добавляем бренды/топонимы Евроторга, чтобы не маскировать их как ФИО.
_FIO_STOPWORDS = {
    "евроопт", "грошык", "хит", "дискаунтер", "белхард", "евроторг",
    "еплюс", "еплюса", "еплюсу", "e-plus", "eplus",
    "едоставка", "едоставки", "e-dostavka",
    "ямигом", "я-мигом",
    "минск", "гомель", "брест", "витебск", "гродно", "могилёв", "могилев",
    "беларусь", "республика", "санта", "клаус", "россия", "украина",
    "мария", "анастасия",  # часто встречаются как слова-нарицательные
}


# ==================== NER (natasha) — ленивая инициализация ====================

_NER_LOCK = threading.Lock()
_NER_STATE: dict = {"loaded": False, "available": False, "segmenter": None, "tagger": None, "doc_cls": None}


def _init_ner() -> bool:
    """Единоразовая инициализация natasha. Возвращает True если NER доступен."""
    if _NER_STATE["loaded"]:
        return _NER_STATE["available"]
    with _NER_LOCK:
        if _NER_STATE["loaded"]:
            return _NER_STATE["available"]
        _NER_STATE["loaded"] = True
        if not _NER_ENABLED:
            logger.info("pii_ner_disabled", reason="env_flag")
            return False
        try:
            from natasha import Segmenter, NewsEmbedding, NewsNERTagger, Doc
            _NER_STATE["segmenter"] = Segmenter()
            _NER_STATE["tagger"] = NewsNERTagger(NewsEmbedding())
            _NER_STATE["doc_cls"] = Doc
            _NER_STATE["available"] = True
            logger.info("pii_ner_ready")
            return True
        except ImportError:
            logger.warning("pii_ner_unavailable", reason="natasha_not_installed")
            return False
        except Exception as e:
            logger.warning("pii_ner_init_failed", error=str(e))
            return False


def _ner_find_fio(text: str) -> list[tuple[int, int]]:
    """Найти PER-сущности natasha. Возвращает список (start, end) в символах."""
    if not _init_ner():
        return []
    try:
        doc = _NER_STATE["doc_cls"](text)
        doc.segment(_NER_STATE["segmenter"])
        doc.tag_ner(_NER_STATE["tagger"])
        spans = []
        for span in doc.spans:
            if span.type != "PER":
                continue
            if (span.stop - span.start) < _NER_MIN_LEN:
                continue
            # Отсеиваем бренды/топонимы Евроторга — иногда NER путается.
            low = span.text.lower().strip()
            if any(sw in low.split() for sw in _FIO_STOPWORDS):
                continue
            spans.append((span.start, span.stop))
        return spans
    except Exception as e:
        logger.warning("pii_ner_error", error=str(e))
        return []


# ==================== Luhn (для банковских карт) ====================

def _luhn_check(digits: str) -> bool:
    """Проверка контрольной суммы Luhn. digits — только цифры."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - ord("0")
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ==================== API ====================

@dataclass(frozen=True)
class PIIMatch:
    type: str      # phone/email/card/passport/id_by/address/fio/date
    start: int
    end: int
    placeholder: str


def detect_pii(text: str) -> list[PIIMatch]:
    """Найти все вхождения ПДн. Возвращает список без пересечений.

    При пересечениях побеждает более длинный/более специфичный матч
    (паспорт > ФИО по суффиксу, карта > loyalty и т.п.).
    """
    if not text:
        return []

    matches: list[PIIMatch] = []

    def add(type_: str, start: int, end: int):
        matches.append(PIIMatch(type_, start, end, PLACEHOLDERS[type_]))

    # 1. Телефоны (РБ intl → РБ local → intl) — приоритет выше, чем у «карты»,
    # чтобы +375 29 123-45-67 не был принят за карту.
    for rx in (_PHONE_BY_INTL, _PHONE_BY_LOCAL):
        for m in rx.finditer(text):
            add("phone", m.start(), m.end())

    # Международные: отдельно — могут конфликтовать с БЫ-форматом, поэтому
    # добавляем только если не накрывает уже найденный телефон.
    for m in _PHONE_INTL.finditer(text):
        # пропускаем, если уже есть phone-матч в этой позиции
        if any(mm.type == "phone" and mm.start <= m.start() < mm.end for mm in matches):
            continue
        # Минимум 10 цифр в числе, иначе это не телефон
        digits = re.sub(r"\D", "", m.group())
        if len(digits) >= 10 and len(digits) <= 15:
            add("phone", m.start(), m.end())

    # 2. Email
    for m in _EMAIL.finditer(text):
        add("email", m.start(), m.end())

    # 3. Паспорт по маркеру (группа — сам номер, но маскируем всё вместе).
    for m in _PASSPORT_MARKED.finditer(text):
        add("passport", m.start(1), m.end(1))
    # Паспорт без маркера: 2 буквы + 7 цифр. Осторожно — может поймать код товара.
    for m in _PASSPORT_BY.finditer(text):
        # Пропускаем, если уже ID РБ или уже паспорт по маркеру в этой позиции.
        if any(mm.start <= m.start() < mm.end for mm in matches):
            continue
        add("passport", m.start(), m.end())

    # 4. ID РБ (14-символьный)
    for m in _ID_RB.finditer(text):
        if any(mm.start <= m.start() < mm.end for mm in matches):
            continue
        add("id_by", m.start(), m.end())

    # 5. Банковские карты: находим кандидатов (13–19 цифр), валидируем Luhn.
    for m in _CARD_CANDIDATE.finditer(text):
        if any(mm.start <= m.start() < mm.end for mm in matches):
            continue  # уже телефон/паспорт
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and _luhn_check(digits):
            add("card", m.start(), m.end())

    # 6. Карта лояльности по маркеру (даже если Luhn не прошёл)
    for m in _LOYALTY_MARKED.finditer(text):
        # группа 1 — сами цифры
        if any(mm.start <= m.start(1) < mm.end for mm in matches):
            continue
        add("card", m.start(1), m.end(1))

    # 7. Физический адрес
    for m in _ADDRESS.finditer(text):
        # Если пересекается с phone/email — пропустить.
        if any(_overlaps(mm, m.start(), m.end()) for mm in matches):
            continue
        add("address", m.start(), m.end())

    # 8. ФИО по маркеру (группа — имя)
    for m in _FIO_MARKED.finditer(text):
        if any(_overlaps(mm, m.start(1), m.end(1)) for mm in matches):
            continue
        add("fio", m.start(1), m.end(1))

    # 9. ФИО по суффиксу фамилии (Иванов Иван Иванович)
    for m in _FIO_BY_SURNAME.finditer(text):
        if any(_overlaps(mm, m.start(), m.end()) for mm in matches):
            continue
        # фильтр стоп-слов
        parts = [g for g in m.groups() if g]
        if any(p.lower() in _FIO_STOPWORDS for p in parts):
            continue
        add("fio", m.start(), m.end())

    # 10. Дата рождения по маркеру (группа 1 — сама дата)
    for m in _DOB.finditer(text):
        if any(_overlaps(mm, m.start(1), m.end(1)) for mm in matches):
            continue
        add("date", m.start(1), m.end(1))

    # 11. ФИО через NER — ловит имена без маркеров и без типовых суффиксов.
    # «Влад Сидоров звонил» — regex по суффиксам не найдёт, NER найдёт.
    for start, end in _ner_find_fio(text):
        # Пересечение с уже найденным (например, regex-ФИО) — пропускаем.
        if any(_overlaps(mm, start, end) for mm in matches):
            continue
        add("fio", start, end)

    # Сортируем, убираем пересечения (оставляем более ранний + длинный)
    matches.sort(key=lambda mm: (mm.start, -(mm.end - mm.start)))
    cleaned: list[PIIMatch] = []
    last_end = -1
    for mm in matches:
        if mm.start < last_end:
            continue
        cleaned.append(mm)
        last_end = mm.end
    return cleaned


def _overlaps(m: PIIMatch, start: int, end: int) -> bool:
    return not (end <= m.start or start >= m.end)


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Замаскировать ПДн в тексте.

    Returns:
        (masked_text, [types]) — список ТИПОВ (не значений!) найденных ПДн,
        в порядке появления, с повторами. Для логирования.
    """
    if not text:
        return text, []

    matches = detect_pii(text)
    if not matches:
        return text, []

    out: list[str] = []
    cursor = 0
    types: list[str] = []
    for m in matches:
        out.append(text[cursor:m.start])
        out.append(m.placeholder)
        types.append(m.type)
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out), types


def has_pii(text: str) -> bool:
    """Быстрая проверка — есть ли ПДн в тексте."""
    return bool(detect_pii(text))
