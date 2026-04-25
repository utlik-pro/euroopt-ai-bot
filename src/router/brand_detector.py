"""Brand & city detector — определяет, про какой бренд/город спрашивает пользователь.

Закрывает претензию из отчёта 24.04 P1: «вопрос про Евроопт не должен
подмешивать Хит/Грошык; вопрос про Лиду не должен показывать Гродно».

Просто keyword-матчинг — для русского с малым словарём это надёжнее,
чем ML.
"""
from __future__ import annotations

import re

# Канонизированные бренды (в той же форме, что в data/stores/all_stores.json)
BRAND_PATTERNS: dict[str, list[str]] = {
    "Евроопт": [
        r"\bевроопт\w*",
        r"\beuroopt\w*",
        r"\bевроторг\w*",
    ],
    "Хит": [
        r"\bхит[\s\-]?дискаунтер\w*",
        r"\bхитдис\w*",
        r"\bхит\b(?!\s*(?:сезон|лет|весн|зим))",  # не «хит сезона»
    ],
    "Грошык": [
        r"\bгрошык\w*",
        r"\bгрошик\w*",
    ],
}

# Города Беларуси — для детекции «магазины в Минске», «Евроопт Лида»
# Покрываем основные областные центры + большие районные.
CITY_PATTERNS: dict[str, list[str]] = {
    "Минск": [r"\bминск\w*", r"\bменск\w*"],
    "Гомель": [r"\bгомел\w*"],
    "Витебск": [r"\bвитебск\w*"],
    "Гродно": [r"\bгродн\w*"],
    "Брест": [r"\bбрест\w*"],
    "Могилёв": [r"\bмогил[её]в\w*"],
    "Лида": [r"\bлид[аеуы]\b"],
    "Барановичи": [r"\bбаранович\w*"],
    "Борисов": [r"\bборисов\w*"],
    "Орша": [r"\bорш[аеуы]\b"],
    "Солигорск": [r"\bсолигорск\w*"],
    "Мозырь": [r"\bмозыр\w*"],
    "Молодечно": [r"\bмолодечн\w*"],
    "Бобруйск": [r"\bбобруйск\w*"],
    "Слуцк": [r"\bслуцк\w*"],
    "Полоцк": [r"\bполоцк\w*"],
    "Новополоцк": [r"\bновополоцк\w*"],
    "Жлобин": [r"\bжлобин\w*"],
    "Светлогорск": [r"\bсветлогорск\w*"],
    "Речица": [r"\bречиц[аеуы]\b"],
    "Калинковичи": [r"\bкалинкович\w*"],
    "Сморгонь": [r"\bсморгон\w*"],
    "Пинск": [r"\bпинск\w*"],
    "Кобрин": [r"\bкобрин\w*"],
}


def detect_brand(text: str) -> str | None:
    """Вернуть каноническое имя бренда или None.

    Если в запросе несколько брендов — приоритет тот, что упомянут первым.
    """
    if not text:
        return None
    low = text.lower()
    earliest_pos = len(low) + 1
    earliest_brand: str | None = None
    for brand, patterns in BRAND_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, low, re.UNICODE)
            if m and m.start() < earliest_pos:
                earliest_pos = m.start()
                earliest_brand = brand
    return earliest_brand


def detect_city(text: str) -> str | None:
    """Вернуть каноническое имя города или None.

    Если городов несколько — приоритет первого.
    """
    if not text:
        return None
    low = text.lower()
    earliest_pos = len(low) + 1
    earliest_city: str | None = None
    for city, patterns in CITY_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, low, re.UNICODE)
            if m and m.start() < earliest_pos:
                earliest_pos = m.start()
                earliest_city = city
    return earliest_city


def detect_format(text: str) -> str | None:
    """Грубое определение запрашиваемого формата магазина.

    Возвращает каноническую категорию (как в data/stores/all_stores.json) или None.
    """
    if not text:
        return None
    low = text.lower()
    if re.search(r"\bавтолавк\w*", low):
        return "Автолавка"
    if re.search(r"\bгипермаркет\w*|\bгипер\w*", low):
        return "Гипермаркет"
    if re.search(r"\bсупермаркет\w*", low):
        return "Супермаркет"
    if re.search(r"\bминимаркет\w*", low):
        return "Минимаркет (село)"
    return None
