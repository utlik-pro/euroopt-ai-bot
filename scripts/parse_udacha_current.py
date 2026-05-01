"""Парсер текущего тура «Удача в придачу» с главной igra.evroopt.by.

Каждый час обновляет:
- номер тура
- даты (с-по)
- дату розыгрыша
- описание призов
- ссылка на правила (PDF)

Сохраняет в data/udacha/current.json — оттуда reindex_v2 кладёт в RAG.

Запуск:
    python3.11 scripts/parse_udacha_current.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT_FP = ROOT / "data/udacha/current.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

URL = "https://igra.evroopt.by/"

# Регексы для извлечения информации
TUR_RE = re.compile(r"[Тт]ур\s*(\d{2,4})")
DATES_RE = re.compile(
    r"с\s+(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+(?:по\s+)?(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*",
    re.IGNORECASE,
)
DRAW_DATE_RE = re.compile(
    r"[Рр]озыгрыш\s+(?:суперпризов\s+)?[–—\-]?\s*(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*",
    re.IGNORECASE,
)

# Детали призов — выдёргиваем bullet-блоки про квартиры/авто/деньги/др.
PRIZE_KEYWORDS = re.compile(
    r"(квартир\w*|автомобил\w*|машин\w*|тысяч\w*\s+рубл|рубл\w*|подарочн\w*|сертификат\w*|"
    r"телевизор\w*|стиральн\w*|холодильник\w*|смартфон\w*|велосипед\w*|пылесос\w*|"
    r"путешеств\w*|поездк\w*|тур\s+в\s|велик|купон\w*)",
    re.IGNORECASE,
)


def fetch(url: str) -> str:
    r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
    r.raise_for_status()
    return r.text


def extract_tur_info(html: str) -> dict:
    """Извлечь номер тура и общие даты."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=False)
    text = re.sub(r"\s+", " ", text)

    out = {
        "tur_number": None,
        "period": None,
        "draw_date": None,
        "prizes_summary": None,
        "rules_url": None,
        "main_image_url": None,
        "source_url": URL,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }

    # Номер тура — берём САМЫЙ ВЫСОКИЙ номер на странице (актуальный)
    nums = [int(m.group(1)) for m in TUR_RE.finditer(text)]
    if nums:
        out["tur_number"] = max(nums)

    # Период «с N <месяц> по M <месяц>»
    m = DATES_RE.search(text)
    if m:
        out["period"] = (
            f"с {m.group(1)} {m.group(2)} по {m.group(3)} {m.group(4)}"
        )

    # Дата розыгрыша
    m = DRAW_DATE_RE.search(text)
    if m:
        out["draw_date"] = f"{m.group(1)} {m.group(2)}"

    # Призы тянем ИЗ alt-атрибутов слайдера (там точные названия):
    # alt="Квартира 1", "Автомобиль Geely Monjaro", "Призы 50, 40, 30 тыс" и т.п.
    prize_alts = []
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if not alt or len(alt) > 100:
            continue
        if PRIZE_KEYWORDS.search(alt):
            prize_alts.append(alt)
    # Дедупликация с сохранением порядка
    seen = set()
    prizes_unique = []
    for p in prize_alts:
        key = p.lower().strip()
        if key not in seen:
            seen.add(key)
            prizes_unique.append(p)
    if prizes_unique:
        out["prizes_list"] = prizes_unique[:8]
        out["prizes_summary"] = "Главные призы: " + "; ".join(prizes_unique[:5]) + "."

    # Fallback — ищем по тексту, если в alt'ах ничего не нашлось
    if not out.get("prizes_summary"):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for s in sentences[:200]:
            if PRIZE_KEYWORDS.search(s) and len(s) < 400:
                s_clean = re.sub(r"\s+", " ", s).strip()
                if 50 < len(s_clean) < 400:
                    out["prizes_summary"] = s_clean
                    break

    # Ссылка на правила (PDF)
    rules_link = soup.find("a", href=re.compile(r"\.pdf", re.I), string=re.compile(r"правил|regulation", re.I))
    if not rules_link:
        # Иначе ищем любую ссылку на pdf со словом «правил»/«rules» в href
        for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = a.get("href", "")
            if "rule" in href.lower() or "правил" in (a.get_text() or "").lower():
                rules_link = a
                break
    if rules_link:
        href = rules_link.get("href", "")
        if href.startswith("/"):
            href = "https://igra.evroopt.by" + href
        out["rules_url"] = href

    return out


def main():
    print(f"📥 Загружаю {URL}")
    try:
        html = fetch(URL)
    except Exception as e:
        print(f"❌ Не удалось загрузить страницу: {e}")
        # Если не получилось — сохраняем минимальный fallback,
        # чтобы reindex не падал и бот всё равно мог отвечать общим текстом
        info = {
            "tur_number": None,
            "period": None,
            "draw_date": None,
            "prizes_summary": None,
            "rules_url": None,
            "source_url": URL,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "error": str(e),
        }
    else:
        info = extract_tur_info(html)

    OUT_FP.parent.mkdir(parents=True, exist_ok=True)
    OUT_FP.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("Тур:", info.get("tur_number") or "—")
    print("Период:", info.get("period") or "—")
    print("Розыгрыш:", info.get("draw_date") or "—")
    print("Призы:", (info.get("prizes_summary") or "—")[:160])
    print("Правила:", info.get("rules_url") or "—")
    print()
    print(f"📄 Сохранено в {OUT_FP.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
