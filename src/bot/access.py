"""Access control: approval flow, private-only, rate limit, полное логирование."""

import json
from datetime import datetime, date
from pathlib import Path

from aiogram import types
from aiogram.enums import ChatType

from src.config import settings
from src.bot import whitelist_store, rate_limit

import os
# Логи пишем на persistent disk (чтобы переживали рестарты контейнера)
_PERSIST = Path(os.environ.get("PERSIST_DIR", "/app/persist"))
LOGS_DIR = _PERSIST / "logs" if _PERSIST.exists() else Path("logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_TESTER_LIST_PATH = Path("data/tester_list.json")


def _log_path(prefix: str) -> Path:
    return LOGS_DIR / f"{prefix}_{date.today().isoformat()}.jsonl"


def _write_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_access(user: types.User, status: str, reason: str = "", chat_type: str = "") -> None:
    _write_jsonl(
        _log_path("access"),
        {
            "ts": datetime.now().isoformat(),
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "status": status,  # approved|pending|denied|revoked|rate_limited|non_private|first_request
            "reason": reason,
            "chat_type": chat_type,
        },
    )


def log_message(user: types.User, direction: str, text: str, latency_ms: int | None = None, extra: dict | None = None) -> None:
    record = {
        "ts": datetime.now().isoformat(),
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "direction": direction,
        "text": text,
    }
    if latency_ms is not None:
        record["latency_ms"] = latency_ms
    if extra:
        record.update(extra)
    _write_jsonl(_log_path("messages"), record)


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_user_ids_set()


def is_private(message: types.Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    return rate_limit.check(user_id, settings.rate_limit_per_hour, 3600)


def match_tester(username: str | None, first_name: str, last_name: str | None) -> dict | None:
    """Найти запись в Excel-списке тестеров по @username или ФИО. Подсказка для админа."""
    if not _TESTER_LIST_PATH.exists():
        return None
    try:
        data = json.loads(_TESTER_LIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    uname = (username or "").lower().lstrip("@")
    full_name = f"{first_name or ''} {last_name or ''}".strip().lower()
    for t in data.get("list", []):
        if uname and t.get("username") and t["username"].lower() == uname:
            return t
    for t in data.get("list", []):
        fio = (t.get("fio") or "").lower()
        if full_name and fio and (full_name in fio or fio in full_name):
            return t
    return None


async def notify_admins(bot, user: types.User, first_message: str) -> None:
    """Отправить админам уведомление о новой заявке."""
    hint = match_tester(user.username, user.first_name, user.last_name)
    hint_line = ""
    if hint:
        hint_line = f"\n✅ Совпадение со списком тестеров: #{hint['n']} {hint['fio']} ({hint['role']}, {hint['company']})"
    else:
        hint_line = "\n⚠️ НЕТ совпадения с Excel-списком тестеров — проверь вручную!"

    text = (
        f"🔔 <b>Новая заявка на доступ</b>\n\n"
        f"👤 <b>user_id:</b> <code>{user.id}</code>\n"
        f"📛 <b>username:</b> @{user.username or '—'}\n"
        f"📝 <b>Имя:</b> {user.first_name or ''} {user.last_name or ''}"
        f"{hint_line}\n\n"
        f"💬 <b>Первое сообщение:</b>\n<code>{(first_message or '')[:200]}</code>\n\n"
        f"Подтвердить: <code>/approve {user.id}</code>\n"
        f"Отклонить: <code>/deny {user.id}</code>"
    )
    for admin_id in settings.admin_user_ids_set():
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
