"""Canonical Verification domain schema — additive ``business_os_verification_*`` tables.

Follows the S1 ``ensure_schema`` convention exactly: idempotent ``CREATE TABLE IF NOT
EXISTS`` via ``services.db``, no ``bot.py`` import, never mutating a legacy table.

Tables:

* ``business_os_verification_runs`` — one row per attestation run: business, actor, overall
  ``status`` (pass/fail), passed/total check counts, created_at.
* ``business_os_verification_checks`` — one row per individual check within a run: name,
  category, ``ok`` flag, human-readable detail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:20])


_RUNS = """
CREATE TABLE IF NOT EXISTS business_os_verification_runs (
    run_id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    actor_user_id TEXT,
    status TEXT NOT NULL DEFAULT 'pass',
    checks_total INTEGER NOT NULL DEFAULT 0,
    checks_passed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

_CHECKS = """
CREATE TABLE IF NOT EXISTS business_os_verification_checks (
    check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    detail TEXT,
    created_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_bo_verif_runs_business "
    "ON business_os_verification_runs(business_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_bo_verif_checks_run "
    "ON business_os_verification_checks(run_id)",
)


def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(_RUNS)
        conn.execute(_CHECKS)
        for stmt in _INDEXES:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
