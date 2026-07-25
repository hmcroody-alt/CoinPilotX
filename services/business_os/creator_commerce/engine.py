"""Creator commerce engine — deterministic earnings/tier projection over the
append-only logs (Stage 6).

Records offerings and supporter contributions (idempotently), then computes a
per-creator projection:

  * summed support per supporter (and a global contribution count);
  * a deterministic supporter **tier** by cumulative support, using fixed transparent
    thresholds (``bronze`` >= 0, ``silver`` >= 25, ``gold`` >= 100, ``platinum`` >= 500);
  * a ranked supporter list (top supporters first).

Determinism discipline: no randomness. Supporters are ordered by an explicit tie-break
— total support descending, then ``supporter_id`` ascending — so the output is fully
reproducible. The supporter table is a *projection*: recomputing a creator is
deterministic and idempotent (it replaces that creator's rows, and the UNIQUE
``(creator_id, supporter_id)`` key guarantees exactly-one row per supporter). Report
helpers roll up earnings per offering on the fly from the same log.

Hard boundary — nothing here moves money or takes an action. Earnings are a reporting
quantity summarizing contributions that already happened; a tier is a label, not an
entitlement grant. No payout, no charge, no unlock.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, getcontext
from typing import Any, Optional

from services import db
from services.business_os.creator_commerce import schema as _schema


getcontext().prec = 40

VALID_OFFERING_TYPES = ("membership", "subscription", "tip", "product")

# Fixed, transparent tier thresholds on cumulative support (highest first). A
# supporter is assigned the highest tier whose threshold their total meets.
_TIERS = (("platinum", Decimal("500")),
          ("gold", Decimal("100")),
          ("silver", Decimal("25")),
          ("bronze", Decimal("0")))

_AMOUNT_Q = Decimal("0.01")


class CreatorCommerceError(ValueError):
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


def _to_amount(value: Any, field: str = "amount") -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        raise CreatorCommerceError(f"{field} must be numeric")
    if d < 0:
        raise CreatorCommerceError(f"{field} must be non-negative")
    return d


def _fmt_amount(value: Decimal) -> str:
    return format(value.quantize(_AMOUNT_Q), "f")


def _tier_for(total: Decimal) -> str:
    for name, threshold in _TIERS:
        if total >= threshold:
            return name
    return "bronze"


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
def record_offering(creator_id: str, offering_type: str, *, name: Optional[str] = None,
                    unit_amount: Any = None, currency: str = "USD", active: Any = True,
                    source: str = "manual", external_ref: Optional[str] = None,
                    meta: Any = None, conn=None) -> dict:
    """Declare a support offering. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt)."""
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise CreatorCommerceError("creator_id is required")
    offering_type = str(offering_type or "").strip().lower()
    if offering_type not in VALID_OFFERING_TYPES:
        raise CreatorCommerceError(f"unknown offering_type: {offering_type!r}")
    unit_str = None
    if unit_amount not in (None, ""):
        unit_str = _fmt_amount(_to_amount(unit_amount, "unit_amount"))
    active_i = 1 if (active is True or str(active).strip().lower() in
                     ("1", "true", "yes", "on")) else 0

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT offering_id FROM business_os_creator_offerings "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"offering_id": dup["offering_id"], "recorded": False,
                        "deduped": True}
        offering_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_creator_offerings "
            "(offering_id,creator_id,name,offering_type,unit_amount,currency,active,"
            "source,external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (offering_id, creator_id, name, offering_type, unit_str, currency,
             active_i, source, external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"offering_id": offering_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_contribution(creator_id: str, supporter_id: str, amount: Any, *,
                        offering_id: Optional[str] = None, currency: str = "USD",
                        occurred_at: Any = None, source: str = "manual",
                        external_ref: Optional[str] = None, meta: Any = None,
                        conn=None) -> dict:
    """Append one supporter contribution fact. Idempotent on ``(source,
    external_ref)`` (NULL ref exempt). Records support that already happened — nothing
    is charged here."""
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise CreatorCommerceError("creator_id is required")
    supporter_id = str(supporter_id or "").strip()
    if not supporter_id:
        raise CreatorCommerceError("supporter_id is required")
    amount_d = _to_amount(amount, "amount")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT contribution_id FROM business_os_creator_contributions "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"contribution_id": existing["contribution_id"],
                        "recorded": False, "deduped": True}
        cid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_creator_contributions "
            "(contribution_id,creator_id,offering_id,supporter_id,amount,currency,"
            "occurred_at,source,external_ref,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, creator_id, offering_id, supporter_id, _fmt_amount(amount_d),
             currency, _norm_ts(occurred_at), source, external_ref,
             _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"contribution_id": cid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def compute_creator(creator_id: str, *, conn=None) -> dict:
    """Compute (and persist) the supporter/tier projection for one creator. Idempotent:
    replaces the creator's rows. Returns the ranked supporter list."""
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise CreatorCommerceError("creator_id is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT supporter_id,amount FROM business_os_creator_contributions "
            "WHERE creator_id = ?", (creator_id,)).fetchall()
        totals = {}
        counts = {}
        for r in rows:
            d = dict(r)
            sid = d["supporter_id"]
            try:
                amt = Decimal(str(d["amount"]))
            except (InvalidOperation, ValueError):
                continue
            totals[sid] = totals.get(sid, Decimal(0)) + amt
            counts[sid] = counts.get(sid, 0) + 1

        # Deterministic ordering: total desc, then supporter_id asc.
        ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))

        conn.execute(
            "DELETE FROM business_os_creator_supporters WHERE creator_id = ?",
            (creator_id,))

        now = _now()
        out = []
        for rank, (sid, total) in enumerate(ordered, start=1):
            tier = _tier_for(total)
            conn.execute(
                "INSERT INTO business_os_creator_supporters "
                "(row_id,creator_id,supporter_id,total_amount,contribution_count,tier,"
                "rank,computed_at) VALUES (?,?,?,?,?,?,?,?)",
                (_schema.new_id(), creator_id, sid, _fmt_amount(total),
                 int(counts.get(sid, 0)), tier, rank, now))
            out.append({"supporter_id": sid, "total_amount": _fmt_amount(total),
                        "contribution_count": int(counts.get(sid, 0)), "tier": tier,
                        "rank": rank})
        if owned:
            conn.commit()
        return {"creator_id": creator_id, "count": len(out), "supporters": out}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_supporters(creator_id: str, *, limit: int = 200, conn=None) -> list:
    """Read the stored supporter projection for a creator, best rank first."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT supporter_id,total_amount,contribution_count,tier,rank "
            "FROM business_os_creator_supporters WHERE creator_id = ? "
            "ORDER BY rank ASC LIMIT ?", (str(creator_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_offerings(creator_id: str, *, limit: int = 200, conn=None) -> list:
    """The declared offerings for a creator (active first, then created_at)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT offering_id,name,offering_type,unit_amount,currency,active,"
            "created_at FROM business_os_creator_offerings WHERE creator_id = ? "
            "ORDER BY active DESC, created_at ASC, offering_id ASC LIMIT ?",
            (str(creator_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def earnings_report(creator_id: str, *, conn=None) -> dict:
    """Operator/creator report: total support and a per-offering rollup (computed on
    the fly from the contribution log). Informational only — no payout is implied."""
    creator_id = str(creator_id or "").strip()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT offering_id,amount FROM business_os_creator_contributions "
            "WHERE creator_id = ?", (creator_id,)).fetchall()
        total = Decimal(0)
        per_offering = {}
        for r in rows:
            d = dict(r)
            try:
                amt = Decimal(str(d["amount"]))
            except (InvalidOperation, ValueError):
                continue
            total += amt
            key = d["offering_id"] or "(unassigned)"
            per_offering[key] = per_offering.get(key, Decimal(0)) + amt
        ordered = sorted(per_offering.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"creator_id": creator_id, "total_support": _fmt_amount(total),
                "contribution_count": len(rows),
                "offerings": [{"offering_id": k, "total": _fmt_amount(v)}
                              for k, v in ordered]}
    finally:
        if owned:
            conn.close()
