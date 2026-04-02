import re
import structlog

logger = structlog.get_logger()

BLOCKED_KEYWORDS = [
    # Политика
    "политик", "президент", "партия", "партию", "партии",
    "оппозиц", "санкци", "протест", "государств",
    "выборы", "выборов", "выбора",
    "лукашенк", "путин", "зеленск", "трамп", "байден",
    # Религия
    "религи", "церков", "церкви", "мечет", "мечети",
    "молитв", "ислам", "христиан", "буддизм", "иудаизм",
    # Конкуренты
    "гиппо", "белмаркет", "соседи", "корон", "остров чистоты", "mile.by",
]

BLOCKED_PATTERNS_RE = [
    # Нецензурная лексика
    re.compile(r"(бля[дть]|блять|ху[йяе]|пизд|ебат|сук[аи]|мудак|пидор)", re.IGNORECASE),
    # "бог" как отдельное слово (не "богат", "много")
    re.compile(r"(?<!\w)бог[ауе]?(?!\w)", re.IGNORECASE),
]

POLITE_REFUSAL = (
    "Извините, я могу помочь вам с вопросами о наших магазинах, "
    "акциях, рецептах и услугах. Давайте поговорим о чём-то из этого! 😊\n\n"
    "Попробуйте спросить:\n"
    "• Какие сейчас акции?\n"
    "• Что приготовить на ужин?\n"
    "• Как работает доставка?"
)


def check_content(text: str) -> tuple[bool, str | None]:
    """Check if text contains blocked content.

    Returns:
        (is_allowed, refusal_message) - True if content is allowed, None if no refusal needed.
    """
    text_lower = text.lower()

    # Keyword matching (case-insensitive substring)
    for keyword in BLOCKED_KEYWORDS:
        if keyword in text_lower:
            logger.info("content_filtered", matched=keyword, method="keyword")
            return False, POLITE_REFUSAL

    # Regex patterns (profanity, edge cases)
    for pattern in BLOCKED_PATTERNS_RE:
        match = pattern.search(text)
        if match:
            logger.info("content_filtered", matched=match.group(), method="regex")
            return False, POLITE_REFUSAL

    return True, None
