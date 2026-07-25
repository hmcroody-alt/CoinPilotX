"""Merchant automation engine — deterministic rule evaluation over the append-only
logs (Stage 6).

Records rule definitions and signal facts (idempotently), then evaluates every active
rule for a merchant against the **latest** signal per ``(subject_ref, signal_type)``
and emits a rebuildable projection of **proposed actions**.

A rule is ``latest(signal_type) <operator> threshold -> suggest action_type``. The
supported operators are the six numeric comparisons ``lt / lte / gt / gte / eq / ne``.
Values and thresholds are decimal strings compared as ``Decimal`` (transparent,
engine-portable).

Determinism discipline: evaluation has no randomness. Proposals are ordered by an
explicit tie-break — ``priority`` descending, then ``rule_id`` ascending, then
``subject_ref`` ascending — so the output is fully reproducible. The proposals table
is a *projection*: recomputing a merchant is deterministic and idempotent (it replaces
that merchant's rows, and the UNIQUE ``(merchant_id, rule_id, subject_ref)`` key
guarantees exactly-one row per matched pair).

Hard boundary — nothing here moves money or takes an action. A proposal is a
suggestion, a reporting quantity, not an instruction. No stock is reordered, no order
is placed, no price is changed, no notification is sent.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, getcontext
from typing import Any, Optional

from services import db
from services.business_os.merchant_automation import schema as _schema


getcontext().prec = 40

VALID_OPERATORS = ("lt", "lte", "gt", "gte", "eq", "ne")

# Each operator as a pure comparison of two Decimals. Deterministic, total.
_OPS = {
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}

_OP_SYMBOL = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "==", "ne": "!="}


class MerchantAutomationError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _norm_ts(value: Any) -> str:
    if value in (None, ""):
        return _now()
    return str(value)


def _to_decimal(value: Any, field: str) -> Decimal:
    """Parse a numeric value/threshold into Decimal, raising a curated error."""
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        raise MerchantAutomationError(f"{field} must be numeric")


def _fmt_num(value: Any) -> str:
    """Canonical decimal string (so a threshold and observed value read consistently)."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return str(value)
    return format(d, "f")


def _meta_json(meta: Any) -> Optional[str]:
    if meta in (None, ""):
        return None
    try:
        return json.dumps(meta, sort_keys=True)[:4000]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_rule(merchant_id: str, signal_type: str, operator: str, threshold: Any,
                action_type: str, *, name: Optional[str] = None,
                active: Any = True, priority: Any = 0, source: str = "manual",
                external_ref: Optional[str] = None, meta: Any = None,
                conn=None) -> dict:
    """Declare a rule for a merchant. Idempotent on ``(source, external_ref)`` (NULL
    ref exempt). Returns the new (or deduped) rule id."""
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        raise MerchantAutomationError("merchant_id is required")
    signal_type = str(signal_type or "").strip()
    if not signal_type:
        raise MerchantAutomationError("signal_type is required")
    operator = str(operator or "").strip().lower()
    if operator not in VALID_OPERATORS:
        raise MerchantAutomationError(f"unknown operator: {operator!r}")
    action_type = str(action_type or "").strip()
    if not action_type:
        raise MerchantAutomationError("action_type is required")
    threshold_d = _to_decimal(threshold, "threshold")
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        raise MerchantAutomationError("priority must be an integer")
    active_i = 1 if str(active).strip().lower() in ("1", "true", "yes", "on") \
        or active is True else 0

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT rule_id FROM business_os_merchant_rules "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"rule_id": dup["rule_id"], "recorded": False,
                        "deduped": True}
        rule_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_merchant_rules "
            "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
            "active,priority,source,external_ref,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rule_id, merchant_id, name, signal_type, operator, _fmt_num(threshold_d),
             action_type, active_i, priority, source, external_ref,
             _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"rule_id": rule_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_signal(merchant_id: str, subject_ref: str, signal_type: str, value: Any, *,
                  observed_at: Any = None, source: str = "manual",
                  external_ref: Optional[str] = None, meta: Any = None,
                  conn=None) -> dict:
    """Append one signal fact. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt). Never updates in place — a later ``observed_at`` supersedes."""
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        raise MerchantAutomationError("merchant_id is required")
    subject_ref = str(subject_ref or "").strip()
    if not subject_ref:
        raise MerchantAutomationError("subject_ref is required")
    signal_type = str(signal_type or "").strip()
    if not signal_type:
        raise MerchantAutomationError("signal_type is required")
    value_d = _to_decimal(value, "value")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT signal_id FROM business_os_merchant_signals "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"signal_id": existing["signal_id"], "recorded": False,
                        "deduped": True}
        sid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_merchant_signals "
            "(signal_id,merchant_id,subject_ref,signal_type,value,observed_at,"
            "source,external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, merchant_id, subject_ref, signal_type, _fmt_num(value_d),
             _norm_ts(observed_at), source, external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"signal_id": sid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# evaluation internals (read-only over the logs)
# ---------------------------------------------------------------------------
def _active_rules(conn, merchant_id: str) -> list:
    """Active rule rows for a merchant (as dicts)."""
    rows = conn.execute(
        "SELECT rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
        "priority FROM business_os_merchant_rules "
        "WHERE merchant_id = ? AND active = 1", (str(merchant_id),)).fetchall()
    return [dict(r) for r in rows]


