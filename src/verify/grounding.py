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
    # Типовой режим гипермаркетов «Евроопт» из SYSTEM_PROMPT — каноническая константа.
    # Без этого verifier режет ответ даже когда LLM выдаёт правильный диапазон.
    "hours": frozenset([
        "8:00-23:00", "8:00–23:00", "08:00-23:00", "08:00–23:00",
        "8:00 до 23:00", "08:00 до 23:00",
        "с 8:00 до 23:00", "с 08:00 до 23:00",
    ]),
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

    kind: str  # phone | percent | price | time | address | address_range
    value: str
    context_snippet: str  # 30 символов вокруг для логов


def _remove_sentence_with(text: str, value: str) -> str:
    """Удалить из ответа предложение(я), где встречается `value`.

    Работает по разделителям `.!?` (русские предложения). Полезно для
    time auto-fix: вместо вставки «уточните на evroopt.by/shops» в середину
    предложения, выкидываем всё это предложение целиком и оставляем редирект
    в конце.
    """
    if not value or value not in text:
        return text
    # Бьём по концам предложений, оставляя разделители
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [p for p in parts if value not in p]
    cleaned = " ".join(kept).rstrip()
    # Если что-то удалили и в результате нет упоминания shops/evroopt — добавим редирект
    if len(kept) < len(parts):
        if "evroopt.by/shops" not in cleaned and "evroopt.by" not in cleaned:
            cleaned += "\n\nРежим работы конкретного магазина уточните на [evroopt.by/shops](https://evroopt.by/shops/)."
    return cleaned


