"""Shared fixtures for the Business OS suites.

Why this file exists
--------------------
Several suites in this directory each seeded their own minimal ``users`` table
with ``CREATE TABLE IF NOT EXISTS`` against the *same* test database. That makes
the winner run-order dependent: whichever module executed first defined the
shape, and every later module that needed a column the winner had not declared
died in ``setup_module`` with ``table users has no column named ...``. Running a
file on its own passed; running the directory produced dozens of errors that
moved around whenever a file was added or renamed.

The fix is to define the table once, additively, before any module-level setup
runs. Each suite's own ``CREATE TABLE IF NOT EXISTS users`` then becomes a
harmless no-op and its inserts find the columns they expect.

The column list is the union of what the suites in this directory actually
insert, and the key is ``user_id`` — the same key the canonical table in
``bot.init_db`` uses. Matching the production column name is the point: a
fixture keyed on ``id`` is what previously hid two real delivery bugs, because
the tests were exercising a schema production does not have.
"""

from __future__ import annotations

import pytest

# NOTE: ``services.db`` is imported lazily, inside the fixture, and never at
# module scope. Each suite here points ``DATABASE_URL`` at its own temp file at
# import time and the first import wins, binding the engine for the whole
# session. A conftest-level import would run during collection — before any of
# that — and bind the engine to the developer's real database instead.

# (column, SQL type). ``user_id`` is the primary key and is created with the
# table; the rest are added additively so an already-created table is upgraded
# rather than conflicting with it.
# The DEFAULTs are load-bearing, not decoration: the entitlement suites insert a
# user without naming these columns and then assert the account reads as active
# and access-enabled. Declaring them without the default would turn those rows
# NULL and silently invert what those tests check.
_USERS_COLUMNS = (
    ("username", "TEXT"),
    ("display_name", "TEXT"),
    ("full_name", "TEXT"),
    ("avatar_url", "TEXT"),
    ("email", "TEXT"),
    ("verified_badge", "INTEGER"),
    ("created_at", "TEXT"),
    ("account_status", "TEXT DEFAULT 'active'"),
    ("access_enabled", "INTEGER DEFAULT 1"),
)


def _existing_columns(db, conn, table: str) -> set:
    try:
        if db.ENGINE_NAME == "sqlite":
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


@pytest.fixture(scope="session", autouse=True)
def _canonical_users_table():
    """Define ``users`` once, additively, before any module setup runs.

    Session-scoped so it is ordered ahead of the xunit-style ``setup_module``
    hooks these suites use (pytest sets higher-scoped fixtures up first).
    Best-effort: a failure here must not mask the real assertion in a test, and
    suites that never touch ``users`` are unaffected either way.
    """
    try:
        from services import db  # noqa: PLC0415 — deliberately lazy, see above
        conn = db.connect()
    except Exception:
        yield
        return
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)"
        )
        present = _existing_columns(db, conn, "users")
        for name, sql_type in _USERS_COLUMNS:
            if name in present:
                continue
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
            except Exception:
                # Another suite may have added it concurrently; harmless.
                pass
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    yield
