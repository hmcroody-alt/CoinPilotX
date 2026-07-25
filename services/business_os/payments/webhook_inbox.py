"""Durable, idempotent provider-webhook inbox (Stage 1 first slice).

Fixes the two webhook defects the Stage 0 inventory found:

1. **Persist-before-process.** ``enqueue_event`` writes the raw event to the
   inbox *before* any business handler runs, so a crash mid-processing loses
   nothing — the event can be replayed.
2. **Idempotent + replay-resistant.** ``UNIQUE (provider, provider_event_id)``
   makes re-delivery a no-op. Out-of-order and delayed deliveries are safe
   because processing is driven off the persisted row, and downstream handlers
   (e.g. the ledger) are themselves idempotency-keyed.

A ``reconcile_pending`` sweep reprocesses anything left ``received``,
``failed`` (under the retry cap), or stranded in ``processing`` by a crash.

Engine-portable via ``services.db``; does not import ``bot.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from services import db

STATUS_RECEIVED = "received"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

_TERMINAL = {STATUS_PROCESSED, STATUS_SKIPPED}
DEFAULT_MAX_RETRIES = 5


class WebhookInboxError(ValueError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_unique_violation(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "integrityerror" in name or "uniqueviolation" in name:
        return True
    return "unique" in msg or "duplicate key" in msg


def _begin(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")


def _commit(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        conn.execute("COMMIT")
    else:
        conn.commit()


def _rollback(conn) -> None:
    try:
        if db.ENGINE_NAME == "sqlite":
            conn.execute("ROLLBACK")
        else:
            conn.rollback()
    except Exception:
        pass


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                event_type TEXT,
                payload_json TEXT NOT NULL,
                signature_verified INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'received',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                UNIQUE (provider, provider_event_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webhook_events_status "
            "ON provider_webhook_events (status)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def get_event(provider: str, provider_event_id: str, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM provider_webhook_events "
            "WHERE provider = ? AND provider_event_id = ?",
            (provider, provider_event_id),
        )
        return _row_to_dict(cur.fetchone())
    finally:
        if owned:
            conn.close()


def enqueue_event(
    *,
    provider: str,
    provider_event_id: str,
    payload: Mapping[str, Any],
    event_type: str = "",
    signature_verified: bool = False,
) -> dict:
    """Persist a received event before processing. Duplicate = no-op.

    Returns the stored row plus ``duplicate`` (True if this event id was
    already recorded — re-delivery/replay).
    """
    if not provider or not str(provider).strip():
        raise WebhookInboxError("provider is required")
    if not provider_event_id or not str(provider_event_id).strip():
        raise WebhookInboxError("provider_event_id is required")

    now = _utc_now_iso()
    payload_json = json.dumps(payload if payload is not None else {})

    conn = db.connect()
    try:
        _begin(conn)
        try:
            conn.execute(
                """
                INSERT INTO provider_webhook_events
                    (provider, provider_event_id, event_type, payload_json,
                     signature_verified, status, retry_count, received_at)
                VALUES (?, ?, ?, ?, ?, 'received', 0, ?)
                """,
                (
                    provider, provider_event_id, event_type or None,
                    payload_json, 1 if signature_verified else 0, now,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if _is_unique_violation(exc):
                _rollback(conn)
                existing = get_event(provider, provider_event_id, conn=conn)
                if existing is not None:
                    existing["duplicate"] = True
                    return existing
                return {
                    "provider": provider,
                    "provider_event_id": provider_event_id,
                    "duplicate": True,
                }
            _rollback(conn)
            raise
        _commit(conn)
        stored = get_event(provider, provider_event_id, conn=conn) or {}
        stored["duplicate"] = False
        return stored
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def _claim(conn, event_id: int) -> bool:
    """Atomically move a row to 'processing' if it is claimable. Returns True
    if this caller won the claim (prevents double-processing under concurrency).
    """
    _begin(conn)
    try:
        cur = conn.execute(
            "UPDATE provider_webhook_events SET status = 'processing' "
            "WHERE id = ? AND status IN ('received','failed','processing')",
            (event_id,),
        )
        claimed = getattr(cur, "rowcount", 0) == 1
        _commit(conn)
        return claimed
    except Exception:
        _rollback(conn)
        raise


def process_event(
    provider: str,
    provider_event_id: str,
    handler: Callable[[dict], Any],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """Run ``handler(payload)`` for a persisted event exactly once.

    Already-terminal events are skipped. ``handler`` must be idempotent; the
    inbox guarantees single-claim semantics but a handler may still be retried
    after a failure, so downstream writes should be idempotency-keyed.
    """
    conn = db.connect()
    try:
        row = get_event(provider, provider_event_id, conn=conn)
        if row is None:
            raise WebhookInboxError(
                f"unknown event {provider}:{provider_event_id}"
            )
        if row["status"] in _TERMINAL:
            return {"status": row["status"], "skipped": True}
        if int(row.get("retry_count") or 0) >= max_retries:
            return {"status": STATUS_FAILED, "exhausted": True}

        if not _claim(conn, int(row["id"])):
            # Someone else claimed it; report current state without racing.
            latest = get_event(provider, provider_event_id, conn=conn) or {}
            return {"status": latest.get("status", "unknown"), "skipped": True}

        payload = json.loads(row["payload_json"])
        try:
            handler(payload)
        except Exception as exc:  # noqa: BLE001 — handler failure is expected-path
            _begin(conn)
            try:
                conn.execute(
                    "UPDATE provider_webhook_events "
                    "SET status = 'failed', retry_count = retry_count + 1, last_error = ? "
                    "WHERE id = ?",
                    (str(exc)[:1000], int(row["id"])),
                )
                _commit(conn)
            except Exception:
                _rollback(conn)
                raise
            return {"status": STATUS_FAILED, "error": str(exc)}

        now = _utc_now_iso()
        _begin(conn)
        try:
            conn.execute(
                "UPDATE provider_webhook_events "
                "SET status = 'processed', processed_at = ?, last_error = NULL "
                "WHERE id = ?",
                (now, int(row["id"])),
            )
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
        return {"status": STATUS_PROCESSED, "processed_at": now}
    finally:
        conn.close()


def reconcile_pending(
    handler: Callable[[dict], Any],
    *,
    provider: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    limit: int = 200,
) -> dict:
    """Replay every non-terminal event (received / failed-under-cap / stranded
    processing). Safe to run on a schedule. Returns a summary count.
    """
    conn = db.connect()
    try:
        params: list = []
        sql = (
            "SELECT provider, provider_event_id FROM provider_webhook_events "
            "WHERE status IN ('received','failed','processing') "
            "AND retry_count < ? "
        )
        params.append(max_retries)
        if provider:
            sql += "AND provider = ? "
            params.append(provider)
        sql += "ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = [_row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()

    processed = failed = skipped = 0
    for r in rows:
        result = process_event(
            r["provider"], r["provider_event_id"], handler, max_retries=max_retries
        )
        status = result.get("status")
        if status == STATUS_PROCESSED:
            processed += 1
        elif status == STATUS_FAILED:
            failed += 1
        else:
            skipped += 1
    return {
        "examined": len(rows),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
    }
