"""Shared test harness for the presence suite.

The point of this file is to run the *real* production modules -- not copies,
not reimplementations -- against an in-memory database. `pulse_communications_v2`
reaches for the application through a lazy `import bot` inside `_bot()`, and it
only ever touches two attributes of it: `bot.db()` and `bot.sqlite3`. That
narrow surface is what makes a stub honest here: we are not simulating the code
under test, only the database handle it asks for.

Everything else -- schema creation, privacy evaluation, expiry arithmetic --
executes exactly as it does in production.
"""

import os
import sqlite3
import sys
from datetime import timedelta

# Derived from this file's own location (tests/presence/harness.py -> repo root)
# so the suite is runnable from any checkout without configuration. The env var
# remains as an override for out-of-tree runs.
REPO = os.environ.get("PULSE_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

# The comm_v2 feature flag gates every public entry point. Tests exercise the
# enabled path, which is the one that ships.
os.environ.setdefault("PULSE_COMMS_V2_ENABLED", "1")
os.environ.setdefault("PULSE_COMMUNICATIONS_V2", "1")

_CONN = sqlite3.connect(":memory:", check_same_thread=False)
_CONN.row_factory = sqlite3.Row


class _SharedConn:
    """The shared in-memory connection, with close() disarmed.

    Service functions own their handle's lifecycle and close it in a `finally`.
    In production each call gets its own pooled connection, so that is correct.
    Here every call shares one `:memory:` database -- closing it would discard
    the whole schema mid-suite. Swallowing close() is what makes a single
    in-memory database stand in for the pool; nothing else about the connection
    is altered.
    """

    def __init__(self, inner):
        self._inner = inner

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        if name == "_inner":
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)


class _BotStub:
    """The two attributes pulse_communications_v2 asks of the app."""

    sqlite3 = sqlite3

    @staticmethod
    def db():
        return _SharedConn(_CONN)


sys.modules.setdefault("bot", _BotStub())

from services import presence_service as ps  # noqa: E402


def conn():
    return _CONN


def cursor():
    return _CONN.cursor()


def bootstrap_users(cur, count=6):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT)"
    )
    for uid in range(1, count + 1):
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, display_name, avatar_url) VALUES (?,?,?,?)",
            (uid, f"user{uid}", f"User {uid}", ""),
        )
    _CONN.commit()


def age_session(cur, user_id, seconds):
    """Push a user's session expiry into the past.

    Used instead of sleeping. Expiry is compared against wall-clock UTC at read
    time, so rewriting the stored timestamp is indistinguishable from waiting --
    and it keeps the suite instant.
    """
    cur.execute(
        "UPDATE presence_sessions SET expires_at=? WHERE user_id=?",
        (ps.iso(ps.utc_now() - timedelta(seconds=seconds)), int(user_id)),
    )
    _CONN.commit()


def age_last_beat(cur, user_id, seconds):
    """Backdate the last heartbeat without expiring the session (away testing)."""
    cur.execute(
        "UPDATE presence_sessions SET last_heartbeat_at=? WHERE user_id=?",
        (ps.iso(ps.utc_now() - timedelta(seconds=seconds)), int(user_id)),
    )
    _CONN.commit()


# ---------------------------------------------------------------- assertions

_RESULTS = []


def check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return bool(condition)


def check_eq(name, actual, expected):
    ok = actual == expected
    return check(name, ok, "" if ok else f"expected {expected!r}, got {actual!r}")


def section(title):
    print(f"\n=== {title} ===")


def summary(label):
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{label}: {passed}/{total} passed")
    failures = [name for name, ok, _ in _RESULTS if not ok]
    if failures:
        print("FAILURES:")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    return True
