"""Простой sliding-window rate limiter (in-memory)."""

import time
from collections import defaultdict, deque
from threading import Lock

_LOCK = Lock()
_hits: dict[int, deque] = defaultdict(deque)


def check(user_id: int, limit: int, window_sec: int) -> tuple[bool, int, int]:
    """Возвращает (allowed, remaining, reset_in_sec).

    reset_in_sec — сколько секунд до следующего освобождающегося слота
    (т.е. до выхода самой старой записи из скользящего окна).
    Если allowed=True, reset_in_sec = 0.
    """
    now = time.time()
    cutoff = now - window_sec
    with _LOCK:
        q = _hits[user_id]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            # Самая старая запись в окне освободится через (q[0] + window_sec - now)
            reset_in = int(q[0] + window_sec - now) + 1
            return False, 0, reset_in
        q.append(now)
        return True, limit - len(q), 0
