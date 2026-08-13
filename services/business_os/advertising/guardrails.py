"""Account-level spend ceilings and the emergency stop.

This module lives in the canonical advertising package rather than in
``ads_intelligence`` on purpose. Everything in the intelligence layer is
advisory by construction — it measures, diagnoses and proposes, and it is
structurally incapable of stopping delivery. A spend ceiling is the opposite: it
stops delivery, and stopping delivery stops charging. That is a money authority,
so it belongs next to the ledger and the eligibility gate, under the same audit
trail as every other decision that costs an advertiser money.

What was missing
----------------
Per-campaign budgets already exist and are enforced. The wallet already refuses
to overdraft. Between them there was still no answer to "this advertiser is
spending faster than they meant to across forty campaigns" and no way for an
operator to stop one account without pausing its campaigns one at a time.

Two guards, deliberately failing in opposite directions
--------------------------------------------------------
The asymmetry below is the most important thing in this file.

**The daily ceiling fails OPEN.** If the spend read fails, delivery continues.
This looks wrong and is not: the ceiling is a convenience above two harder
limits that are still in force — the per-campaign budget and an overdraft-guarded
ledger that cannot go negative. A ceiling that fails closed would convert a
transient database error into a total advertising outage for every account, to
protect against an overspend the ledger already prevents.

**The emergency stop fails CLOSED.** If the halt read fails, delivery stops for
that advertiser. An emergency stop that a failed query can silently lift is not
an emergency stop. The blast radius is one account rather than the platform,
which is what makes the strict direction affordable here.

Nothing here moves money
------------------------
This module reads spend and writes a policy row. It never writes to the ledger,
never to ``business_os_ad_billing_events``, and never changes a campaign's
budget. It answers "may this account deliver right now"; billing remains the
sole authority on what is actually charged.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db

_LOG = logging.getLogger(__name__)

#: Reasons `check` can refuse. Stable strings — they are surfaced to advertisers
#: and asserted on in tests, so they are part of the contract.
REASON_OK = "ok"
REASON_HALTED = "account_delivery_halted"
REASON_DAILY_CEILING = "account_daily_ceiling_reached"
REASON_HALT_UNREADABLE = "account_halt_state_unreadable"

#: A ceiling of 0 means "no ceiling", not "spend nothing". A limit column that
#: defaults to 0 would otherwise silently stop every account the first time this
#: table is populated by a migration.
NO_LIMIT = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _day_start_iso(now: Optional[datetime] = None) -> str:
    """Start of the current UTC day, as the ISO prefix stored in created_at.

    UTC rather than the advertiser's local day: the ledger, the billing events
    and the spend accumulator are all UTC, and a ceiling that resets on a
    different clock from the spend it measures is a ceiling that can be crossed
    twice in one day.
    """
    moment = now or _now()
    return _iso(moment.replace(hour=0, minute=0, second=0, microsecond=0))


def ensure_schema(conn=None) -> None:
    """Idempotent. Safe to call on every boot, as the package convention requires."""
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_os_ad_account_guardrails (
                advertiser_user_id TEXT PRIMARY KEY,
                daily_spend_limit_cents INTEGER NOT NULL DEFAULT 0
                    CHECK (daily_spend_limit_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                delivery_halted INTEGER NOT NULL DEFAULT 0,
                halt_reason TEXT,
                halted_by TEXT,
                halted_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_guardrails_halted "
            "ON business_os_ad_account_guardrails (delivery_halted)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def _row(conn, advertiser_user_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT advertiser_user_id, daily_spend_limit_cents, currency, "
        "delivery_halted, halt_reason, halted_by, halted_at "
        "FROM business_os_ad_account_guardrails WHERE advertiser_user_id = ?",
        (advertiser_user_id,)).fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    keys = ("advertiser_user_id", "daily_spend_limit_cents", "currency",
            "delivery_halted", "halt_reason", "halted_by", "halted_at")
    return {k: row[i] for i, k in enumerate(keys) if i < len(row)}


def account_spend_today(conn, advertiser_user_id: Any, *, currency: str = "usd",
                        now: Optional[datetime] = None) -> int:
    """Cents charged to this advertiser today, across every campaign.

    Read from ``business_os_ad_billing_events`` — the canonical record of what
    was actually charged — rather than from the intelligence layer's event log,
    which measures delivery and explicitly does not decide what is billable.
    Failed billing events are excluded because they were never charged.
    """
    day_start = _day_start_iso(now)
    row = conn.execute(
        "SELECT COALESCE(SUM(total_amount_cents), 0) "
        "FROM business_os_ad_billing_events "
        "WHERE advertiser_user_id = ? AND currency = ? "
        "AND created_at >= ? AND billing_status != 'failed'",
        (str(advertiser_user_id), str(currency or "usd").lower(),
         day_start)).fetchone()
    try:
        return int((row or [0])[0] or 0)
    except (TypeError, ValueError):
        return 0


def check(advertiser_user_id: Any, *, currency: str = "usd", conn=None,
          now: Optional[datetime] = None) -> dict:
    """May this account deliver right now? ``{allowed, reason, ...}``.

    Precedence is halt → ceiling. An account that has been stopped by an
    operator is stopped regardless of how much of its ceiling is left, and the
    reason returned says so, because "you have reached your daily limit" sent to
    an advertiser who was actually halted for policy is a misleading answer that
    generates a support ticket and erodes trust in every other message.
    """
    result = {
        "allowed": True,
        "reason": REASON_OK,
        "advertiser_user_id": str(advertiser_user_id or ""),
        "halted": False,
        "daily_limit_cents": NO_LIMIT,
        "spent_today_cents": 0,
        "remaining_cents": None,
        "degraded": False,
    }
    if not result["advertiser_user_id"]:
        return result

    owned = conn is None
    conn = conn or db.connect()
    try:
        try:
            row = _row(conn, result["advertiser_user_id"])
        except Exception:
            # Fails CLOSED: an unreadable halt state is treated as halted. See
            # the module docstring — a stop a failed query can lift is not one.
            _LOG.warning("AD_GUARDRAIL_READ_FAILED", exc_info=True)
            result.update(allowed=False, reason=REASON_HALT_UNREADABLE,
                          degraded=True)
            return result

        if row is None:
            # No row is the normal case for most accounts: no ceiling, no halt.
            return result

        if int(row.get("delivery_halted") or 0):
            result.update(allowed=False, reason=REASON_HALTED, halted=True,
                          halt_reason=row.get("halt_reason"))
            return result

        limit = int(row.get("daily_spend_limit_cents") or NO_LIMIT)
        result["daily_limit_cents"] = limit
        if limit <= NO_LIMIT:
            return result

        try:
            spent = account_spend_today(conn, result["advertiser_user_id"],
                                        currency=currency, now=now)
        except Exception:
            # Fails OPEN: the per-campaign budget and the overdraft-guarded
            # ledger are both still enforcing. Blocking every account on a
            # transient read error would be the larger failure.
            _LOG.warning("AD_GUARDRAIL_SPEND_READ_FAILED", exc_info=True)
            result["degraded"] = True
            return result

        result["spent_today_cents"] = spent
        result["remaining_cents"] = max(limit - spent, 0)
        if spent >= limit:
            result.update(allowed=False, reason=REASON_DAILY_CEILING)
        return result
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Operator actions. Every one of these is audited.
# --------------------------------------------------------------------------- #

def _audit(conn, advertiser_user_id: str, action: str, actor: Any,
           reason: Any, after: dict) -> None:
    conn.execute(
        "INSERT INTO business_os_ad_audit (campaign_id, advertiser_user_id, "
        "action, actor, reason, before_json, after_json, created_at) "
        "VALUES (NULL, ?, ?, ?, ?, NULL, ?, ?)",
        (advertiser_user_id, action, str(actor or "system"),
         str(reason or ""), json.dumps(after), _iso(_now())))


def _upsert(conn, advertiser_user_id: str, **fields) -> None:
    """Insert-or-update without relying on a dialect-specific UPSERT clause.

    ``ON CONFLICT`` differs enough between SQLite and PostgreSQL versions that
    the read-then-write here is the portable option, and this table is written
    by operators rather than by delivery, so the race window does not matter.
    """
    existing = _row(conn, advertiser_user_id)
    fields["updated_at"] = _iso(_now())
    if existing is None:
        fields.setdefault("daily_spend_limit_cents", NO_LIMIT)
        fields.setdefault("currency", "usd")
        fields.setdefault("delivery_halted", 0)
        fields["advertiser_user_id"] = advertiser_user_id
        cols = sorted(fields)
        conn.execute(
            f"INSERT INTO business_os_ad_account_guardrails "
            f"({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(fields[c] for c in cols))
        return
    cols = sorted(fields)
    conn.execute(
        f"UPDATE business_os_ad_account_guardrails "
        f"SET {', '.join(c + ' = ?' for c in cols)} "
        f"WHERE advertiser_user_id = ?",
        tuple(fields[c] for c in cols) + (advertiser_user_id,))


def set_daily_ceiling(advertiser_user_id: Any, limit_cents: Any, *,
                      actor: Any = None, reason: Any = None,
                      currency: str = "usd", conn=None) -> dict:
    """Set (or with 0, remove) an account's daily spend ceiling."""
    advertiser = str(advertiser_user_id or "").strip()
    if not advertiser:
        raise ValueError("advertiser_user_id is required")
    try:
        limit = int(limit_cents)
    except (TypeError, ValueError):
        raise ValueError("limit_cents must be an integer number of cents")
    if limit < 0:
        raise ValueError("limit_cents may not be negative")

    owned = conn is None
    conn = conn or db.connect()
    try:
        _upsert(conn, advertiser, daily_spend_limit_cents=limit,
                currency=str(currency or "usd").lower())
        _audit(conn, advertiser, "account_daily_ceiling_set", actor, reason,
               {"daily_spend_limit_cents": limit})
        if owned:
            conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return {"advertiser_user_id": advertiser, "daily_spend_limit_cents": limit}


