"""Shared initialization for the UNDX worker, owned by neither the web app nor the worker.

Before this module existed, ``undx_worker.py`` opened with ``import bot``. That single
line pulled the entire Flask application into a background process: 111k lines, ~1,538
route registrations, every optional route pack, and the whole third-party dependency set
including Stripe, LiveKit and the payment integrations. The worker needed exactly two
things from it — schema creation and a heartbeat write — and paid for them with the
web service's entire import graph.

The cost was not theoretical. ``import undx_worker`` failed locally with
``ModuleNotFoundError: No module named 'stripe'``, raised from ``bot.py`` line 22, in a
process that will never take a payment. A worker that cannot be imported without the
payment SDK cannot be reasoned about independently, cannot be given a reduced credential
set, and cannot be tested without standing up the web application's dependencies.

So this module provides those two things directly, over ``services.db``, which depends on
nothing but SQLAlchemy and the standard library.

**Least privilege, applied to schema.** ``ensure_worker_schema`` deliberately does *not*
reproduce ``bot.init_db()``. That function creates roughly 170 tables spanning payments,
media, live sessions, marketplace and the rest of the product — none of which a worker
executing a governed capability has any business creating. This module creates only the
tables the worker itself owns, and delegates the UNDX tables to the modules that define
them, so there is exactly one definition of each.

The tables a *capability* touches are not created here either, and that is the point. The
web service owns the product schema and creates it on boot. If a worker reaches an
executor whose table does not exist, the gateway fails closed and the run is settled as
failed — which is the correct outcome. A worker that quietly creates a domain table it
does not own would be a worse outcome wearing the costume of a better one.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from services import db as db_module


HEARTBEAT_TABLE = "worker_heartbeats"

#: Mirrors the definition in ``bot.init_db()`` exactly. Duplicated rather than imported
#: because importing it is the entire problem this module solves, and because
#: ``CREATE TABLE IF NOT EXISTS`` from two processes against one database is safe in a
#: way that two *different* definitions would not be. A drift test asserts the column
#: set matches what the web service writes, so this copy cannot silently diverge.
HEARTBEAT_DDL = """
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_name TEXT PRIMARY KEY,
    status TEXT,
    last_seen_at TEXT,
    last_error TEXT,
    metadata_json TEXT
)
"""

HEARTBEAT_COLUMNS = ("worker_name", "status", "last_seen_at", "last_error", "metadata_json")

MAX_ERROR_CHARS = 1000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def connect():
    """Open a connection the same way every other process in this codebase does."""
    return db_module.connect()


def ensure_worker_schema(cur) -> None:
    """Create only what the worker owns, and let each UNDX module own its own tables.

    Imports are function-local on purpose. The UNDX modules import this one indirectly
    through the worker, and a module-level import here would close that loop. It also
    keeps ``ensure_worker_schema`` the single place where the worker's schema surface is
    enumerated, rather than spreading it across import statements at the top of a file.
    """
    cur.execute(HEARTBEAT_DDL)

    from services import undx_agent_runs
    from services import undx_architecture
    from services import undx_mission_runtime

    # Order matters only for readability; each is independently idempotent. The
    # architecture tables come first because they hold the confirmations and tool
    # operations that both of the other two read through the gateway.
    undx_architecture.ensure_schema(cur)
    undx_mission_runtime.ensure_schema(cur)
    undx_agent_runs.ensure_schema(cur)


def init_worker_db() -> None:
    """Idempotent startup schema pass. Failure is logged and raised, never swallowed.

    A worker that starts without its own tables would claim nothing and report healthy
    forever, which is the most expensive kind of silence. Better to crash on boot where
    Railway will show it.
    """
    conn = connect()
    try:
        cur = conn.cursor()
        ensure_worker_schema(cur)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            logging.debug("worker schema connection close failed", exc_info=True)


def record_worker_heartbeat(
    worker_name: str,
    status: str = "healthy",
    last_error: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Write the worker's heartbeat row.

    Byte-compatible with ``bot.record_worker_heartbeat`` — same table, same columns, same
    upsert, same truncation — because the admin dashboards, ``/health/undx`` and the stale
    worker counters all read these rows and must not be able to tell which process wrote
    one.

    Swallows its own exceptions, matching the original. A heartbeat is an observation of
    the worker, and failing to record an observation must never be the thing that stops
    the work being observed.
    """
    try:
        conn = connect()
    except Exception:
        logging.exception("Worker heartbeat connection failed for %s", worker_name)
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO worker_heartbeats (worker_name, status, last_seen_at, last_error, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_name) DO UPDATE SET
                status=excluded.status,
                last_seen_at=excluded.last_seen_at,
                last_error=excluded.last_error,
                metadata_json=excluded.metadata_json
            """,
            (
                str(worker_name),
                str(status or ""),
                _utc_now_iso(),
                str(last_error or "")[:MAX_ERROR_CHARS],
                json.dumps(dict(metadata or {}), sort_keys=True, default=str),
            ),
        )
        conn.commit()
    except Exception:
        logging.exception("Worker heartbeat write failed for %s", worker_name)
    finally:
        try:
            conn.close()
        except Exception:
            logging.debug("heartbeat connection close failed", exc_info=True)


def read_worker_heartbeat(cur, worker_name: str) -> dict:
    """Read one heartbeat row. Returns ``{}`` rather than raising when absent.

    Used by the health surface to report heartbeat age. Absence and staleness are
    different facts and the caller distinguishes them; this returns the row or nothing
    and does not editorialise.
    """
    try:
        cur.execute(
            "SELECT worker_name, status, last_seen_at, last_error, metadata_json "
            "FROM worker_heartbeats WHERE worker_name=? LIMIT 1",
            (str(worker_name),),
        )
        row = cur.fetchone()
    except Exception:
        logging.debug("heartbeat read failed for %s", worker_name, exc_info=True)
        return {}
    return dict(row) if row else {}


__all__ = [
    "HEARTBEAT_COLUMNS",
    "HEARTBEAT_DDL",
    "connect",
    "ensure_worker_schema",
    "init_worker_db",
    "read_worker_heartbeat",
    "record_worker_heartbeat",
]
