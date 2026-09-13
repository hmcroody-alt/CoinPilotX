"""Route-hit telemetry, keyed by client.

**Why this exists.** The web rebuild's inventory can say which of the 2,146
routes are *referenced* by code. It cannot say which are *reached* by traffic,
because URLs in this codebase are assembled at runtime — f-string route
constants, `url_for` with computed endpoints, links built inside 496 inline-HTML
pages. Static extraction reads a route as dead when the only caller builds its
path at request time.

So every deletion in the rebuild is gated on a week of this table, and the gate
is only meaningful if the hit is attributed to a *client*: a route that only the
native app reaches must survive the web rebuild untouched, and a route nothing
reaches for seven days is a deletion candidate. One number per route cannot tell
those apart, which is the whole reason the `client` column exists.

**What it deliberately does not record.**

- Not the path — the **url rule** (`/pulse/post/<int:post_id>`). No ids, no
  usernames, no query strings, therefore no personal data and no free-text
  column that could carry any. It is also what keeps the table bounded: the key
  space is (rules x methods x clients x days), not (requests).
- Not the user, not the IP, not the User-Agent. Client is a four-value
  classification and nothing finer.

**What it costs.** Nothing per request but a dict increment under a lock. Writes
are batched and flushed on an interval, so a route under load contributes one
upsert per window rather than one insert per hit. Default OFF
(`PULSE_ROUTE_HIT_LOG_ENABLED`), like every other observation bridge here.

**It must never be the reason a request fails.** Every public function swallows
its own exceptions and counts them into `stats()`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date

from services import db
from services.device_classification import classify_device

TABLE = "route_hit_daily"

CLIENT_NATIVE = "native"
CLIENT_WEB = "web"
CLIENT_BOT = "bot"
CLIENT_UNKNOWN = "unknown"

#: Requests that matched no route. Recorded under a single key rather than
#: dropped: a 404 flood against a family the rebuild is about to delete is
#: evidence, and it would otherwise be invisible.
UNMATCHED_RULE = "<unmatched>"

FLUSH_INTERVAL_SECONDS = 60.0
#: Upper bound on distinct keys held in memory between flushes. The key space is
#: naturally bounded, so hitting this means something is wrong (an unbounded
#: rule, a classification bug) and dropping is the correct response — this
#: buffer must never be the thing that exhausts a gunicorn worker.
MAX_BUFFER_KEYS = 20000

_LOCK = threading.Lock()
_BUFFER: dict[tuple[str, str, str, str], int] = {}
#: Seeded at import, not at zero. Zero means "one window has already elapsed",
#: which makes the *first* request each worker serves pay for a synchronous
#: flush — precisely the per-request write this buffer exists to avoid, and it
#: lands on the coldest request in the process.
_LAST_FLUSH = time.time()
_STATS = {"recorded": 0, "dropped": 0, "flushed": 0, "flush_failures": 0, "record_failures": 0}

_BOT_TOKENS = (
    "bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests",
    "headlesschrome", "monitoring", "uptime", "pingdom", "postman", "insomnia",
)


def enabled() -> bool:
    return os.getenv("PULSE_ROUTE_HIT_LOG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def classify_client(user_agent: str, headers=None) -> str:
    """Four values, and the order of the checks is the whole design.

    The native app is identified first and by its own explicit signals
    (`X-PulseSoc-Platform`, the `PulseSocNativeApp/` UA prefix) rather than by
    "is it mobile" — a phone browser is a *web* client and must not be counted
    as the app, or the deletion gate would protect routes the app never calls.

    Bots are separated out for the same reason in reverse: an uptime check that
    pings a route every 60 seconds would otherwise keep a dead route alive
    forever.
    """
    ua = str(user_agent or "")
    try:
        info = classify_device(ua, headers)
        if info.get("is_native_app"):
            return CLIENT_NATIVE
    except Exception:
        pass
    if not ua.strip():
        return CLIENT_UNKNOWN
    lowered = ua.lower()
    if any(token in lowered for token in _BOT_TOKENS):
        return CLIENT_BOT
    if "mozilla/" in lowered or "safari/" in lowered or "gecko" in lowered:
        return CLIENT_WEB
    return CLIENT_UNKNOWN


def record(rule: str, method: str, client: str, *, now: float | None = None) -> None:
    """Buffer one hit. Cheap enough to call on every request."""
    if not enabled():
        return
    try:
        key = (
            date.today().isoformat(),
            (rule or UNMATCHED_RULE)[:255],
            (method or "GET").upper()[:8],
            client or CLIENT_UNKNOWN,
        )
        with _LOCK:
            if key not in _BUFFER and len(_BUFFER) >= MAX_BUFFER_KEYS:
                _STATS["dropped"] += 1
                return
            _BUFFER[key] = _BUFFER.get(key, 0) + 1
            _STATS["recorded"] += 1
            due = (now or time.time()) - _LAST_FLUSH >= FLUSH_INTERVAL_SECONDS
        if due:
            flush(now=now)
    except Exception:
        _STATS["record_failures"] += 1


def flush(*, now: float | None = None) -> int:
    """Write the buffer out. Returns the number of keys written.

    The buffer is swapped out under the lock and written outside it, so a slow
    database cannot serialise the request threads that are still recording.
    On failure the counts are merged back rather than discarded — losing a
    window of evidence is how a route gets deleted on incomplete data.
    """
    global _LAST_FLUSH
    with _LOCK:
        if not _BUFFER:
            _LAST_FLUSH = now or time.time()
            return 0
        batch = dict(_BUFFER)
        _BUFFER.clear()
        _LAST_FLUSH = now or time.time()

    try:
        conn = db.connect()
        try:
            cur = conn.cursor()
            for (day, rule, method, client), hits in batch.items():
                cur.execute(
                    f"INSERT INTO {TABLE} (day, rule, method, client, hits, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (day, rule, method, client) DO UPDATE SET "
                    f"hits = {TABLE}.hits + EXCLUDED.hits, last_seen = CURRENT_TIMESTAMP",
                    (day, rule, method, client, hits),
                )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _STATS["flushed"] += len(batch)
        return len(batch)
    except Exception as exc:
        with _LOCK:
            for key, hits in batch.items():
                _BUFFER[key] = _BUFFER.get(key, 0) + hits
        _STATS["flush_failures"] += 1
        logging.warning("ROUTE_HIT_LOG_FLUSH_FAILED keys=%s error=%s", len(batch), exc)
        return 0


def stats() -> dict:
    with _LOCK:
        return dict(_STATS, buffered=len(_BUFFER), enabled=enabled())


def ensure_schema(conn) -> None:
    """Idempotent. There is no migration framework here; `init_db()` re-runs.

    Called with an existing connection and deliberately does **not** commit —
    see the `ensure_schema(conn)` trap: committing a caller's route connection,
    or failing to, is how DDL rolls back and a second connection then blocks on
    the uncommitted catalog lock. `init_db()` owns the transaction.
    """
    cur = conn.cursor()
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ("
        "  day TEXT NOT NULL,"
        "  rule TEXT NOT NULL,"
        "  method TEXT NOT NULL,"
        "  client TEXT NOT NULL,"
        "  hits BIGINT NOT NULL DEFAULT 0,"
        "  last_seen TIMESTAMP,"
        "  PRIMARY KEY (day, rule, method, client)"
        ")"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_rule ON {TABLE} (rule, client)"
    )
