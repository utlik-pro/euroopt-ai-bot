"""LIVE-прогон всех сценариев тестировщиков заказчика через ПОЛНЫЙ Pipeline.

Имитирует реальный запрос пользователя в Telegram — со всеми слоями:
PII -> content -> canonical -> intent -> RAG -> web -> LLM -> grounding -> tagger.

ВНИМАНИЕ: тратит LLM-токены (по одному запросу на сценарий, ~28 шт).
Пропускает PII-сценарии при запуске без --include-pii (чтобы не палить
PII-следы в логах живой системы — там и так Test environment).

Запуск:
    # Полный прогон (с LLM, тратит токены):
    python3.11 scripts/run_tester_scenarios.py

    # Только проверка маршрутизации (без LLM, быстро, бесплатно):
    python3.11 scripts/run_tester_scenarios.py --dry-run

    # С PII-сценариями (по умолчанию пропущены):
    python3.11 scripts/run_tester_scenarios.py --include-pii

Результат:
    docs/tester_scenarios_run_<дата>.md — полный отчёт с ответами бота.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

# Включаем все v2-флаги ДО импорта Pipeline
os.environ.setdefault("ENABLE_CANONICAL_ANSWERS", "true")
os.environ.setdefault("ENABLE_INTENT_ROUTER", "true")
os.environ.setdefault("ENABLE_QUERY_NORMALIZER", "true")
os.environ.setdefault("ENABLE_GROUNDING_VERIFY", "true")
os.environ.setdefault("GROUNDING_AUTO_FIX", "false")  # только лог при первом прогоне
os.environ.setdefault("ENABLE_BRAND_FILTER", "true")
os.environ.setdefault("ENABLE_RERANKER", "true")
os.environ.setdefault("RERANKER_MODE", "lite")
os.environ.setdefault("ENABLE_MECHANIC_CONTEXT", "true")
os.environ.setdefault("ENABLE_RESPONSE_CACHE", "true")
os.environ.setdefault("ENABLE_SOURCE_TAGGER", "true")

from src.canonical import CanonicalMatcher
from src.router import IntentRouter, detect_brand, detect_city, detect_format
from src.promotions.mechanic_detector import MechanicDetector
from src.filters.pii_filter import mask_pii
from src.filters.content_filter import check_content


def _check_response_content(response: str, sc: dict) -> tuple[bool, str]:
    """Эвристические проверки ОТВЕТА БОТА на ожидаемое содержание.

    Например, для eplus_pay_99_percent ответ обязан содержать «99%» и
    «2 копейки» — это требование заказчика из 24.04 P2.
    """
    if not response:
        return False, "пустой ответ"
    low = response.lower()

    canonical_id = sc.get("expected_canonical")
    if canonical_id == "eplus_pay_99_percent":
        if "99%" not in response or "2 копейки" not in low:
            return False, "не упомянул '99%' и/или '2 копейки'"
    if canonical_id == "eplus_card_lost":
        if "виртуальн" not in low or "форму обратной связи" not in low:
            return False, "не упомянул виртуальную карту и/или форму обратной связи"
    if canonical_id == "eplus_login":
        if "забыли пароль" not in low and "sms" not in low:
            return False, "не упомянул процедуру восстановления пароля"
    if canonical_id == "eplus_transfer_bonuses":
        if "форму обратной связи" not in low:
            return False, "не упомянул форму обратной связи"

    # Проверка на признак галлюцинации часов
    if sc.get("id") == "21_store_hours":
        # Должен либо отослать к evroopt.by/shops, либо назвать типовой 8:00-23:00
        if "shops" not in low and "8:00" not in response and "8.00" not in response:
            return False, "не направил на evroopt.by/shops и не назвал типовой режим"

    if sc.get("id") == "21_promo_easter":
        # Не должен предсказывать будущие акции — либо отказ, либо редирект
        forbidden_signals = ["к пасхе будут", "запланировано на", "пасхальные акции"]
        if any(sig in low for sig in forbidden_signals):
            return False, "предсказал будущие акции (галлюцинация)"

    if sc.get("id") == "21_delivery_evroopt":
        # Не должен говорить «у Евроопта есть доставка» в чистом виде
        # Должен описать конкретные сервисы (Ямигом / е-доставка)
        if "ямигом" not in low and "e-dostavka" not in low and "е-доставк" not in low:
            return False, "не упомянул конкретные сервисы доставки"

    if sc.get("id") == "24_eplus_99_percent":
        # Тот же canonical
        if "99%" not in response and "99 %" not in response:
            return False, "не упомянул 99%"
        if "2 копейки" not in low:
            return False, "не упомянул минимум 2 копейки"

    # PII-сценарии: ответ обязан начинаться с PII-рамки
    if sc.get("expected_pii_types"):
        pii_frame_signals = [
            "не могу обрабатыв",
            "не могу принимать",
            "не могу повторять",
            "не сохраняю",
            "персональные данные",
        ]
        if not any(sig in low for sig in pii_frame_signals):
            return False, "нет PII-рамки в начале ответа"

    return True, "ok"


async def run_live(scenarios: list[dict], skip_pii: bool = True) -> list[dict]:
    """Прогнать сценарии через настоящий Pipeline."""
    from src.pipeline import Pipeline

    pipeline = Pipeline()
    results = []

    for i, sc in enumerate(scenarios, 1):
        if skip_pii and sc.get("expected_pii_types"):
            results.append({**sc, "skipped": "pii_excluded"})
            print(f"[{i:2d}/{len(scenarios)}] ⏭️  {sc['id']:35s} (PII-сценарий пропущен)")
            continue

        t0 = time.monotonic()
        try:
            response = await pipeline.process(sc["query"], user_id=900000 + i)
            elapsed = time.monotonic() - t0
            content_ok, content_msg = _check_response_content(response, sc)
            results.append({
                **sc,
                "response": response,
                "elapsed_s": round(elapsed, 2),
                "content_ok": content_ok,
                "content_msg": content_msg,
            })
            mark = "✅" if content_ok else "⚠"
            print(f"[{i:2d}/{len(scenarios)}] {mark} {sc['id']:35s} ({elapsed:.1f}s) — {content_msg}")
        except Exception as e:
            elapsed = time.monotonic() - t0
            results.append({**sc, "error": str(e), "elapsed_s": round(elapsed, 2)})
            print(f"[{i:2d}/{len(scenarios)}] ❌ {sc['id']:35s} ОШИБКА: {e}")

    return results


def run_dry(scenarios: list[dict]) -> list[dict]:
    """Холодный прогон без LLM — только маршрутизация."""
    canonical = CanonicalMatcher()
    intent_router = IntentRouter()
    mechanic_det = MechanicDetector()

    results = []
    for sc in scenarios:
        query = sc["query"]
        out = {**sc, "dry_run": True, "checks": {}}
        ok_overall = True

        is_allowed, _ = check_content(query)
        out["checks"]["content_filter"] = "passed" if is_allowed else "blocked"
        if not is_allowed:
            ok_overall = False

        if sc.get("expected_pii_types"):
            _, types = mask_pii(query)
            missing = [t for t in sc["expected_pii_types"] if t not in types]
            out["checks"]["pii_detect"] = {
                "got": types, "missing": missing, "ok": not missing
            }
            if missing:
                ok_overall = False

        c_hit = canonical.match(query)
        c_id = c_hit.id if c_hit else None
        if sc.get("expected_canonical"):
            ok = c_id == sc["expected_canonical"]
            out["checks"]["canonical"] = {"got": c_id, "ok": ok}
            if not ok:
                ok_overall = False

        intent_res = intent_router.classify(query)
        if sc.get("expected_intent"):
            ok = intent_res.intent.value == sc["expected_intent"]
            out["checks"]["intent"] = {"got": intent_res.intent.value, "ok": ok}
            if not ok:
                ok_overall = False

        if sc.get("expected_brand"):
            brand = detect_brand(query)
            ok = brand == sc["expected_brand"]
            out["checks"]["brand"] = {"got": brand, "ok": ok}
            if not ok:
                ok_overall = False

        if sc.get("expected_city"):
            city = detect_city(query)
            ok = city == sc["expected_city"]
            out["checks"]["city"] = {"got": city, "ok": ok}
            if not ok:
                ok_overall = False

        if sc.get("expected_format"):
            fmt = detect_format(query)
            ok = fmt == sc["expected_format"]
            out["checks"]["format"] = {"got": fmt, "ok": ok}
            if not ok:
                ok_overall = False

        if sc.get("expected_mechanic"):
            mech = mechanic_det.detect(query)
            mech_id = mech.id if mech else None
            ok = mech_id == sc["expected_mechanic"]
            out["checks"]["mechanic"] = {"got": mech_id, "ok": ok}
            if not ok:
                ok_overall = False

        out["dry_ok"] = ok_overall
        mark = "✅" if ok_overall else "❌"
        print(f"{mark} {sc['id']:35s} {sc.get('block', '?'):15s}")
        results.append(out)

    return results


def render_md(results: list[dict], mode: str) -> str:
    today = date.today().strftime("%d.%m.%Y")
    lines = [
        f"# Прогон сценариев тестировщиков ({mode})",
        "",
        f"**Дата:** {today}",
        f"**Режим:** {'LIVE через Pipeline (с LLM)' if mode == 'live' else 'Dry-run (только маршрутизация)'}",
        "",
        "## Итог",
        "",
    ]

    total = len(results)
    skipped = sum(1 for r in results if r.get("skipped"))
    if mode == "live":
        ok = sum(1 for r in results if r.get("content_ok"))
        errors = sum(1 for r in results if r.get("error"))
        warned = total - ok - skipped - errors
        lines += [
            f"- Всего сценариев: **{total}**",
            f"- Полное соответствие ожиданиям: **{ok}**",
            f"- Расхождения по содержанию ответа: **{warned}**",
            f"- Ошибки выполнения: **{errors}**",
            f"- Пропущено PII (`--include-pii` для прогона): **{skipped}**",
            "",
        ]
    else:
        ok = sum(1 for r in results if r.get("dry_ok"))
        lines += [
            f"- Всего сценариев: **{total}**",
            f"- Маршрутизация корректна: **{ok}** / **{total}**",
            "",
        ]

    lines.append("## Детально\n")
    for r in results:
        sid = r["id"]
        lines.append(f"### `{sid}` ({r.get('block', '?')})")
        lines.append("")
        lines.append(f"**Запрос:** `{r['query']}`")
        lines.append("")
        lines.append(f"**Откуда:** отчёт {r['report']}, {r.get('priority', '—')}")
        if r.get("old_problem"):
            lines.append(f"**Что было:** {r['old_problem']}")
        if r.get("v2_layer"):
            lines.append(f"**Слой v2:** {r['v2_layer']}")
        lines.append("")

        if r.get("skipped"):
            lines.append(f"⏭️ **Пропущен:** {r['skipped']}")
        elif r.get("error"):
            lines.append(f"❌ **Ошибка:** `{r['error']}`")
        elif mode == "live":
            mark = "✅" if r.get("content_ok") else "⚠"
            lines.append(f"{mark} **Проверка содержимого:** {r.get('content_msg')}")
            lines.append(f"⏱ Время ответа: {r.get('elapsed_s')}s")
            lines.append("")
            lines.append("**Ответ бота:**")
            lines.append("```")
            response = r.get("response", "")
            lines.append(response[:1500] + ("…" if len(response) > 1500 else ""))
            lines.append("```")
        else:
            for ck, cval in (r.get("checks") or {}).items():
                if isinstance(cval, dict):
                    mark = "✅" if cval.get("ok") else "❌"
                    lines.append(f"- {mark} `{ck}`: {cval}")
                else:
                    lines.append(f"- `{ck}`: {cval}")
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="без LLM, только маршрутизация")
    ap.add_argument("--include-pii", action="store_true", help="включить PII-сценарии")
    ap.add_argument("--scenarios", default="tests/scenarios/tester_scenarios.json")
    ap.add_argument("--out", default="docs/tester_scenarios_run_26042026.md")
    args = ap.parse_args()

    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))["scenarios"]

    if args.dry_run:
        print(f"=== DRY-RUN: {len(scenarios)} сценариев ===\n")
        results = run_dry(scenarios)
        mode = "dry"
    else:
        skip_pii = not args.include_pii
        n_run = sum(1 for s in scenarios if not (skip_pii and s.get("expected_pii_types")))
        print(f"=== LIVE: {n_run} сценариев через настоящий Pipeline ===\n")
        print(f"⚠ Тратит LLM-токены, ожидаемое время ~{n_run*20//60}+ минут\n")
        results = asyncio.run(run_live(scenarios, skip_pii=skip_pii))
        mode = "live"

    md = render_md(results, mode)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"\n→ Отчёт: {out_path}")

    # Exit code: 0 если всё ок, 1 если есть warns/errors
    if mode == "live":
        bad = sum(
            1 for r in results
            if not r.get("skipped") and (not r.get("content_ok") or r.get("error"))
        )
    else:
        bad = sum(1 for r in results if not r.get("dry_ok"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
