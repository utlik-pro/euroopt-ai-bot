"""Smoke-тест прод-бота через Telegram Bot API сразу после Render-деплоя.

Не отправляет сообщения реальным пользователям. Проверяет:
1. Bot API: токен валиден, getMe возвращает имя бота
2. (опционально) Pings Render dashboard через API: статус деплоя

Запуск:
    TELEGRAM_BOT_TOKEN=... python3.11 scripts/render_smoke_test.py
    # или с явным сервисом:
    RENDER_SERVICE_ID=srv-d7fkju67r5hc739bktfg python3.11 scripts/render_smoke_test.py
"""
from __future__ import annotations

import os
import sys
import json
import urllib.request
import urllib.error


def telegram_get_me(token: str) -> dict | None:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        return data
    except Exception as e:
        print(f"❌ getMe failed: {e}", file=sys.stderr)
        return None


def render_deploy_status(service_id: str, api_key: str) -> dict | None:
    url = f"https://api.render.com/v1/services/{service_id}/deploys?limit=1"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list) and data:
            return data[0].get("deploy") or data[0]
        return None
    except Exception as e:
        print(f"❌ Render API failed: {e}", file=sys.stderr)
        return None


def main() -> int:
    print("=== Render smoke test ===")

    # 1. Render deploy status (если есть API-ключ)
    service_id = os.environ.get("RENDER_SERVICE_ID", "srv-d7fkju67r5hc739bktfg")
    api_key = os.environ.get("RENDER_API_KEY")
    if api_key:
        d = render_deploy_status(service_id, api_key)
        if d:
            status = d.get("status", "?")
            commit = (d.get("commit") or {}).get("id", "?")[:7]
            print(f"  [Render] last deploy: {status} commit={commit}")
            if status != "live":
                print(f"  ⚠ деплой не live — статус {status}")
                return 1
        else:
            print("  ⚠ Render API не вернул деплой")
    else:
        print("  ℹ RENDER_API_KEY не задан — пропускаю проверку статуса деплоя")
        print("  ℹ используй render CLI: render deploys list srv-d7fkju67r5hc739bktfg")

    # 2. Telegram Bot API
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("  ❌ TELEGRAM_BOT_TOKEN не задан — пропускаю Telegram-проверку")
        return 2
    me = telegram_get_me(token)
    if not me or not me.get("ok"):
        print(f"  ❌ Telegram getMe failed: {me}")
        return 3
    bot = me["result"]
    print(f"  ✅ [Telegram] бот: @{bot['username']} ({bot['first_name']})")
    print(f"     id={bot['id']} can_join_groups={bot.get('can_join_groups')}")

    print("\n=== Чеклист для ручной проверки в Telegram ===")
    print("Отправьте боту следующие сообщения и проверьте поведение:\n")

    checks = [
        ("как войти в личный кабинет", "<1с, упоминает SMS / 'забыли пароль' / eplus.by"),
        ("что делать если потерял карту Еплюс?", "<1с, упоминает виртуальную карту + форму обратной связи"),
        ("можно ли оплатить весь чек бонусами?", "содержит '99%' и '2 копейки'"),
        ("горячая линия Евроопт", "+375 44 788 88 80"),
        ("какая погода в Минске?", "погода через Tavily, упоминает Минск"),
    ]
    for i, (q, expected) in enumerate(checks, 1):
        print(f"  {i}. «{q}»")
        print(f"     → ожидается: {expected}\n")

    print("Всё хорошо — переходим к Фазе 2 (через 1–2 дня).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
