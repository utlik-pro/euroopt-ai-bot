"""Простой sliding-window rate limiter (in-memory)."""

import time
from collections import defaultdict, deque
from threading import Lock

_LOCK = Lock()
_hits: dict[int, deque] = defaultdict(deque)


def check(user_id: int, limit: int, window_sec: int) -> tuple[bool, int]:
    """Возвращает (allowed, remaining)."""
    now = time.time()
    cutoff = now - window_sec
    with _LOCK:
        q = _hits[user_id]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False, 0
        q.append(now)
        return True, limit - len(q)
