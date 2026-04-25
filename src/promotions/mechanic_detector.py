"""Detector & registry of promo mechanics (Еврошок / Цены вниз / 1+1 / ...).

Закрывает претензию заказчика 24.04 P1: «бот может смешивать спеццены,
бонусы, игровые коды и акции для держателей Еплюс».

Источник данных: data/promotions/mechanics.json — справочник всех известных
механик акций с алиасами, описанием и landing-ссылкой.

Использование:
    detector = MechanicDetector()
    mech = detector.detect("какой Еврошок сегодня?")
    if mech:
        # mech.name = "Еврошок", mech.network = "Евроопт"
        # mech.description, mech.landing_url

В Pipeline: при intent=PROMOTIONS детектор определяет конкретную механику
и в RAG/web-поиске концентрируется именно на ней (а не на смешанной выдаче).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()

DEFAULT_PATH = Path("data/promotions/mechanics.json")


@dataclass
class Mechanic:
    """Описание одной промо-механики."""

    id: str
    name: str
    aliases: list[str]
    network: str  # Евроопт / Хит / Грошык
    type: str  # permanent / weekly / loyalty / promo / service
    description: str
    landing_url: str
    valid_period: str

    def format_brief(self) -> str:
        """Краткое представление для добавления в RAG-контекст."""
        return (
            f"Акция «{self.name}» (сеть {self.network}, период: {self.valid_period}).\n"
            f"{self.description}\n"
            f"Подробнее: {self.landing_url}"
        )


class MechanicDetector:
    """Загружает реестр механик и определяет упомянутую в запросе."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.mechanics: list[Mechanic] = []
        self._patterns: list[tuple[Mechanic, re.Pattern]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            logger.warning("mechanics_file_not_found", path=str(self.path))
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("mechanics_load_error", err=str(e), path=str(self.path))
            return

        for raw in data:
            m = Mechanic(
                id=raw["id"],
                name=raw["name"],
                aliases=raw.get("aliases", []),
                network=raw.get("network", "Евроопт"),
                type=raw.get("type", "promo"),
                description=raw.get("description", ""),
                landing_url=raw.get("landing_url", ""),
                valid_period=raw.get("valid_period", ""),
            )
            self.mechanics.append(m)
            # Скомпилируем regex для всех алиасов: точное вхождение в lowercase
            # с границами слов. Для коротких алиасов («1+1») границы не работают
            # (нет word-char), поэтому используем lookbehind/lookahead на пробел/конец.
            for alias in m.aliases:
                pat = self._compile_pattern(alias)
                if pat:
                    self._patterns.append((m, pat))

        logger.info(
            "mechanics_loaded",
            count=len(self.mechanics),
            patterns=len(self._patterns),
        )

    @staticmethod
    def _compile_pattern(alias: str) -> re.Pattern | None:
        """Скомпилировать regex для точного матчинга алиаса в тексте."""
        if not alias:
            return None
        norm = alias.lower().strip()
        if not norm:
            return None
        # Если алиас содержит только word-чары — используем word-границы
        if re.fullmatch(r"[\w\s\-]+", norm, re.UNICODE):
            escaped = re.escape(norm)
            return re.compile(rf"\b{escaped}\b", re.IGNORECASE | re.UNICODE)
        # Иначе (например «1+1») — границы по non-word
        escaped = re.escape(norm)
        return re.compile(
            rf"(?:^|[^\w]){escaped}(?:$|[^\w])",
            re.IGNORECASE | re.UNICODE,
        )

    def detect(self, text: str) -> Mechanic | None:
        """Найти упомянутую механику. При нескольких — берём первую по позиции."""
        if not text or not self._patterns:
            return None
        low = text.lower()
        earliest_pos = len(low) + 1
        earliest: Mechanic | None = None
        for mech, pat in self._patterns:
            m = pat.search(low)
            if m and m.start() < earliest_pos:
                earliest_pos = m.start()
                earliest = mech
        if earliest:
            logger.info(
                "mechanic_detected",
                id=earliest.id,
                name=earliest.name,
                network=earliest.network,
            )
        return earliest

    def detect_all(self, text: str) -> list[Mechanic]:
        """Все упомянутые механики (по уникальному id)."""
        if not text or not self._patterns:
            return []
        low = text.lower()
        seen: set[str] = set()
        out: list[Mechanic] = []
        for mech, pat in self._patterns:
            if mech.id in seen:
                continue
            if pat.search(low):
                seen.add(mech.id)
                out.append(mech)
        return out

    def get_by_network(self, network: str) -> list[Mechanic]:
        net_low = network.lower()
        return [m for m in self.mechanics if m.network.lower() == net_low]

    def get_by_id(self, mechanic_id: str) -> Mechanic | None:
        for m in self.mechanics:
            if m.id == mechanic_id:
                return m
        return None
