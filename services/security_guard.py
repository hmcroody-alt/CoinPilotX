"""Request safety guardrails for high-traffic interactive surfaces."""

from __future__ import annotations

import html
import re
import time
from collections import defaultdict


BUCKETS = defaultdict(list)
BLOCKED_EXTENSIONS = {".exe", ".js", ".mjs", ".sh", ".bat", ".cmd", ".php", ".svg", ".html", ".htm"}
SCRIPT_RE = re.compile(r"<\s*script|javascript:|onerror\s*=|onload\s*=", re.I)


def sanitize_text(value, limit=2000):
    value = str(value or "")[:limit]
    return html.escape(value, quote=True)


def suspicious_text(value):
    return bool(SCRIPT_RE.search(str(value or "")))


def file_extension_allowed(filename):
    lowered = str(filename or "").lower()
    return not any(lowered.endswith(ext) for ext in BLOCKED_EXTENSIONS)


# name -> (last sweep timestamp, widest window this dict has ever been asked about)
_SWEEPS: dict[str, tuple[float, float]] = {}


def sweep_expired(name, buckets, window_seconds, *, now=None, min_interval=60.0):
    """Drop bucket keys that nothing can still be counting. Returns how many.

    The in-process limiters prune *stamps* whenever a key is touched, but never
    remove the key itself, so every distinct (subject, path) pair a worker has
    ever seen stays resident for the life of the process. Organic traffic makes
    that grow slowly; a distributed source makes it grow as fast as it can send
    unique subjects. Gunicorn workers are long-lived, so nothing reclaims it.

    This is deliberately not a policy change and does not need a switch. A key
    whose newest stamp is at least ``widest`` seconds old prunes to an empty
    list the next time it is read — the existing filter keeps
    ``now - stamp < window_seconds`` — so removing it here and letting
    ``BUCKETS.get(key, [])`` return that same empty list produces an identical
    decision. The only thing that changes is that the memory comes back.

    ``widest`` is the largest window this dict has ever been asked about, not
    the window of the current call, and it only ever grows. That ordering is
    what makes the sweep safe: a key can only have been created by a call that
    already recorded its own window, so by the time the key exists ``widest``
    is at least as large as the window governing it. Sweeping against the
    current call's window instead would drop a live 600-second key the first
    time a 60-second one came through.
    """
    now = time.time() if now is None else now
    last_sweep, widest = _SWEEPS.get(name, (0.0, 0.0))
    widest = max(widest, float(window_seconds))
    if now - last_sweep < min_interval:
        _SWEEPS[name] = (last_sweep, widest)
        return 0
    horizon = now - widest
    stale = [key for key, stamps in list(buckets.items())
             if not stamps or max(stamps) <= horizon]
    for key in stale:
        buckets.pop(key, None)
    _SWEEPS[name] = (now, widest)
    return len(stale)


def rate_limited(key, limit=60, window_seconds=60):
    now = time.time()
    bucket = [stamp for stamp in BUCKETS.get(key, []) if now - stamp < window_seconds]
    limited = len(bucket) >= int(limit)
    if not limited:
        bucket.append(now)
    BUCKETS[key] = bucket
    sweep_expired("security_guard.BUCKETS", BUCKETS, window_seconds, now=now)
    return limited


def request_limit_for(path, method):
    if method not in {"POST", "PUT", "PATCH"}:
        return None
    if path.startswith("/api/media/upload"):
        return 10, 300
    if path.startswith("/api/arena/roast"):
        return 45, 60
    if path.startswith("/api/chat/") or path.startswith("/api/players/"):
        return 80, 60
    if path.startswith("/api/sms/"):
        return 8, 600
    if path.startswith("/api/telegram/"):
        return 12, 300
    if path.startswith("/api/alerts") or path.startswith("/api/auto-signals"):
        return 60, 60
    return None
