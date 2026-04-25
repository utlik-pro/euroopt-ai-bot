"""Grounding verifier: проверяет, что фактологические утверждения в ответе LLM
действительно подкреплены источниками (RAG / web).

Идея: LLM может «сгенерировать» конкретный номер телефона, цену, процент,
адрес или режим работы, которых нет в источниках. Это галлюцинация. После
генерации мы проходимся по ответу регулярными выражениями, ищем такие
конкретные утверждения и проверяем их наличие в `<knowledge_base>` и
`<web_context>`. Несоответствия — флагируем и (при включённом auto-fix)
заменяем на безопасную формулировку «уточните на сайте».

Это НЕ полноценный fact-checker (для него нужен отдельный LLM-вызов), а
дешёвая страховка против самых частых багов из тестирования.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# Известные «безопасные» факты — эти числа разрешены даже если их нет в RAG
# (они зафиксированы как канонические в системе и в SYSTEM_PROMPT).
SAFE_FACTS = {
    "phones": frozenset(["+375 44 788 88 80", "+375447888880", "375447888880"]),
    "percents": frozenset(["0,5%", "0.5%", "1%", "99%"]),
    "currency_amounts": frozenset(["99 копеек", "2 копейки", "1 копейка"]),
    "expiration": frozenset(["365 дней"]),
    "store_count": frozenset(["1000", "более 1000"]),
}

# Регулярки для извлечения конкретики из ответа
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
PERCENT_RE = re.compile(r"\b\d+[,.]?\d*\s*%")
PRICE_RE = re.compile(r"\b\d+[,.]?\d*\s*(?:руб|бел\s*руб|byn|коп|копе[ек]\w*)\b", re.IGNORECASE)
TIME_RE = re.compile(
    r"\b\d{1,2}[:.]\d{2}\s*(?:[-–—]|до|по)\s*\d{1,2}[:.]\d{2}",
    re.IGNORECASE,
)


@dataclass
class VerifyIssue:
    """Найденная подозрительная конкретика."""

    kind: str  # phone | percent | price | time
    value: str
    context_snippet: str  # 30 символов вокруг для логов


@dataclass
class VerifyResult:
    """Результат проверки."""

    is_grounded: bool
    issues: list[VerifyIssue] = field(default_factory=list)
    cleaned_text: str = ""  # текст с заменами (если auto_fix=True)


class GroundingVerifier:
    """Проверяет фактологию ответа против источников.

    Использование:
        verifier = GroundingVerifier()
        result = verifier.verify(response_text, kb_text, web_text)
        if not result.is_grounded:
            response_text = result.cleaned_text  # либо логируем для ручного разбора
    """

    def __init__(self, *, auto_fix: bool = False):
        self.auto_fix = auto_fix

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Нормализация для подстрочного поиска: убрать пробелы и пунктуацию."""
        t = text.lower()
        # Унифицируем разделители времени: «до», «по», «—», «–», «-» → ничего
        t = re.sub(r"\s+(?:до|по)\s+", "", t)
        t = re.sub(r"\s*[-–—]\s*", "", t)
        t = re.sub(r"\s+", "", t)
        return t.replace(",", ".")

    def _is_in_sources(self, value: str, sources: str) -> bool:
        """Проверить, есть ли значение (число, телефон) в источниках."""
        if not value or not sources:
            return False
        v = self._normalize_for_match(value)
        s = self._normalize_for_match(sources)
        return v in s

    def _is_safe(self, kind: str, value: str) -> bool:
        v_norm = self._normalize_for_match(value)
        for safe_kind, items in SAFE_FACTS.items():
            for item in items:
                if self._normalize_for_match(item) == v_norm:
                    return True
                if v_norm in self._normalize_for_match(item):
                    return True
        return False

    def _extract(self, text: str, regex: re.Pattern, kind: str) -> list[VerifyIssue]:
        out: list[VerifyIssue] = []
        for m in regex.finditer(text):
            value = m.group(0).strip()
            start, end = m.span()
            ctx = text[max(0, start - 20):min(len(text), end + 20)].replace("\n", " ")
            out.append(VerifyIssue(kind=kind, value=value, context_snippet=ctx))
        return out

    def verify(
        self,
        response_text: str,
        kb_text: str = "",
        web_text: str = "",
    ) -> VerifyResult:
        """Проверить ответ. Источниками считаем kb_text + web_text."""
        if not response_text:
            return VerifyResult(is_grounded=True, cleaned_text=response_text)

        sources = (kb_text or "") + "\n" + (web_text or "")

        # Извлекаем конкретику
        candidates: list[VerifyIssue] = []
        candidates.extend(self._extract(response_text, PHONE_RE, "phone"))
        candidates.extend(self._extract(response_text, PERCENT_RE, "percent"))
        candidates.extend(self._extract(response_text, PRICE_RE, "price"))
        candidates.extend(self._extract(response_text, TIME_RE, "time"))

        # Фильтруем: безопасные (canonical) сразу пропускаем, остальные проверяем в источниках
        unsupported: list[VerifyIssue] = []
        for issue in candidates:
            if self._is_safe(issue.kind, issue.value):
                continue
            if self._is_in_sources(issue.value, sources):
                continue
            unsupported.append(issue)

        cleaned = response_text
        if unsupported and self.auto_fix:
            for issue in unsupported:
                if issue.kind == "phone":
                    repl = "+375 44 788 88 80"
                elif issue.kind == "time":
                    repl = "уточните на evroopt.by/shops"
                elif issue.kind == "percent":
                    # Процент без подтверждения — оставляем, но логируем
                    continue
                else:
                    continue
                cleaned = cleaned.replace(issue.value, repl)

        if unsupported:
            logger.warning(
                "grounding_unsupported_facts",
                count=len(unsupported),
                kinds=sorted({i.kind for i in unsupported}),
                values=[i.value for i in unsupported[:5]],
            )

        return VerifyResult(
            is_grounded=len(unsupported) == 0,
            issues=unsupported,
            cleaned_text=cleaned,
        )
