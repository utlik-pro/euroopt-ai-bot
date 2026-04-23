"""Прямой вызов API НБРБ для курсов валют.

https://www.nbrb.by/apihelp/exrates — открытый REST API НБРБ,
возвращает официальный курс к белорусскому рублю (BYN).

Используем как детерминированный источник, когда пользователь
спрашивает «какой курс доллара/евро». Tavily иногда не находит
свежую цифру или LLM её игнорирует — прямой API надёжнее.
"""
from __future__ import annotations

import urllib.request
import json
import time
from datetime import datetime

import structlog

logger = structlog.get_logger()

# ID валют в системе НБРБ
_CURRENCY_IDS = {
    "USD": 431,
    "EUR": 451,
    "RUB": 456,
    "UAH": 449,
    "GBP": 429,
    "CNY": 480,
    "PLN": 452,
}

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 час — НБРБ обновляет раз в день


def _fetch_rate(cur_id: int) -> dict | None:
    """Сырой запрос к НБРБ API. Возвращает {rate, date, name, scale} или None."""
    url = f"https://api.nbrb.by/exrates/rates/{cur_id}?parammode=0"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        return {
            "rate": data["Cur_OfficialRate"],
            "date": data["Date"][:10],  # ГГГГ-ММ-ДД
            "name": data["Cur_Name"],
            "scale": data["Cur_Scale"],
            "abbr": data["Cur_Abbreviation"],
        }
    except Exception as e:
        logger.warning("nbrb_fetch_error", cur_id=cur_id, error=str(e))
        return None


def get_rate(currency: str) -> dict | None:
    """Получить курс валюты к BYN. currency = 'USD' / 'EUR' / 'RUB' и т.п.

    Возвращает {rate, date, name, scale, abbr} или None при ошибке.
    Кэш — 1 час.
    """
    currency = currency.upper().strip()
    cur_id = _CURRENCY_IDS.get(currency)
    if cur_id is None:
        return None

    # Cache
    now = time.time()
    if currency in _CACHE:
        ts, data = _CACHE[currency]
        if now - ts < _CACHE_TTL:
            return data

    data = _fetch_rate(cur_id)
    if data:
        _CACHE[currency] = (now, data)
    return data


# Ключевые слова, по которым определяем какие валюты нужны
_CURRENCY_KEYWORDS = {
    "USD": ["доллар", "usd", "бакс"],
    "EUR": ["евро", "eur"],
    "RUB": ["рубль рос", "российский рубль", "рубля рос", "rub"],
    "UAH": ["гривн", "uah"],
    "GBP": ["фунт", "gbp"],
    "CNY": ["юан", "cny"],
    "PLN": ["злот", "pln"],
}


def detect_currencies(msg: str) -> list[str]:
    """Определить о каких валютах спрашивает пользователь."""
    if not msg:
        return []
    low = msg.lower()
    found = []
    for cur, kws in _CURRENCY_KEYWORDS.items():
        if any(kw in low for kw in kws):
            found.append(cur)
    # Если спрашивают курс без уточнения — дефолт USD+EUR
    if not found and "курс" in low:
        found = ["USD", "EUR"]
    return found


def format_rates_block(currencies: list[str]) -> str:
    """Собрать блок с курсами в формате web_source для LLM.

    Возвращает XML-фрагмент (без обёртки <web_context>) или пустую
    строку если ничего не удалось получить.
    """
    rows = []
    date_str = ""
    for cur in currencies:
        r = get_rate(cur)
        if not r:
            continue
        scale = r["scale"]
        rate = r["rate"]
        if scale == 1:
            rows.append(f"  1 {r['name']} = {rate} BYN")
        else:
            rows.append(f"  {scale} {r['name']} = {rate} BYN")
        date_str = r["date"]
    if not rows:
        return ""
    body = "Официальный курс НБРБ на " + date_str + ":\n" + "\n".join(rows)
    return (
        '<web_source url="https://www.nbrb.by/statistics/rates/ratesDaily" '
        'title="Официальный курс НБРБ">\n'
        + body
        + "\n</web_source>"
    )
