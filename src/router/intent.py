"""Intent router: классификация запроса по типу для адаптивного pipeline.

Зачем: сейчас все запросы идут через одну ветку — RAG → LLM с фиксированной
temperature. Это даёт галлюцинации на фактических вопросах (где нужна
строгость) и сухие ответы на творческих (где нужна живость).

Решение: делим запросы на N интентов; каждый получает свои параметры
(temperature, RAG-приоритет, шаблон ответа, нужен ли web search).

Классификация сделана на keyword-правилах — это надёжнее ML-классификатора
для русского языка с маленькой выборкой и не требует обучения.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger()


class Intent(str, Enum):
    """Тип пользовательского запроса."""

    FAQ = "faq"  # Вопросы про условия Еплюс, оплата бонусами, утеря карты
    EPLUS = "eplus"  # Программа лояльности, регистрация, баланс
    STORES = "stores"  # Магазины, адреса, режим работы
    PROMOTIONS = "promotions"  # Акции, скидки, спеццены
    RECIPES = "recipes"  # Рецепты, блюда
    DELIVERY = "delivery"  # Ямигом, Е-доставка, доставка
    PRICES = "prices"  # Цены, наличие товаров (требует ERP — пока fallback)
    CURRENCY = "currency"  # Курс валют (внешний поиск + НБРБ)
    WEATHER = "weather"  # Погода
    SMALLTALK = "smalltalk"  # Привет / спасибо / бытовое
    GENERAL = "general"  # Всё остальное


@dataclass
class IntentResult:
    """Результат классификации с уверенностью и параметрами."""

    intent: Intent
    confidence: float
    matched_keywords: list[str]
    # Рекомендуемые параметры генерации:
    temperature: float
    require_rag: bool  # обязательно ли RAG (high score) для ответа
    allow_web: bool  # можно ли идти во внешний поиск
    deterministic: bool  # детерминированный режим (минимум креатива)


# Keyword → Intent. Порядок важен: более специфичные паттерны выше.
INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.CURRENCY: [
        "курс доллар", "курс евро", "курс рубл", "курс юан", "курс злот",
        "обменный курс", "курс валют", "сколько стоит доллар", "usd byn",
    ],
    Intent.WEATHER: [
        "погод", "прогноз погод", "температура на улице", "сколько градусов",
        "будет дождь", "солнечно", "снегопад",
    ],
    Intent.EPLUS: [
        "еплюс", "e-plus", "e plus", "программа лояльности",
        "карта лояльности", "виртуальная карта", "пластиковая карта",
        "вход в личный кабинет", "вход в лк", "забыл пароль",
        "восстановить пароль", "не могу войти",
        "войти в личный кабинет", "войти в лк", "как войти",
        "потерял карту", "утеря карты", "карта не работает",
        "стерся штрихкод", "перенос бонусов", "перенести бонус",
    ],
    Intent.PROMOTIONS: [
        "акци", "скидк", "ценопад", "распродаж", "красная цена",
        "чёрная пятниц", "черная пятниц", "цены вниз", "еврошок",
        "1+1", "счастливые минут", "промо", "спеццен",
        "бонусные дни", "удвоен", "двойные бонус",
    ],
    Intent.STORES: [
        "магазин", "адрес", "ближайший", "режим работы", "до скольки",
        "когда открывает", "когда закрывает", "круглосуточн",
        "автолавк", "гипермаркет", "супермаркет", "у дома",
        "евроопт в", "грошык в", "хит дискаунтер в", "где находит",
        "парковк", "банкомат", "аптека в магаз",
        # Адресные локации — частые в вопросах про магазины
        "проспект", "пр-т ", "пр.", "просп.", "улиц", "бульвар",
        "евроопт на ", "хит на ", "грошык на ",
    ],
    Intent.DELIVERY: [
        "ямигом", "я мигом", "е-доставка", "едоставка", "e-dostavka",
        "доставка продуктов", "доставка на дом", "заказать с доставкой",
        "привезти продукты",
        "есть ли доставка", "у евроопта доставк", "своя доставк",
        "доставк",
    ],
    Intent.RECIPES: [
        "рецепт", "как приготовить", "что приготовить", "блюдо",
        "ингредиенты", "пошаговый рецепт", "рецепт борща", "рецепт пиццы",
        "что можно сделать из", "идея ужина", "идея завтрака",
    ],
    Intent.PRICES: [
        "сколько стоит", "цена", "почём", "почем", "стоимость товара",
        "есть ли в наличии", "наличие в магазин",
    ],
    Intent.FAQ: [
        "как оплатить бонусами", "сколько процентов бонус", "оплата бонусами",
        "начисляется бонус", "как получить карту", "сколько стоит карта",
        "сколько действуют бонус", "когда сгорают бонус",
        "горячая линия", "телефон поддержки", "контакты",
        # Падежные формы и эллипсис (без слова «еплюс»)
        "бонусами", "весь чек бонусами", "чек бонусами", "оплатить чек",
        "процентов бонусами",
    ],
    Intent.SMALLTALK: [
        "привет", "здравствуй", "добрый день", "добрый вечер",
        "спасибо", "благодарю", "пока", "до свидания",
        "как дела", "как ты",
    ],
}

# Параметры генерации по интенту: жёсткие/творческие.
# Для фактологических интентов — temperature 0.0, require_rag, deterministic.
# Для творческих (рецепты, smalltalk) — повыше temperature.
INTENT_PARAMS: dict[Intent, dict] = {
    Intent.FAQ: dict(temperature=0.0, require_rag=True, allow_web=False, deterministic=True),
    Intent.EPLUS: dict(temperature=0.0, require_rag=True, allow_web=False, deterministic=True),
    Intent.STORES: dict(temperature=0.1, require_rag=True, allow_web=True, deterministic=True),
    Intent.PROMOTIONS: dict(temperature=0.1, require_rag=False, allow_web=True, deterministic=True),
    Intent.PRICES: dict(temperature=0.1, require_rag=False, allow_web=True, deterministic=True),
    Intent.DELIVERY: dict(temperature=0.2, require_rag=False, allow_web=False, deterministic=False),
    Intent.RECIPES: dict(temperature=0.5, require_rag=False, allow_web=True, deterministic=False),
    Intent.CURRENCY: dict(temperature=0.0, require_rag=False, allow_web=True, deterministic=True),
    Intent.WEATHER: dict(temperature=0.2, require_rag=False, allow_web=True, deterministic=False),
    Intent.SMALLTALK: dict(temperature=0.4, require_rag=False, allow_web=False, deterministic=False),
    Intent.GENERAL: dict(temperature=0.3, require_rag=False, allow_web=True, deterministic=False),
}


class IntentRouter:
    """Классификатор интента + поставщик параметров генерации."""

    def __init__(self):
        # Подготавливаем нормализованные триггеры (lowercase один раз)
        self._triggers: list[tuple[Intent, str]] = []
        for intent, kws in INTENT_KEYWORDS.items():
            for kw in kws:
                self._triggers.append((intent, kw.lower()))
        logger.info("intent_router_initialized", triggers=len(self._triggers))

    def classify(self, user_message: str) -> IntentResult:
        """Классифицирует запрос. Возвращает интент + параметры."""
        if not user_message or not user_message.strip():
            return self._build_result(Intent.GENERAL, 0.0, [])

        low = user_message.lower()

        # Считаем все совпадения
        matches: dict[Intent, list[str]] = {}
        for intent, kw in self._triggers:
            if kw in low:
                matches.setdefault(intent, []).append(kw)

        if not matches:
            return self._build_result(Intent.GENERAL, 0.0, [])

        # Спец-правило: если в запросе есть и promo-маркер, и eplus-маркер
        # одновременно («удвоенные бонусы Еплюс», «акции по Еплюс»), —
        # это про АКЦИЮ, не про общую программу. Закрывает 24.04 P1
        # «смешение бонусов Еплюс с Еврошоком/спеццены».
        if Intent.PROMOTIONS in matches and Intent.EPLUS in matches:
            promo_strong = any(
                kw in low for kw in [
                    "удвоен", "x2", "x3", "повышенн", "акци", "скидк",
                    "промо", "бонусные дни", "двойные бонус",
                ]
            )
            if promo_strong:
                return self._build_result(
                    Intent.PROMOTIONS,
                    0.9,
                    matches[Intent.PROMOTIONS] + matches[Intent.EPLUS],
                )

        # Выбираем интент с максимальным количеством совпадений.
        # При равенстве — приоритет по порядку INTENT_KEYWORDS
        # (более специфичные интенты определены выше).
        priority = list(INTENT_KEYWORDS.keys())
        ranked = sorted(
            matches.items(),
            key=lambda kv: (-len(kv[1]), priority.index(kv[0])),
        )
        intent, kws = ranked[0]

        # Confidence: 1 совпадение = 0.5, 2 = 0.75, 3+ = 0.9
        n = len(kws)
        if n >= 3:
            conf = 0.9
        elif n == 2:
            conf = 0.75
        else:
            conf = 0.5

        return self._build_result(intent, conf, kws)

    @staticmethod
    def _build_result(intent: Intent, confidence: float, kws: list[str]) -> IntentResult:
        params = INTENT_PARAMS[intent]
        return IntentResult(
            intent=intent,
            confidence=confidence,
            matched_keywords=kws,
            temperature=params["temperature"],
            require_rag=params["require_rag"],
            allow_web=params["allow_web"],
            deterministic=params["deterministic"],
        )
