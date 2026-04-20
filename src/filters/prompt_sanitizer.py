"""Санитизация недоверенного контекста (RAG, web-search) перед вставкой в prompt.

Защита от prompt injection: документ из knowledge base или web-результата
не должен переопределять system prompt. Три слоя защиты:

1. Удаление control-символов и нулевых байт
2. Нейтрализация XML-тегов обёртки — чтобы контент не мог «закрыть» наш тег
3. Нейтрализация известных injection-паттернов (ignore previous, new instructions, и т.п.)

Итоговый вид: XML-обёртка с явной пометкой источника и инструкция в system prompt,
что содержимое тегов — это ДАННЫЕ, а не команды.
"""
from __future__ import annotations

import re
import unicodedata


# Теги-обёртки, которые мы сами используем вокруг контекста.
# Внутри контента их нужно нейтрализовать, чтобы недоверенный текст
# не смог «закрыть» тег и выйти из области данных.
WRAPPER_TAGS = (
    "kb_document",
    "web_source",
    "knowledge_base",
    "web_context",
    "promotions",
)

# Известные injection-паттерны. Case-insensitive, стоят на границах слов.
# Заменяем на обрезанный эквивалент, чтобы не сломать валидный текст
# (например, "ignore" в цитате документа сам по себе — ок; но "ignore
# previous instructions" — нет).
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above|the\s+above)\s+(instructions?|prompts?|rules?|context)",
    r"disregard\s+(all\s+)?(previous|prior|above|the\s+above)\s+(instructions?|prompts?|rules?|context)",
    r"forget\s+(all\s+)?(previous|prior|above|the\s+above)\s+(instructions?|prompts?|rules?|context)",
    r"new\s+(instructions?|rules?|system\s+prompt)",
    r"you\s+are\s+now\s+[a-zа-я]",
    r"act\s+as\s+(if|a|an)\s+",
    r"system\s*[:>]",
    r"system\s+override",
    r"забудь\s+(все\s+|всё\s+)?(предыдущие|прошлые|прежние)\s+инструкции",
    r"игнорируй\s+(все\s+|всё\s+)?(предыдущие|прошлые|выше)\s+инструкции",
    r"новые\s+инструкции",
    r"ты\s+теперь\s+",
    # Специальные маркеры чат-шаблонов
    r"\[/?INST\]",
    r"<\|(system|user|assistant|im_start|im_end)\|?>",
    r"<<SYS>>",
    r"<</SYS>>",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Control-символы кроме \t \n \r
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control_chars(text: str) -> str:
    """Удаляет невидимые управляющие символы, нормализует пробелы."""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_RE.sub("", text)
    return text


def _neutralize_wrapper_tags(text: str) -> str:
    """Нейтрализует закрывающие/открывающие теги обёртки внутри контента."""
    for tag in WRAPPER_TAGS:
        # </tag> → </ tag>
        text = re.sub(
            rf"</\s*{tag}\s*>",
            f"</ {tag}>",
            text,
            flags=re.IGNORECASE,
        )
        # <tag ...> → < tag ...>
        text = re.sub(
            rf"<\s*{tag}(\s[^>]*)?>",
            lambda m: "< " + m.group(0)[1:],
            text,
            flags=re.IGNORECASE,
        )
    return text


def _neutralize_injections(text: str) -> str:
    """Помечает обнаруженные injection-паттерны как [filtered]."""
    return _INJECTION_RE.sub("[filtered]", text)


def sanitize_context_text(text: str, max_chars: int = 2000) -> str:
    """Очистить ОДИН чанк контекста (документ/web-результат) перед вставкой в prompt.

    Args:
        text: сырой текст из RAG/web
        max_chars: обрезать до этого числа символов

    Returns:
        Санитизированный текст, безопасный для вставки внутрь XML-тега.
    """
    if not text:
        return ""
    text = _strip_control_chars(text)
    text = _neutralize_wrapper_tags(text)
    text = _neutralize_injections(text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def build_kb_block(documents: list[dict]) -> str:
    """Собрать блок knowledge base в виде <kb_document> тегов.

    Args:
        documents: [{"id": ..., "text": ..., "score": ...}]

    Returns:
        Строка с XML-обёрнутыми документами или пометкой отсутствия данных.
    """
    if not documents:
        return "<knowledge_base>\n(нет релевантных документов)\n</knowledge_base>"
    parts = ["<knowledge_base>"]
    for d in documents:
        doc_id = str(d.get("id", "")).replace('"', "'")[:64]
        score = d.get("score", 0.0)
        clean = sanitize_context_text(d.get("text", ""))
        parts.append(f'<kb_document id="{doc_id}" score="{score:.2f}">')
        parts.append(clean)
        parts.append("</kb_document>")
    parts.append("</knowledge_base>")
    return "\n".join(parts)


def build_web_block(results: list[dict]) -> str:
    """Собрать блок web-результатов в виде <web_source> тегов.

    Args:
        results: [{"title": ..., "url": ..., "content": ...}]

    Returns:
        Строка с XML-обёрнутыми источниками или пустая если results пуст.
    """
    if not results:
        return ""
    parts = ["<web_context>"]
    for r in results:
        url = str(r.get("url", "")).strip()[:500]
        # url только http/https схемы
        if not re.match(r"^https?://", url):
            continue
        title = sanitize_context_text(r.get("title", ""), max_chars=200)
        content = sanitize_context_text(r.get("content", ""), max_chars=800)
        # В атрибуте url экранируем кавычки
        safe_url = url.replace('"', "%22")
        parts.append(f'<web_source url="{safe_url}" title="{title}">')
        parts.append(content)
        parts.append("</web_source>")
    parts.append("</web_context>")
    return "\n".join(parts)
