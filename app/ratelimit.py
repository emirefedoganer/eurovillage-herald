"""A minimal in-memory sliding-window rate limiter for a handful of
sensitive public endpoints (admin login, the contact form, the Turnstile
gate check). Deliberately not a general-purpose or distributed limiter --
just enough to blunt brute-force/spam bursts without adding a Redis (or
similar) dependency to a small, single/low-worker-count deployment.

Caveat: counters are per-process, so with multiple gunicorn workers the
effective limit is roughly (limit * worker count). That's an accepted
trade-off for "practical" rate-limiting per the project's scale, not a
hard security boundary.
"""
import time
from collections import defaultdict, deque
from threading import Lock

_hits = defaultdict(deque)
_lock = Lock()


def allow(key, max_hits, window_seconds):
    now = time.time()
    with _lock:
        dq = _hits[key]
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= max_hits:
            return False
        dq.append(now)
        return True
