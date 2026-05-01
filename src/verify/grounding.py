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

# Адрес магазина в ответе бота. Ловим именно подозрительные формы:
# 1. Адрес С детализацией «корп.», «пом.», «офис», «к.» — частая галлюцинация LLM,
#    который дописывает несуществующие помещения к реальным номерам домов.
# 2. Диапазоны типа «дом 74-98» — таких адресов не существует, это «обобщение».
# 3. Полный адрес с маркером улицы + номером дома — для общей проверки в источниках.
#
# Найденный фрагмент проверяется на дословное вхождение в kb_text + web_text.
# Если нет — флагируется как unsupported_facts kind=address.
ADDRESS_DETAILED_RE = re.compile(
    r"(?ix)"
    r"(?:ул\.?|улиц[аы]|пр-?т[еауы]?\.?|пр\.|проспект[ауе]?|пер\.|переул(?:ок|ка)|"
    r"бул\.?|бульвар[ауе]?|б-р[еауы]?\.?|ш\.|шоссе|наб\.?|пл\.?|площад[ьи]|"
    r"мкр(?:-н)?\.?|микрорайон\w*|тракт)"
    r"\s+[А-ЯЁа-яёA-Za-z][\w\-\.]{1,40}"
    r"(?:\s+[А-ЯЁа-яёA-Za-z\-\d\.]{1,40}){0,3}"
    r"(?:[,\s]+(?:д\.?|дом\s+)?\d+[а-яёА-ЯЁa-z\-/]*)"
    r"(?:[,\s]+(?:корп(?:ус|\.)?|к\.)\s*\d+[а-яёА-ЯЁa-z\-/]*)?"
    r"(?:[,\s]+(?:кв(?:артира|\.)?|пом(?:ещение|\.)?|офис)\s*[\d,\-А-ЯЁа-яёA-Za-z]+)?"
)

# Диапазон «дом 74-98» — однозначная галлюцинация (одного дома быть в диапазоне не может).
# Нумерация типа «3/3», «10А», «23/1» — НЕ диапазон, разделитель «/» или буква.
# Диапазон именно «N-M» где оба — целые числа без модификаторов.
ADDRESS_RANGE_RE = re.compile(
    r"(?:д\.?|дом\s+|,\s*)\b(\d{1,4}[\-–—]\d{1,4})\b(?!\s*[/\\а-яёА-ЯЁa-zA-Z])"
)

# Маркеры детализации адреса, которые LLM любит дописывать.
# Если они встретились в ответе — подозрительно, проверяем строже.
ADDRESS_DETAIL_MARKERS = re.compile(
    r"(?i)\b(?:корп(?:ус|\.)?|кв(?:артира|\.)?|пом(?:ещение|\.)?|офис)\s*[\d№#]"
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

    def _extract_addresses(self, text: str) -> list[VerifyIssue]:
        """Извлекаем адреса для проверки. Приоритет — подозрительные формы.

        Стратегия:
        1. Диапазоны «дом 74-98» → всегда unsupported (таких адресов нет).
        2. Адреса с маркерами «корп.», «пом.», «офис», «кв.» → проверяем строже.
        3. Обычные адреса (улица + дом) → проверяем дословное вхождение в kb.
        """
        out: list[VerifyIssue] = []

        # 1) Диапазоны — однозначная галлюцинация
        for m in ADDRESS_RANGE_RE.finditer(text):
            value = m.group(0).strip(" ,.")
            start, end = m.span()
            ctx = text[max(0, start - 30):min(len(text), end + 30)].replace("\n", " ")
            out.append(VerifyIssue(kind="address_range", value=value, context_snippet=ctx))

        # 2) Полные адреса (улица + дом + опционально корп/пом)
        for m in ADDRESS_DETAILED_RE.finditer(text):
            value = m.group(0).strip()
            start, end = m.span()
            ctx = text[max(0, start - 30):min(len(text), end + 30)].replace("\n", " ")
            out.append(VerifyIssue(kind="address", value=value, context_snippet=ctx))

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
        candidates.extend(self._extract_addresses(response_text))

        # Фильтруем: безопасные (canonical) сразу пропускаем, остальные проверяем в источниках
        unsupported: list[VerifyIssue] = []
        for issue in candidates:
            # address_range — всегда плохо (диапазоны домов не существуют)
            if issue.kind == "address_range":
                unsupported.append(issue)
                continue
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