def halt_account_delivery(advertiser_user_id: Any, *, actor: Any = None,
                          reason: Any = None, conn=None) -> dict:
    """The emergency stop. Every campaign on this account stops serving.

    Deliberately does NOT pause the campaigns themselves. A halt is a temporary
    operator action; rewriting each campaign's operational state would lose the
    advertiser's own paused/active intent and could not be cleanly undone, so
    lifting the halt would silently resume campaigns the advertiser had paused.
    """
    advertiser = str(advertiser_user_id or "").strip()
    if not advertiser:
        raise ValueError("advertiser_user_id is required")
    stamp = _iso(_now())

    owned = conn is None
    conn = conn or db.connect()
    try:
        _upsert(conn, advertiser, delivery_halted=1,
                halt_reason=str(reason or "") or None,
                halted_by=str(actor or "system"), halted_at=stamp)
        _audit(conn, advertiser, "account_delivery_halted", actor, reason,
               {"delivery_halted": 1, "halted_at": stamp})
        if owned:
            conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return {"advertiser_user_id": advertiser, "delivery_halted": True,
            "halted_at": stamp}


def lift_account_halt(advertiser_user_id: Any, *, actor: Any = None,
                      reason: Any = None, conn=None) -> dict:
    """Lift the emergency stop. Audited as its own action, never as an edit."""
    advertiser = str(advertiser_user_id or "").strip()
    if not advertiser:
        raise ValueError("advertiser_user_id is required")

    owned = conn is None
    conn = conn or db.connect()
    try:
        _upsert(conn, advertiser, delivery_halted=0, halt_reason=None,
                halted_by=None, halted_at=None)
        _audit(conn, advertiser, "account_delivery_halt_lifted", actor, reason,
               {"delivery_halted": 0})
        if owned:
            conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return {"advertiser_user_id": advertiser, "delivery_halted": False}


def explain(result: dict) -> str:
    """The refusal, in words an advertiser can act on."""
    if not result or result.get("allowed"):
        return "This account is able to deliver."
    reason = result.get("reason")
    if reason == REASON_HALTED:
        detail = result.get("halt_reason")
        return ("Delivery on this account has been stopped by PulseSoc"
                + (f": {detail}." if detail else ". Support can tell you more."))
    if reason == REASON_DAILY_CEILING:
        return (f"This account has reached the daily spend limit you set "
                f"({result.get('daily_limit_cents', 0) / 100:.2f}). Delivery "
                f"resumes tomorrow, or when you raise the limit.")
    return "This account cannot deliver right now."