def _remove_address_lines(text: str, issues: list["VerifyIssue"]) -> str:
    """Удалить из ответа целые строки/пункты списка с галлюцинированными адресами.

    Стратегия: для каждого issue ищем строку (`\\n`-line) содержащую значение,
    и убираем её целиком. Если в результате остаётся «обвисший» список из <2 пунктов
    или ни одного пункта — заменяем хвост на безопасный редирект.

    Также убираем «пункты списка» вида «1. Минск, пр-т ...» где в самой строке
    есть значение issue. Это типичный формат ответа бота на «адреса магазинов».
    """
    if not issues:
        return text
    lines = text.splitlines()
    bad_values = {i.value for i in issues}

    # Проходим, выбрасывая строки содержащие любое из плохих значений
    kept: list[str] = []
    removed_count = 0
    for ln in lines:
        if any(bv in ln for bv in bad_values):
            removed_count += 1
            continue
        kept.append(ln)

    cleaned = "\n".join(kept)

    # Если после удаления список почти пустой — добавляем редирект
    # Признак списка: в исходнике были строки с маркерами «1.», «2.», «-», «•»
    had_list = any(
        re.match(r"^\s*(\d+\.|[-•])\s+", l) for l in lines
    )
    if had_list and removed_count > 0:
        # Считаем, сколько пунктов осталось
        remaining_items = sum(1 for l in kept if re.match(r"^\s*(\d+\.|[-•])\s+", l))
        if remaining_items < 2:
            # Не оставляем неполный список — заменяем хвост на редирект
            cleaned = cleaned.rstrip()
            redirect = (
                "\n\nПолный список магазинов с актуальными адресами и режимом работы — "
                "на [evroopt.by/shops](https://evroopt.by/shops/) с фильтром по городу/улице."
            )
            if redirect.strip() not in cleaned:
                cleaned += redirect

    return cleaned


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
        if v in s:
            return True
        # 02.05 — fallback для курсов валют и цен с разной разрядностью.
        # «2.823 BYN» в ответе может быть в источнике как «2,823 руб», «2.83 BYN»,
        # «1 USD = 2.823» и т.д. Извлекаем «голое» число и ищем его + признак
        # валюты/цены в источнике.
        num_match = re.search(r"\b(\d+[.,]\d{1,4})\b", value)
        if num_match:
            num = num_match.group(1).replace(",", ".")
            # Допуск ±0.01 для курсов валют — Tavily может возвращать чуть устаревший
            # курс, банк ЦБ другой и т.д. Если в источнике число близкое к нашему — ок.
            try:
                target = float(num)
                # Все числа из source с разделителем
                for src_num_match in re.finditer(r"\b(\d+[.,]\d{1,4})\b", sources):
                    try:
                        src = float(src_num_match.group(1).replace(",", "."))
                        if abs(src - target) <= 0.05:
                            return True
                    except ValueError:
                        continue
            except ValueError:
                pass
        return False

    @staticmethod
    def _is_address_in_sources(addr: str, sources: str) -> bool:
        """Fuzzy-проверка адреса: расщепляем на «улица + дом» и ищем оба в RAG.

        Зачем: точная нормализация ломается на разной форматировке инициалов
        («ул. Шаранговича В.Ф., 48» vs «ул. Шаранговича, 48»). Здесь смотрим
        упоминание имени улицы и номера дома **в радиусе 120 символов** друг
        от друга в источнике.
        """
        if not addr or not sources:
            return False
        # 1) номер дома (последовательность цифр + опц. буква)
        house_m = re.search(r"\b(\d{1,4}[а-яёА-ЯЁa-z]?)\b(?!\s*[-–])", addr)
        if not house_m:
            return False
        house = house_m.group(1)
        # 2) ключевое слово улицы (первое слово ≥4 букв после маркера улицы)
        street_m = re.search(
            r"(?:ул\.?|улиц[аы]|пр-?т[еауы]?\.?|проспект[ауе]?|пер\.|"
            r"переул(?:ок|ка)|бул\.?|бульвар|б-р[еауы]?\.?|ш\.|шоссе|"
            r"наб\.?|пл\.?|площад[ьи]|мкр(?:-н)?|микрорайон|тракт)"
            r"[\s.]+([А-ЯЁа-яё]{4,})",
            addr, re.IGNORECASE,
        )
        if not street_m:
            return False
        street = street_m.group(1).lower()
        # Корень слова — обрежем последние 2 символа (падежи: «Шаранговича» / «Шаранговичу»)
        street_root = street[:-2] if len(street) > 6 else street
        # Ищем все вхождения этого корня в источнике, проверяем что в радиусе 120
        # от него стоит наш номер дома.
        s_lower = sources.lower()
        for sm in re.finditer(re.escape(street_root), s_lower):
            window_start = max(0, sm.start() - 30)
            window_end = min(len(s_lower), sm.end() + 120)
            window = s_lower[window_start:window_end]
            # ищем «48» как отдельное число в окне
            if re.search(rf"\b{re.escape(house.lower())}\b", window):
                return True
        return False

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
            # Адрес — пробуем fuzzy match по корню улицы + номеру дома,
            # но только если в самом адресе НЕТ детальных маркеров «корп.»/«пом.»/«кв.»/«офис»
            # (для них требуем строгое вхождение — иначе пропустим галлюцинации
            # «корп. 3, пом. 7Н» к реальной улице).
            if issue.kind == "address" and not ADDRESS_DETAIL_MARKERS.search(issue.value):
                if self._is_address_in_sources(issue.value, sources):
                    continue
            unsupported.append(issue)

        cleaned = response_text
        if unsupported and self.auto_fix:
            # Адреса убираем целым "пунктом списка" (со строкой, маркером и переносом),
            # т.к. диапазоны/выдуманные «корп.7Н» не имеют корректной замены.
            address_issues = [i for i in unsupported if i.kind in ("address", "address_range")]
            if address_issues:
                cleaned = _remove_address_lines(cleaned, address_issues)

            for issue in unsupported:
                if issue.kind in ("address", "address_range"):
                    continue  # уже обработано выше через _remove_address_lines
                if issue.kind == "phone":
                    cleaned = cleaned.replace(issue.value, "+375 44 788 88 80")
                elif issue.kind == "time":
                    # Не подставляем placeholder в середину предложения
                    # (получалось «работы — с уточните на evroopt.by/shops»),
                    # а удаляем всё предложение с этим временем целиком.
                    cleaned = _remove_sentence_with(cleaned, issue.value)
                elif issue.kind == "percent":
                    # Процент без подтверждения — оставляем, но логируем
                    continue

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
