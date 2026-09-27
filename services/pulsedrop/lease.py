"""One curator tick at a time, across every instance, without a scheduler.

## Why a database lease and not a cron service

Railway runs N containers and none of them is special. A cron entry inside the
worker would fire in all of them; a separate scheduler service would be a new
deployable whose only job is to be a single point of failure. The database is
already the thing every instance agrees about, so the lease lives there.

## Why the interval gate and the lease are the same row

They answer the same question from opposite sides. ``expires_at`` says "an
instance is inside a tick right now"; ``next_run_at`` says "no instance may
start one yet". Split across two rows, an instance can take a lease it is not
allowed to use, and the window between the two reads is exactly where a double
publication lives. In one row they are one conditional UPDATE, and the database
decides the winner.

## Why the claim commits immediately

A lease held inside an uncommitted transaction is not held. The claim therefore
runs on its own connection and commits before the tick body starts — which also
means a tick that crashes hard leaves the lease *taken* until it expires, rather
than rolling back and letting a second instance start the same work seconds
later. Expiry, not rollback, is the recovery mechanism, and that is why
``lease_seconds`` must exceed the worst-case tick.

## Why ``next_run_at`` is advanced on claim and again on release

On claim, so a crashed instance cannot make the interval meaningless by
retrying every five seconds — the next attempt is already pushed out. On
release, so the interval is measured from when the tick *finished*. The two
together mean a 90-second tick on a two-hour interval runs at 0:00 and 2:01,
not at 0:00 and 2:00-minus-90-seconds.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta

from services.pulsedrop import config, schema

log = logging.getLogger(__name__)

CURATOR = "curator"

_owner_lock = threading.Lock()
_owner = ""


def owner_id() -> str:
    """A stable-per-process identifier, for diagnosis rather than correctness.

    Correctness comes from the conditional UPDATE, not from the name. The name
    is what makes "which instance has been holding the lease for an hour"
    answerable, so it carries the host and pid.
    """
    global _owner
    with _owner_lock:
        if not _owner:
            host = os.getenv("RAILWAY_REPLICA_ID") or socket.gethostname() or "unknown"
            _owner = f"{host[:40]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        return _owner


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _ensure_row(cur, key: str, now: str) -> None:
    cur.execute(
        """
        INSERT INTO pulsedrop_leases (lease_key, owner, acquired_at, expires_at, next_run_at, updated_at)
        VALUES (?, '', NULL, NULL, NULL, ?)
        ON CONFLICT (lease_key) DO NOTHING
        """,
        (key, now),
    )


def acquire(key: str = CURATOR, *, now: datetime | None = None) -> str:
    """Take the lease if it is free *and* due. Returns the owner id, or ``""``.

    ``""`` is the overwhelmingly common answer — the worker loop runs every five
    seconds and the curator is due every two hours — so it is not an error and is
    not logged.
    """
    schema.ensure_schema()
    from services import db as db_service

    now = now or datetime.utcnow()
    now_text = _iso(now)
    mine = owner_id()
    expires = _iso(now + timedelta(seconds=config.lease_seconds()))
    # Pushed out on claim so a hard crash cannot produce a retry storm. The
    # release path overwrites this with the real next-run time.
    provisional_next = _iso(now + timedelta(seconds=config.evaluation_interval_seconds()))

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        _ensure_row(cur, key, now_text)
        cur.execute(
            """
            UPDATE pulsedrop_leases
            SET owner=?, acquired_at=?, expires_at=?, next_run_at=?, updated_at=?
            WHERE lease_key=?
              AND COALESCE(next_run_at,'') <= ?
              AND COALESCE(expires_at,'') <= ?
            """,
            (mine, now_text, expires, provisional_next, now_text, key, now_text, now_text),
        )
        won = int(cur.rowcount or 0) > 0
        conn.commit()
        return mine if won else ""
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("pulsedrop_lease_acquire_failed key=%s", key, exc_info=True)
        return ""
    finally:
        try:
            conn.close()
        except Exception:
            pass


def release(
    holder: str,
    key: str = CURATOR,
    *,
    now: datetime | None = None,
    next_run_in_seconds: int | None = None,
) -> None:
    """Give the lease back and set the next due time. Never raises.

    Guarded by ``owner=?``: an instance whose lease already expired and was
    stolen must not clear the new holder's claim on its way out. A failure to
    release is survivable — the lease expires — so this swallows everything.
    """
    if not holder:
        return
    from services import db as db_service

    now = now or datetime.utcnow()
    seconds = (
        config.evaluation_interval_seconds()
        if next_run_in_seconds is None
        else max(0, int(next_run_in_seconds))
    )
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulsedrop_leases
            SET owner='', expires_at=NULL, next_run_at=?, updated_at=?
            WHERE lease_key=? AND owner=?
            """,
            (_iso(now + timedelta(seconds=seconds)), _iso(now), key, holder),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_lease_release_failed key=%s", key, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def status(key: str = CURATOR) -> dict:
    """The lease row as the ops surface sees it. ``{}`` when unreadable."""
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM pulsedrop_leases WHERE lease_key=? LIMIT 1", (key,))
        return dict(cur.fetchone() or {})
    except Exception:
        log.debug("pulsedrop_lease_status_unavailable", exc_info=True)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def schedule_next(seconds: int, key: str = CURATOR, *, now: datetime | None = None) -> None:
    """Push the next run out without holding the lease.

    Used by the tick's early exits — kill switch off, nothing eligible — which
    must not re-evaluate five seconds later, and which have nothing to release
    because :func:`acquire` was never called or already returned.
    """
    from services import db as db_service

    now = now or datetime.utcnow()
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        _ensure_row(cur, key, _iso(now))
        cur.execute(
            "UPDATE pulsedrop_leases SET next_run_at=?, updated_at=? WHERE lease_key=?",
            (_iso(now + timedelta(seconds=max(0, int(seconds)))), _iso(now), key),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_schedule_next_failed", exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
