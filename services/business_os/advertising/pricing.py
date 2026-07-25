"""Business OS — Advertising: versioned, server-authoritative pricing policy.

The billing service must never read a price from client input and must never
carry a hardcoded production price in code. This module is the ONLY source of the
unit price applied to a billing event. Prices live in the additive
``business_os_ad_pricing_policy`` table, one immutable row per
``(billing_model, currency, effective_version)``. The *active* price for a
``(billing_model, currency)`` is the ``active=1`` row with the highest
``effective_version``; publishing a new price is an additive insert of the next
version, so a past billing event's price stays reproducible from its recorded
``pricing_policy_version``.

Two MVP billing models (spec §2):

  * ``cpm`` — ``unit_price_cents`` = cents per 1000 accepted impressions.
  * ``cpc`` — ``unit_price_cents`` = cents per accepted click.

Validation (spec §2, §16): a published price must be a non-negative integer number
of cents within the configured ``[min_price_cents, max_price_cents]`` guard band
(when set). There is no default price baked in: an environment with no published
policy for a model raises ``AdvertisingError`` (``no_pricing_policy``) so billing
fails closed rather than inventing a number.

Nothing here moves money, reads escrow, or touches the ledger.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising.service import AdvertisingError


BILLING_MODELS = {"cpm", "cpc"}
SUPPORTED_CURRENCIES = {"usd"}


def _norm_model(billing_model: Any) -> str:
    m = str(billing_model or "").strip().lower()
    if m not in BILLING_MODELS:
        raise AdvertisingError(
            f"Unknown billing model: {billing_model!r}.", 400, "bad_billing_model")
    return m


def _norm_currency(currency: Any) -> str:
    c = str(currency or "").strip().lower()
    if c not in SUPPORTED_CURRENCIES:
        raise AdvertisingError(
            f"Unsupported currency: {currency!r}.", 400, "bad_currency")
    return c


def _norm_price(unit_price_cents: Any) -> int:
    if isinstance(unit_price_cents, bool):
        raise AdvertisingError("Price must be an integer number of cents.",
                               400, "bad_price")
    if isinstance(unit_price_cents, int):
        n = unit_price_cents
    elif isinstance(unit_price_cents, str) and \
            unit_price_cents.strip().lstrip("-").isdigit():
        n = int(unit_price_cents.strip())
    else:
        raise AdvertisingError("Price must be an integer number of cents.",
                               400, "bad_price")
    if n < 0:
        raise AdvertisingError("Price cannot be negative.", 400, "bad_price")
    return n


def get_active_policy(billing_model: Any, currency: Any = "usd",
                      *, conn=None) -> dict:
    """Return the active pricing policy row for a model/currency, or raise.

    The active price is the ``active=1`` row with the highest ``effective_version``.
    Raises ``AdvertisingError`` (``no_pricing_policy``, 409) when none is published
    so billing fails closed. Read-only; no flag requirement (billing enforces it).
    """
    model = _norm_model(billing_model)
    cur = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _svc._row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_pricing_policy "
            "WHERE billing_model = ? AND currency = ? AND active = 1 "
            "ORDER BY effective_version DESC LIMIT 1",
            (model, cur)).fetchone())
        if row is None:
            raise AdvertisingError(
                f"No active pricing policy for {model}/{cur}.",
                409, "no_pricing_policy")
        return row
    finally:
        if owned:
            conn.close()


def get_active_policy_or_none(billing_model: Any, currency: Any = "usd",
                              *, conn=None) -> Optional[dict]:
    """Non-raising variant used by reporting/reconciliation surfaces."""
    try:
        return get_active_policy(billing_model, currency, conn=conn)
    except AdvertisingError:
        return None


def publish_policy(billing_model: Any, currency: Any, unit_price_cents: Any, *,
                   actor: Any, min_price_cents: Any = None,
                   max_price_cents: Any = None, note: Optional[str] = None,
                   conn=None) -> dict:
    """Publish the NEXT version of a price for a model/currency (admin, additive).

    Trusted caller — the bot.py admin route enforces owner/admin RBAC first. The
    new row is inserted at ``max(effective_version)+1`` and marked active; prior
    versions are left intact (retained, not mutated) but deactivated so exactly one
    active price exists per model/currency. Enforces the guard band: the price must
    sit within ``[min, max]`` when those bounds are supplied.
    """
    _svc._require_enabled()
    model = _norm_model(billing_model)
    cur = _norm_currency(currency)
    price = _norm_price(unit_price_cents)
    lo = None if min_price_cents is None else _norm_price(min_price_cents)
    hi = None if max_price_cents is None else _norm_price(max_price_cents)
    if lo is not None and price < lo:
        raise AdvertisingError(
            f"Price {price} below minimum {lo}.", 400, "price_below_min")
    if hi is not None and price > hi:
        raise AdvertisingError(
            f"Price {price} above maximum {hi}.", 400, "price_above_max")
    if lo is not None and hi is not None and lo > hi:
        raise AdvertisingError(
            "min_price_cents cannot exceed max_price_cents.", 400, "bad_bounds")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        _svc._begin(conn)
        row = conn.execute(
            "SELECT COALESCE(MAX(effective_version), 0) AS v "
            "FROM business_os_ad_pricing_policy "
            "WHERE billing_model = ? AND currency = ?", (model, cur)).fetchone()
        next_version = int((row["v"] if hasattr(row, "keys") else row[0]) or 0) + 1
        # Deactivate all prior versions so exactly one active price remains.
        conn.execute(
            "UPDATE business_os_ad_pricing_policy SET active = 0 "
            "WHERE billing_model = ? AND currency = ?", (model, cur))
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_pricing_policy "
            "(billing_model, currency, unit_price_cents, effective_version, "
            "min_price_cents, max_price_cents, active, created_by, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (model, cur, price, next_version, lo, hi,
             None if actor is None else str(actor), note, now))
        _svc._audit(conn, campaign_id=None, advertiser_user_id=None,
                    action="ad_pricing_publish", actor=actor,
                    after={"billing_model": model, "currency": cur,
                           "unit_price_cents": price,
                           "effective_version": next_version})
        _svc._commit(conn)
        return get_active_policy(model, cur, conn=conn)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def list_policies(*, billing_model: Optional[str] = None,
                  currency: Optional[str] = None, active_only: bool = False,
                  conn=None) -> list:
    """Admin: list published pricing policy versions (newest first)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        clauses, params = [], []
        if billing_model is not None:
            clauses.append("billing_model = ?"); params.append(_norm_model(billing_model))
        if currency is not None:
            clauses.append("currency = ?"); params.append(_norm_currency(currency))
        if active_only:
            clauses.append("active = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = conn.execute(
            "SELECT * FROM business_os_ad_pricing_policy" + where +
            " ORDER BY billing_model, currency, effective_version DESC",
            tuple(params))
        return [_svc._row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def public_policy(row: dict) -> dict:
    """Client-safe projection of a pricing policy row."""
    if row is None:
        return None
    return {
        "billing_model": row.get("billing_model"),
        "currency": row.get("currency"),
        "unit_price_cents": row.get("unit_price_cents"),
        "effective_version": row.get("effective_version"),
        "active": bool(row.get("active")),
    }
