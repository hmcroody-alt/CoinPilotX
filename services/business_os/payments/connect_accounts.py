"""Stripe Connect account state projection (Wave B).

A single row per seller records what Stripe last told us about their Connect
account: can it charge, can it be paid out, what requirements are outstanding.
The row is a *projection* — Stripe is authoritative, and the two write paths
both copy Stripe's words verbatim:

* ``apply_account_updated_event`` consumes an ``account.updated`` webhook.
  An event for an account we cannot attribute to any local user opens an
  ``ORPHAN_STRIPE_OBJECT`` info incident and is otherwise ignored — we never
  invent a user to hang it on.
* ``record_account_snapshot`` consumes the dict shape returned by
  ``services.payment_provider.get_account_status`` (a server-side pull),
  attributed to a known ``user_id`` by the caller.

Engine-portable via ``services.db``; does not import ``bot.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db
from services.business_os.payments import incidents

INCIDENT_DOMAIN = "seller_payments"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {key: row[key] for key in row.keys()}


def ensure_schema(conn=None) -> None:
    """Create the projection table. Idempotent; safe on SQLite and Postgres."""
    own = conn is None
    if own:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connect_account_state (
                user_id TEXT NOT NULL UNIQUE,
                connected_account_id TEXT NOT NULL UNIQUE,
                payouts_enabled INTEGER NOT NULL DEFAULT 0,
                charges_enabled INTEGER NOT NULL DEFAULT 0,
                details_submitted INTEGER NOT NULL DEFAULT 0,
                requirements_json TEXT NOT NULL DEFAULT '{}',
                disabled_reason TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _serialize(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    for flag in ("payouts_enabled", "charges_enabled", "details_submitted"):
        out[flag] = bool(out.get(flag))
    try:
        out["requirements"] = json.loads(out.pop("requirements_json") or "{}")
    except (TypeError, ValueError):
        out["requirements"] = {}
    return out


def get_state(user_id: Any, conn=None) -> Optional[dict]:
    """The last-known Connect state for ``user_id``, or None if never seen."""
    own = conn is None
    if own:
        conn = db.connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM connect_account_state WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        if own:
            conn.commit()
        return _serialize(_row_to_dict(row))
    finally:
        if own:
            conn.close()


def get_state_by_account(connected_account_id: str, conn=None) -> Optional[dict]:
    own = conn is None
    if own:
        conn = db.connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM connect_account_state"
            " WHERE connected_account_id = ?",
            (str(connected_account_id),),
        ).fetchone()
        if own:
            conn.commit()
        return _serialize(_row_to_dict(row))
    finally:
        if own:
            conn.close()


def _upsert(conn, *, user_id: str, connected_account_id: str,
            payouts_enabled: bool, charges_enabled: bool,
            details_submitted: bool, requirements: Mapping[str, Any],
            disabled_reason: str) -> dict:
    now = _utc_now_iso()
    requirements_json = json.dumps(dict(requirements or {}), default=str)
    # Identity only. `user_id` is NOT NULL UNIQUE, so it addresses exactly one
    # row and is the portable stand-in for `rowid`, which Postgres does not have.
    existing = conn.execute(
        "SELECT user_id FROM connect_account_state"
        " WHERE user_id = ? OR connected_account_id = ?",
        (user_id, connected_account_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO connect_account_state
                (user_id, connected_account_id, payouts_enabled,
                 charges_enabled, details_submitted, requirements_json,
                 disabled_reason, last_synced_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, connected_account_id, int(payouts_enabled),
             int(charges_enabled), int(details_submitted), requirements_json,
             disabled_reason, now, now, now),
        )
    else:
        conn.execute(
            """
            UPDATE connect_account_state
               SET connected_account_id = ?, payouts_enabled = ?,
                   charges_enabled = ?, details_submitted = ?,
                   requirements_json = ?, disabled_reason = ?,
                   last_synced_at = ?, updated_at = ?
             WHERE user_id = ?
            """,
            (connected_account_id, int(payouts_enabled), int(charges_enabled),
             int(details_submitted), requirements_json, disabled_reason,
             now, now, existing["user_id"]),
        )
    _project_onto_legacy_row(
        conn,
        connected_account_id=connected_account_id,
        payouts_enabled=payouts_enabled,
        charges_enabled=charges_enabled,
        requirements=requirements,
        disabled_reason=disabled_reason,
    )
    row = conn.execute(
        "SELECT * FROM connect_account_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return _serialize(_row_to_dict(row))


def _project_onto_legacy_row(conn, *, connected_account_id: str,
                             payouts_enabled: bool, charges_enabled: bool,
                             requirements: Mapping[str, Any],
                             disabled_reason: str) -> int:
    """Copy the same Stripe truth onto the legacy ``seller_payout_accounts`` row.

    ``connect_account_state`` is the canonical projection, but it is not what
    the two surfaces that decide anything actually read. The checkout
    capability gate (``services/marketplace_card_capability._seller_row``) and
    the seller payouts screen (``services/seller_money._payout_method``) both
    SELECT from ``seller_payout_accounts``. Before this, only the
    ``account.updated`` webhook refreshed that table; an explicit refresh
    (``record_account_snapshot``) updated the projection and left the gate
    reading the old columns. A seller Stripe had just switched off therefore
    kept a ``charges_enabled = 1`` row — a stale column unlocking card
    checkout.

    Calling this from ``_upsert`` is what makes the two write paths one path:
    every route by which Stripe truth enters the system lands in both tables,
    in the same transaction, from the same normalized values.

    **UPDATE only, never INSERT.** Creating the row belongs to the onboarding
    path, which owns ``seller_type`` — half of this table's unique key — and a
    webhook about an account with no local row must not invent one. A missing
    row already reads as ``STRIPE_NOT_CONNECTED``, which is a refusal, so
    update-only is also the fail-closed choice.

    Matched on the Stripe account id, not on ``user_id``: this module stores
    ``user_id`` as TEXT and the legacy table as INTEGER, and SQLite does not
    compare those equal. The account id is TEXT on both sides.

    Returns the number of rows updated. Never raises.
    """
    now = _utc_now_iso()
    account_id = str(connected_account_id or "")
    if not account_id:
        return 0
    assignments = [
        "charges_enabled = ?", "payouts_enabled = ?", "requirements_json = ?",
        "last_checked_at = ?", "last_synced_at = ?", "updated_at = ?",
    ]
    params: list[Any] = [
        int(bool(charges_enabled)), int(bool(payouts_enabled)),
        json.dumps(dict(requirements or {}), default=str), now, now, now,
    ]
    if str(disabled_reason or "").strip():
        # Stripe naming a `disabled_reason` is Stripe saying this account is
        # restricted, and the gate treats that string as a refusal. Every other
        # value in this column belongs to the onboarding path's vocabulary and
        # is not ours to overwrite — leaving a stale 'complete' here is safe,
        # because the capability flags above are what actually open the rail.
        assignments.append("onboarding_status = ?")
        params.append("restricted")
    params.extend([account_id, account_id])
    try:
        cur = conn.execute(
            "UPDATE seller_payout_accounts SET " + ", ".join(assignments)
            + " WHERE connected_account_id = ? OR provider_account_id = ?",
            tuple(params),
        )
        return int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001 - mirroring must not lose the write
        # Failing to mirror must never lose the canonical write that already
        # happened in this transaction. Same posture as `_lookup_local_user`.
        # The capability gate reads `connect_account_state` directly as well,
        # so a mirror that did not land cannot leave the gate permissive.
        text = str(exc).lower()
        if "no such table" in text or "does not exist" in text:
            # Hermetic projection tests carry no legacy table. Not a fault.
            logging.debug(
                "CONNECT_LEGACY_PROJECTION_SKIPPED_NO_TABLE account=%s", account_id)
        else:
            logging.exception(
                "CONNECT_LEGACY_PROJECTION_FAILED connected_account_id=%s", account_id)
        return 0


def _lookup_local_user(conn, connected_account_id: str) -> str:
    """Best-effort attribution of a Stripe account id to a local user.

    Checks our own projection first, then the legacy read-only
    ``seller_payout_accounts`` mapping written by the old Connect flow.
    Returns '' when no local owner is known.
    """
    row = conn.execute(
        "SELECT user_id FROM connect_account_state WHERE connected_account_id = ?",
        (str(connected_account_id),),
    ).fetchone()
    if row is not None:
        return str(row["user_id"])
    try:
        legacy = conn.execute(
            "SELECT user_id FROM seller_payout_accounts"
            " WHERE connected_account_id = ?",
            (str(connected_account_id),),
        ).fetchone()
        if legacy is not None:
            return str(legacy["user_id"])
    except Exception:
        pass  # legacy table absent in hermetic environments
    return ""


def apply_account_updated_event(event: Mapping[str, Any]) -> dict:
    """Project an ``account.updated`` Stripe webhook into local state.

    Attribution order: metadata.user_id on the account (set by our account
    creation), then existing projection row, then the legacy mapping table.
    Unattributable accounts open an ORPHAN_STRIPE_OBJECT info incident.
    """
    event = dict(event or {})
    event_type = str(event.get("type") or "")
    if event_type != "account.updated":
        return {"ok": True, "ignored": True, "reason": "unhandled_event_type"}
    obj = dict(((event.get("data") or {}).get("object")) or {})
    connected_account_id = str(obj.get("id") or "")
    if not connected_account_id:
        return {"ok": True, "ignored": True, "reason": "missing_account_id"}

    metadata = dict(obj.get("metadata") or {})
    user_id = str(metadata.get("user_id") or "").strip()

    conn = db.connect()
    try:
        ensure_schema(conn)
        if not user_id:
            user_id = _lookup_local_user(conn, connected_account_id)
        if not user_id:
            incidents.open_incident(
                incidents.ORPHAN_STRIPE_OBJECT,
                domain=INCIDENT_DOMAIN,
                severity="info",
                summary=(
                    "account.updated for Stripe account "
                    f"{connected_account_id} with no local owner"
                ),
                details={"event_id": str(event.get("id") or "")},
                related_object=f"stripe_account:{connected_account_id}",
                stripe_ref=connected_account_id,
                incident_key=(
                    f"{incidents.ORPHAN_STRIPE_OBJECT}:"
                    f"stripe_account:{connected_account_id}"
                ),
            )
            conn.commit()
            return {"ok": True, "ignored": True, "orphan": True,
                    "connected_account_id": connected_account_id}

        requirements = dict(obj.get("requirements") or {})
        disabled_reason = str(requirements.get("disabled_reason") or "")
        state = _upsert(
            conn,
            user_id=user_id,
            connected_account_id=connected_account_id,
            payouts_enabled=bool(obj.get("payouts_enabled")),
            charges_enabled=bool(obj.get("charges_enabled")),
            details_submitted=bool(obj.get("details_submitted")),
            requirements=requirements,
            disabled_reason=disabled_reason,
        )
        conn.commit()
        return {"ok": True, "ignored": False, "state": state}
    finally:
        conn.close()


def record_account_snapshot(user_id: Any, account_status: Mapping[str, Any]) -> dict:
    """Project a ``payment_provider.get_account_status`` result for a known user."""
    status = dict(account_status or {})
    if not status.get("ok"):
        return {"ok": False, "ignored": True, "reason": "status_not_ok"}
    connected_account_id = str(status.get("provider_account_id") or "")
    if not connected_account_id:
        return {"ok": False, "ignored": True, "reason": "missing_account_id"}
    account = dict(status.get("account") or {})
    requirements = dict(status.get("requirements") or account.get("requirements") or {})
    disabled_reason = str(requirements.get("disabled_reason") or "")
    conn = db.connect()
    try:
        ensure_schema(conn)
        state = _upsert(
            conn,
            user_id=str(user_id),
            connected_account_id=connected_account_id,
            payouts_enabled=bool(status.get("payouts_enabled")),
            charges_enabled=bool(status.get("charges_enabled")),
            details_submitted=bool(account.get("details_submitted")),
            requirements=requirements,
            disabled_reason=disabled_reason,
        )
        conn.commit()
        return {"ok": True, "ignored": False, "state": state}
    finally:
        conn.close()