def _latest_signals(conn, merchant_id: str) -> dict:
    """Return {(subject_ref, signal_type): (value_str, observed_at)} = the latest
    observed value per key for a merchant. Latest = max observed_at, then max
    created_at as a deterministic tie-break."""
    rows = conn.execute(
        "SELECT subject_ref,signal_type,value,observed_at,created_at "
        "FROM business_os_merchant_signals WHERE merchant_id = ? "
        "ORDER BY observed_at ASC, created_at ASC", (str(merchant_id),)).fetchall()
    latest = {}
    for r in rows:
        d = dict(r)
        key = (d["subject_ref"], d["signal_type"])
        # rows arrive oldest-first, so the last write per key wins deterministically
        latest[key] = (d["value"], d["observed_at"])
    return latest


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def evaluate_merchant(merchant_id: str, *, conn=None) -> dict:
    """Evaluate every active rule for a merchant against the latest signals and
    persist the proposed-action projection. Idempotent: replaces the merchant's rows.
    Returns the ranked proposals. Nothing is executed — proposals are suggestions."""
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        raise MerchantAutomationError("merchant_id is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rules = _active_rules(conn, merchant_id)
        latest = _latest_signals(conn, merchant_id)

        # Collect every (rule, subject) whose latest signal satisfies the rule.
        matches = []
        for rule in rules:
            op = _OPS.get(rule["operator"])
            if op is None:
                continue
            try:
                threshold = Decimal(str(rule["threshold"]))
            except (InvalidOperation, ValueError):
                continue
            stype = rule["signal_type"]
            for (subject_ref, sig_type), (val_str, _obs) in latest.items():
                if sig_type != stype:
                    continue
                try:
                    observed = Decimal(str(val_str))
                except (InvalidOperation, ValueError):
                    continue
                if op(observed, threshold):
                    matches.append({
                        "rule_id": rule["rule_id"],
                        "subject_ref": subject_ref,
                        "signal_type": stype,
                        "action_type": rule["action_type"],
                        "operator": rule["operator"],
                        "threshold": _fmt_num(threshold),
                        "observed_value": _fmt_num(observed),
                        "priority": int(rule["priority"]),
                        "name": rule.get("name"),
                    })

        # Deterministic ordering: priority desc, rule_id asc, subject_ref asc.
        matches.sort(key=lambda m: (-m["priority"], m["rule_id"], m["subject_ref"]))

        # Recompute is a replace: clear the prior projection for this merchant.
        conn.execute(
            "DELETE FROM business_os_merchant_proposals WHERE merchant_id = ?",
            (merchant_id,))

        now = _now()
        out = []
        for rank, m in enumerate(matches, start=1):
            reason = (f"{m['signal_type']} {_OP_SYMBOL.get(m['operator'], m['operator'])} "
                      f"{m['threshold']} (observed {m['observed_value']}) -> "
                      f"{m['action_type']}")
            conn.execute(
                "INSERT INTO business_os_merchant_proposals "
                "(proposal_id,merchant_id,rule_id,subject_ref,signal_type,action_type,"
                "operator,threshold,observed_value,priority,rank,reason,computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), merchant_id, m["rule_id"], m["subject_ref"],
                 m["signal_type"], m["action_type"], m["operator"], m["threshold"],
                 m["observed_value"], m["priority"], rank, reason, now))
            out.append({"rule_id": m["rule_id"], "subject_ref": m["subject_ref"],
                        "signal_type": m["signal_type"], "action_type": m["action_type"],
                        "operator": m["operator"], "threshold": m["threshold"],
                        "observed_value": m["observed_value"], "priority": m["priority"],
                        "rank": rank, "reason": reason})
        if owned:
            conn.commit()
        return {"merchant_id": merchant_id, "count": len(out), "proposals": out}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_proposals(merchant_id: str, *, limit: int = 200, conn=None) -> list:
    """Read the stored proposal projection for a merchant, best rank first."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT rule_id,subject_ref,signal_type,action_type,operator,threshold,"
            "observed_value,priority,rank,reason FROM business_os_merchant_proposals "
            "WHERE merchant_id = ? ORDER BY rank ASC LIMIT ?",
            (str(merchant_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_rules(merchant_id: str, *, limit: int = 200, conn=None) -> list:
    """The declared rules for a merchant (active first, then priority desc)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT rule_id,name,signal_type,operator,threshold,action_type,active,"
            "priority,created_at FROM business_os_merchant_rules "
            "WHERE merchant_id = ? ORDER BY active DESC, priority DESC, rule_id ASC "
            "LIMIT ?", (str(merchant_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def current_signals(merchant_id: str, *, limit: int = 500, conn=None) -> list:
    """The current (latest-per-key) signal state for a merchant, deterministically
    ordered by subject then signal_type."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        latest = _latest_signals(conn, merchant_id)
        rows = [{"subject_ref": k[0], "signal_type": k[1], "value": v[0],
                 "observed_at": v[1]} for k, v in latest.items()]
        rows.sort(key=lambda r: (r["subject_ref"], r["signal_type"]))
        return rows[:int(limit)]
    finally:
        if owned:
            conn.close()
