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
# Маскированный email: «test [точка] user [собака] mail [точка] com»,
# «testточкаuserсобакаmailточкаcom», «test dot user at mail dot com».
# Закрывает претензию 25.04: бот помогал нормализовать такие email вместо
# отказа от обработки. Детектируем — маскируем — отказ.
_EMAIL_MASKED = re.compile(
    r"\b[A-Za-z0-9._\-]{1,64}"
    r"\s*(?:\[\s*собак[аи]\s*\]|собака|\(?\s*at\s*\)?|\[at\])\s*"
    r"[A-Za-z0-9\-]{1,64}"
    r"(?:\s*(?:\[\s*точк[аи]\s*\]|точка|\(?\s*dot\s*\)?|\[dot\])\s*"
    r"[A-Za-z0-9\-]{1,32}){1,3}\b",
    re.IGNORECASE | re.UNICODE,
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

# Карта лояльности с промежуточными словами между «карт…» и цифрами:
# «Я потерял карту Еплюс, **вот номер 1234567890123**» (заказчик 25.04 §6.1).
# До 60 символов между «карт» и цифрами + опциональный маркер «номер/№».
# Применяется когда основные регулярки не сработали.
_LOYALTY_GAPPED = re.compile(
    r"(?i)карт\w{0,8}.{0,60}?(?:номер|№|#)\s*(\d{10,16})\b"
)
# Также: «вот номер 12345…», «номер карты 12345…» — без явного «карт» рядом
_LOYALTY_NUMBER_MARKER = re.compile(
    r"(?i)(?:вот\s+)?номер[\s:№#]*карт\w{0,4}\s*(\d{10,16})\b|"
    r"номер[\s:№#]+(\d{12,16})\b"
)

# Карта с явным маркером «карт*» (карта/карту/карты/картой) + 13–19 цифр,
# возможно с пробелами/дефисами. Применяется когда Luhn не прошёл (тестовая
# карта, не-валидный ввод) — всё равно маскируем, раз пользователь ЯВНО
# обозначил намерение «это номер карты». Баг с прода 24.04.2026.
_CARD_MARKED = re.compile(
    r"(?i)карт\w*\s*(?:[:№#]|банк\w*)?\s*((?:\d[\s\-]?){12,18}\d)"
)

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


# Контекстные триггеры: если перед/вокруг адреса есть слова о поиске магазина —
# это запрос «Евроопт на пр-те Победителей» (топоним), а не PII пользователя.
_STORE_SEARCH_TRIGGERS = re.compile(
    r"(?ix)\b(?:"
    r"евроопт\w*|еврооп\w*|"
    r"хит(?:\b|\W)|хит-\w+|hitdiscount|"
    r"грошык\w*|groshyk|"
    r"ямигом|я-мигом|yamigom|"
    r"магазин\w*|гипермаркет\w*|супермаркет\w*|маркет\w*|минимаркет\w*|дискаунтер\w*|"
    r"автолавк\w*|"
    r"торгов\w+\s+(?:точк|объект|сет)\w*|"
    r"найти|найди|найдите|"
    r"где\s+есть|где\s+рядом|где\s+ближайш|"
    r"рядом\s+с\s+(?:дом|метро|станц)|"
    r"ближайш\w*\s+(?:магазин|евроопт|хит|грошык)"
    r")\b"
)

# Триггеры, обозначающие что пользователь даёт СВОЙ адрес (например, доставка).
# Если они есть — НЕ применяем «store-search whitelist», маскируем как PII.
_PERSONAL_ADDRESS_TRIGGERS = re.compile(
    r"(?ix)\b(?:"
    r"доставьте|привезите|отправьте|пришлите|"
    r"я\s+живу|моё?\s+место|мой\s+адрес|"
    r"по\s+моему\s+адресу|по\s+адресу\s+(?:моему|моего)|"
    r"адрес\s+доставки|адрес\s+проживани|адрес\s+регистрац|"
    r"прописка|прописан"
    r")\b"
)


def _is_store_search_context(text: str, addr_start: int, window: int = 80) -> bool:
    """True если адрес упомянут в контексте поиска магазина (топоним), а не как PII.

    Эвристика:
    - В окне ±window символов вокруг addr_start есть «store-search» триггер
      (евроопт, магазин, гипермаркет, найти, где, ближайший...).
    - И в том же окне НЕТ «personal-address» триггера
      (доставьте, я живу, мой адрес, по адресу доставки...).

    Примеры что становится whitelist'ом (не PII):
        «Евроопт на пр-те Победителей в Минске»
        «Где гипермаркет на ул. Казинца?»
        «Найти магазин ул. Притыцкого 29»
        «Грошык в Минске на ул. Рокоссовского»

    Примеры что остаётся PII (маскируется):
        «Доставьте по адресу: ул. Ленина, 10, кв. 15»
        «Я живу на ул. Гагарина, 27»
        «Мой адрес — пр-т Независимости, 48»
    """
    lo = max(0, addr_start - window)
    hi = min(len(text), addr_start + window)
    chunk = text[lo:hi]
    if _PERSONAL_ADDRESS_TRIGGERS.search(chunk):
        return False
    return bool(_STORE_SEARCH_TRIGGERS.search(chunk))

# Физический адрес: маркер (ул./проспект/переулок/бульвар) + название + номер дома.
# Опционально с квартирой. Именно маркер — якорь, чтобы не ловить "продукт N".
# \b перед маркером — иначе «ш» попадает внутри «наш», «пр.» внутри «пр. (и т.п.)»
# и регекс схватывает всё подряд.
_ADDRESS = re.compile(
    r"(?ix)"
    r"\b"
    r"(?:"
    r"ул\.?|улиц[аы]|пр(?:осп)?\.|проспект[ауе]?|пр-т\.?|пер\.|переул(?:ок|ка|ке)|"
    r"бул\.?|бульвар[ауе]?|б-р\.?|ш\.|шоссе|наб\.?|набережн\w*|пл\.?|площад[ьи]|"
    r"мкр\.?|микрорайон\w*|тракт"
    r")\s+"
    r"[А-ЯЁа-яёA-Za-z][\w\-\.]{1,40}(?:\s+[А-ЯЁа-яёA-Za-z\-\d\.]{1,40}){0,4}"
    # Номер дома: «д. 10», «дом 10» ИЛИ просто «, 10» (после запятой).
    # Закрывает 25.04 §6.1: «ул. Ленина, 10, кв. 15».
    r"(?:[,\s]+(?:д(?:ом|\.)?\s*)?\d+[а-яёА-ЯЁa-z\-/]*)?"
    # Корпус: «корп. 2», «к. 2»
    r"(?:[,\s]+(?:корп(?:ус|\.)?|к\.)\s*\d+[а-яёА-ЯЁa-z\-/]*)?"
    # Квартира/офис/помещение: «кв. 15», «офис 12», «пом. 4Н»
    r"(?:[,\s]+(?:кв(?:артира|\.)?|офис|пом(?:ещение|\.)?)\s*\d+[а-яёА-ЯЁa-z\-/]*)?"
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
    # СТМ / акции
    "родныя", "родная", "родное", "тавары", "тавар", "товары",
    "сваё", "свае", "удача", "придача", "грильфест", "гриль-фест",
    "краш", "прайс", "lucky", "goods", "крушпрайс",  # «Краш Прайс» / «Lucky Goods» — акции
    "минск", "гомель", "брест", "витебск", "гродно", "могилёв", "могилев",
    "беларусь", "республика", "санта", "клаус", "россия", "украина",
    "мария", "анастасия",  # часто встречаются как слова-нарицательные
}

# Корни брендов/топонимов — для падежных форм (евроопта/евроопту/евроопте).
# Если хоть один токен NER-spana начинается с одного из корней — это бренд,
# не ФИО.
_FIO_STOPWORD_PREFIXES = (
    "евроопт", "евроторг", "белхард", "грошык", "дискаунт",
    "еплюс", "ямигом", "едоставк", "е-доставк",
    # Названия акций / СТМ — могут попасть под NER как «фамилия»
    "еврошок", "ценопад", "выходн", "ценопад", "красн",
    "родны", "родная", "родное", "тавар",  # «Родныя тавары» — белорусская СТМ Евроопта
    "сваё", "свае", "сваи",                  # СВАЁ — собственная марка
    "удач",                                  # «Удача в придачу»
    "грильф", "хитов",                       # «ГрильФест», «Хитовая цена»
    "краш", "крашп", "крашпрайс", "lucky",   # «Краш Прайс», «Lucky Goods» — Е-доставка
    "минск", "менск", "гомел", "брест", "витебск", "гродн", "могил",
    "беларус", "лида", "лиды", "лиде", "лиду", "борисов", "орша",
    "молодечн", "солигорск", "мозыр", "пинск", "кобрин",
)


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
            # Отсеиваем бренды/топонимы Евроторга — иногда NER путается
            # и принимает «Евроопта», «Минске» за ФИО. Учитываем падежи
            # через префикс-матч.
            low = span.text.lower().strip()
            tokens = low.split()
            if any(sw in tokens for sw in _FIO_STOPWORDS):
                continue
            if any(
                t.startswith(prefix)
                for t in tokens
                for prefix in _FIO_STOPWORD_PREFIXES
            ):
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
    # Маскированный email («[точка]/[собака]» и т.п.): тоже считаем email,
    # но не накрываем уже найденный обычный email. Заменяет «[email]».
    for m in _EMAIL_MASKED.finditer(text):
        if any(mm.type == "email" and mm.start <= m.start() < mm.end for mm in matches):
            continue
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

    # 5b. Карта с явным маркером «карта» — маскируем даже без Luhn
    # (тестовые номера, заблокированные карты, опечатки). Пользователь
    # явно назвал это «картой» — значит это PII независимо от валидности.
    for m in _CARD_MARKED.finditer(text):
        if any(_overlaps(mm, m.start(1), m.end(1)) for mm in matches):
            continue
        add("card", m.start(1), m.end(1))

    # 6. Карта лояльности по маркеру (даже если Luhn не прошёл)
    for m in _LOYALTY_MARKED.finditer(text):
        # группа 1 — сами цифры
        if any(mm.start <= m.start(1) < mm.end for mm in matches):
            continue
        add("card", m.start(1), m.end(1))

    # 6a. Карта лояльности с промежуточными словами:
    # «потерял карту Еплюс, вот номер 1234567890123» — между «карт» и цифрами
    # фраза-разделитель. Заказчик 25.04 §6.1 + 28.04 PII неточность.
    for m in _LOYALTY_GAPPED.finditer(text):
        if any(mm.start <= m.start(1) < mm.end for mm in matches):
            continue
        add("card", m.start(1), m.end(1))

    # 6b. Маркер «номер карты»/«вот номер 12345...» без явного «карт» рядом.
    for m in _LOYALTY_NUMBER_MARKER.finditer(text):
        # одна из групп будет заполнена
        for grp in (1, 2):
            try:
                s, e = m.start(grp), m.end(grp)
                if s == -1:
                    continue
            except IndexError:
                continue
            if any(mm.start <= s < mm.end for mm in matches):
                continue
            add("card", s, e)
            break

    # 7. Физический адрес
    for m in _ADDRESS.finditer(text):
        # Если пересекается с phone/email — пропустить.
        if any(_overlaps(mm, m.start(), m.end()) for mm in matches):
            continue
        # Контекстный фильтр: «магазин Евроопт на пр-те Победителей в Минске»
        # — это поиск магазина, а не адрес проживания. Не маскируем.
        if _is_store_search_context(text, m.start()):
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
