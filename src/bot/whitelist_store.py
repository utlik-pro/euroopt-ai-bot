"""Персистентный whitelist с approval flow.

data/whitelist.json:
{
  "approved": {"<user_id>": {"username": "...", "first_name": "...", "last_name": "...", "approved_at": "ISO", "approved_by": <admin_id>, "note": "..."}},
  "pending":  {"<user_id>": {"username": "...", "first_name": "...", "last_name": "...", "requested_at": "ISO", "first_message": "..."}},
  "denied":   {"<user_id>": {"username": "...", "denied_at": "ISO", "denied_by": <admin_id>}}
}
"""

import json
import threading
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path("data")
_DATA_DIR.mkdir(exist_ok=True)
_PATH = _DATA_DIR / "whitelist.json"
_LOCK = threading.Lock()


def _load() -> dict:
    if not _PATH.exists():
        return {"approved": {}, "pending": {}, "denied": {}}
    with open(_PATH, encoding="utf-8") as f:
        data = json.load(f)
    for k in ("approved", "pending", "denied"):
        data.setdefault(k, {})
    return data


def _save(data: dict) -> None:
    tmp = _PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(_PATH)


def is_approved(user_id: int) -> bool:
    with _LOCK:
        return str(user_id) in _load()["approved"]


def is_denied(user_id: int) -> bool:
    with _LOCK:
        return str(user_id) in _load()["denied"]


def is_pending(user_id: int) -> bool:
    with _LOCK:
        return str(user_id) in _load()["pending"]


def add_pending(user_id: int, username: str | None, first_name: str, last_name: str | None, first_message: str) -> bool:
    """Добавить в pending. Возвращает True если запись новая."""
    with _LOCK:
        data = _load()
        key = str(user_id)
        if key in data["approved"] or key in data["pending"]:
            return False
        data["pending"][key] = {
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "requested_at": datetime.now().isoformat(),
            "first_message": first_message[:200],
        }
        _save(data)
        return True


def approve(user_id: int, admin_id: int, note: str = "") -> dict | None:
    with _LOCK:
        data = _load()
        key = str(user_id)
        rec = data["pending"].pop(key, None) or data["denied"].pop(key, None) or {}
        rec.update(
            {
                "approved_at": datetime.now().isoformat(),
                "approved_by": admin_id,
                "note": note,
            }
        )
        data["approved"][key] = rec
        _save(data)
        return rec


def deny(user_id: int, admin_id: int) -> None:
    with _LOCK:
        data = _load()
        key = str(user_id)
        rec = data["pending"].pop(key, None) or data["approved"].pop(key, None) or {}
        rec.update({"denied_at": datetime.now().isoformat(), "denied_by": admin_id})
        data["denied"][key] = rec
        _save(data)


def revoke(user_id: int, admin_id: int) -> bool:
    with _LOCK:
        data = _load()
        key = str(user_id)
        if key not in data["approved"]:
            return False
        rec = data["approved"].pop(key)
        rec.update({"revoked_at": datetime.now().isoformat(), "revoked_by": admin_id})
        data["denied"][key] = rec
        _save(data)
        return True


def list_pending() -> dict:
    with _LOCK:
        return _load()["pending"]


def list_approved() -> dict:
    with _LOCK:
        return _load()["approved"]
