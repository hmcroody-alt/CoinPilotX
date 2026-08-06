"""Business OS — Store: shipping profiles + return policy (the settings behind
the dashboard's Shipping and Returns cards).

The mission's screenshot finding: the Store dashboard renders Shipping and
Returns tiles that open NOTHING — inert controls over settings that have no
backend at all. This module is that backend, built additively in the Store
package: its own tables, the package's flag (``BUSINESS_OS_STORE``), the S1
RBAC the rest of Store already enforces, and the same append-only
``business_os_store_audit`` trail.

Honest-state contract for the dashboard (mission rule: never fake a zero):

  * :func:`policies_summary` returns ``shipping.configured`` /
    ``returns.configured`` booleans so the tiles can say "Not set up" when
    nothing exists, instead of inventing "0 rules" over an absent feature.
  * A business with no return policy row has NO policy — ``get_return_policy``
    returns ``None`` rather than a fabricated default.

Money-adjacent numbers are integer cents / basis points, validated server-side;
the client never computes a shipping rate.

Rules:
  * writes need ``store.manage`` (manager+), reads need ``store.read``
    (viewer+); no role on the business ⇒ 404, existence not leaked;
  * the first active shipping profile becomes the default automatically;
  * the default profile cannot be archived while it is the default — point the
    default elsewhere first (409), so a live storefront can never silently lose
    its shipping answer;
  * account hold beats every write.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from services import db
from services.business_os.store import service as _svc
from services.business_os.store.service import StoreError


RATE_TYPES = {"flat", "free"}
WHO_PAYS = {"buyer", "seller"}
PROFILE_STATUSES = {"active", "archived"}

NAME_MAX = 120
REGION_MAX = 64
MAX_REGIONS = 100
POLICY_TEXT_MAX = 4000
MAX_WINDOW_DAYS = 365
MAX_DELIVERY_DAYS = 120


# --- schema (additive; idempotent) -------------------------------------------
def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_store_shipping_profiles ("
            " profile_id TEXT PRIMARY KEY,"
            " business_id TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'active',"
            " is_default INTEGER NOT NULL DEFAULT 0,"
            " rate_type TEXT NOT NULL DEFAULT 'flat',"
            " base_rate_cents INTEGER NOT NULL DEFAULT 0,"
            " per_item_rate_cents INTEGER NOT NULL DEFAULT 0,"
            " regions_json TEXT,"
            " min_delivery_days INTEGER,"
            " max_delivery_days INTEGER,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_store_shipping_biz "
            "ON business_os_store_shipping_profiles (business_id, status)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_store_return_policy ("
            " policy_id TEXT PRIMARY KEY,"
            " business_id TEXT NOT NULL UNIQUE,"
            " returns_accepted INTEGER NOT NULL DEFAULT 0,"
            " window_days INTEGER,"
            " restocking_fee_bps INTEGER NOT NULL DEFAULT 0,"
            " return_shipping_paid_by TEXT,"
            " policy_text TEXT,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --- validation --------------------------------------------------------------
def _clean_cents(value: Any, *, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StoreError(f"{field} must be a non-negative integer (cents).",
                         400, "invalid")
    return value


def _clean_days(value: Any, *, field: str, maximum: int) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise StoreError(f"{field} must be an integer between 0 and {maximum}.",
                         400, "invalid")
    return value


def _clean_regions(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_REGIONS:
        raise StoreError(f"regions must be a list of at most {MAX_REGIONS} entries.",
                         400, "invalid")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > REGION_MAX:
            raise StoreError("Each region must be non-empty text.", 400, "invalid")
        out.append(item.strip())
    return json.dumps(out, sort_keys=False)


def _validate_profile_payload(payload: dict, *, for_create: bool) -> dict:
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    out: dict = {}
    if for_create or "name" in payload:
        out["name"] = _svc._clean_str(payload.get("name"), field="name",
                                      max_len=NAME_MAX, required=True)
    if for_create or "rate_type" in payload:
        rate_type = payload.get("rate_type", "flat")
        if rate_type not in RATE_TYPES:
            raise StoreError(f"rate_type must be one of {sorted(RATE_TYPES)}.",
                             400, "invalid")
        out["rate_type"] = rate_type
    if for_create or "base_rate_cents" in payload:
        out["base_rate_cents"] = _clean_cents(payload.get("base_rate_cents"),
                                              field="base_rate_cents")
    if for_create or "per_item_rate_cents" in payload:
        out["per_item_rate_cents"] = _clean_cents(payload.get("per_item_rate_cents"),
                                                  field="per_item_rate_cents")
    if for_create or "regions" in payload:
        out["regions_json"] = _clean_regions(payload.get("regions"))
    if for_create or "min_delivery_days" in payload:
        out["min_delivery_days"] = _clean_days(payload.get("min_delivery_days"),
                                               field="min_delivery_days",
                                               maximum=MAX_DELIVERY_DAYS)
    if for_create or "max_delivery_days" in payload:
        out["max_delivery_days"] = _clean_days(payload.get("max_delivery_days"),
                                               field="max_delivery_days",
                                               maximum=MAX_DELIVERY_DAYS)
    lo = out.get("min_delivery_days")
    hi = out.get("max_delivery_days")
    if lo is not None and hi is not None and lo > hi:
        raise StoreError("min_delivery_days cannot exceed max_delivery_days.",
                         400, "invalid")
    if out.get("rate_type") == "free":
        # A "free" profile that charges is a lie; normalise rather than trust.
        out["base_rate_cents"] = 0
        out["per_item_rate_cents"] = 0
    return out


def _profile_public(row: dict) -> dict:
    regions = None
    if row.get("regions_json"):
        try:
            regions = json.loads(row["regions_json"])
        except Exception:
            regions = None
    return {
        "profile_id": row.get("profile_id"),
        "business_id": row.get("business_id"),
        "name": row.get("name"),
        "status": row.get("status"),
        "is_default": bool(row.get("is_default")),
        "rate_type": row.get("rate_type"),
        "base_rate_cents": row.get("base_rate_cents"),
        "per_item_rate_cents": row.get("per_item_rate_cents"),
        "regions": regions,
        "min_delivery_days": row.get("min_delivery_days"),
        "max_delivery_days": row.get("max_delivery_days"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_profile_row(conn, business_id, profile_id) -> Optional[dict]:
    return _svc._row(conn.execute(
        "SELECT * FROM business_os_store_shipping_profiles "
        "WHERE business_id = ? AND profile_id = ?",
        (_svc._sid(business_id), str(profile_id))).fetchone())


# --- shipping profiles: reads ------------------------------------------------
def list_shipping_profiles(business_id: str, actor_user_id: Any, *,
                           include_archived: bool = False) -> list:
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.read")
        q = ("SELECT * FROM business_os_store_shipping_profiles "
             "WHERE business_id = ?")
        params = [_svc._sid(business_id)]
        if not include_archived:
            q += " AND status = 'active'"
        q += " ORDER BY is_default DESC, created_at"
        return [_profile_public(r) for r in _svc._rows(
            conn.execute(q, tuple(params)).fetchall())]
    finally:
        conn.close()


def get_shipping_profile(business_id: str, actor_user_id: Any,
                         profile_id: str) -> dict:
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.read")
        row = _get_profile_row(conn, business_id, profile_id)
        if row is None:
            raise StoreError("Shipping profile not found.", 404, "not_found")
        return _profile_public(row)
    finally:
        conn.close()


# --- shipping profiles: writes -----------------------------------------------
def create_shipping_profile(business_id: str, actor_user_id: Any, payload: dict,
                            *, context: Optional[dict] = None) -> dict:
    """Create an active profile. The business's FIRST active profile becomes the
    default automatically, so a configured store always has a default answer."""
    _svc._require_enabled()
    _svc._require_not_held(context)
    fields = _validate_profile_payload(payload, for_create=True)
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM business_os_store_shipping_profiles "
            "WHERE business_id = ? AND status = 'active'",
            (_svc._sid(business_id),)).fetchone()
        n_active = existing["c"] if hasattr(existing, "keys") else existing[0]
        pid = "shipp_" + uuid.uuid4().hex
        now = _svc._now_iso()
        conn.execute(
            "INSERT INTO business_os_store_shipping_profiles "
            "(profile_id, business_id, name, status, is_default, rate_type, "
            "base_rate_cents, per_item_rate_cents, regions_json, "
            "min_delivery_days, max_delivery_days, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, _svc._sid(business_id), fields["name"],
             1 if n_active == 0 else 0, fields["rate_type"],
             fields["base_rate_cents"], fields["per_item_rate_cents"],
             fields.get("regions_json"), fields.get("min_delivery_days"),
             fields.get("max_delivery_days"), now, now))
        _svc._audit(conn, business_id=business_id, subject_type="shipping_profile",
                    subject_ref=pid, action="shipping_profile.create",
                    actor=actor_user_id,
                    after={"name": fields["name"], "rate_type": fields["rate_type"],
                           "is_default": n_active == 0})
        conn.commit()
        return _profile_public(_get_profile_row(conn, business_id, pid))
    finally:
        conn.close()


def update_shipping_profile(business_id: str, actor_user_id: Any, profile_id: str,
                            payload: dict, *, context: Optional[dict] = None) -> dict:
    _svc._require_enabled()
    _svc._require_not_held(context)
    fields = _validate_profile_payload(payload, for_create=False)
    if not fields:
        raise StoreError("Nothing to update.", 400, "invalid")
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _get_profile_row(conn, business_id, profile_id)
        if row is None:
            raise StoreError("Shipping profile not found.", 404, "not_found")
        if row.get("status") != "active":
            raise StoreError("Archived profiles cannot be edited.", 409, "archived")
        # Window sanity across the merge of old + new values.
        lo = fields.get("min_delivery_days", row.get("min_delivery_days"))
        hi = fields.get("max_delivery_days", row.get("max_delivery_days"))
        if lo is not None and hi is not None and lo > hi:
            raise StoreError("min_delivery_days cannot exceed max_delivery_days.",
                             400, "invalid")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE business_os_store_shipping_profiles SET {sets}, updated_at = ? "
            "WHERE business_id = ? AND profile_id = ?",
            (*fields.values(), _svc._now_iso(), _svc._sid(business_id), str(profile_id)))
        _svc._audit(conn, business_id=business_id, subject_type="shipping_profile",
                    subject_ref=profile_id, action="shipping_profile.update",
                    actor=actor_user_id,
                    before={k: row.get(k) for k in fields},
                    after=dict(fields))
        conn.commit()
        return _profile_public(_get_profile_row(conn, business_id, profile_id))
    finally:
        conn.close()


def set_default_shipping_profile(business_id: str, actor_user_id: Any,
                                 profile_id: str, *,
                                 context: Optional[dict] = None) -> dict:
    """Atomically point the business's default at an active profile."""
    _svc._require_enabled()
    _svc._require_not_held(context)
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _get_profile_row(conn, business_id, profile_id)
        if row is None:
            raise StoreError("Shipping profile not found.", 404, "not_found")
        if row.get("status") != "active":
            raise StoreError("An archived profile cannot be the default.",
                             409, "archived")
        now = _svc._now_iso()
        conn.execute(
            "UPDATE business_os_store_shipping_profiles SET is_default = 0, "
            "updated_at = ? WHERE business_id = ? AND is_default = 1",
            (now, _svc._sid(business_id)))
        conn.execute(
            "UPDATE business_os_store_shipping_profiles SET is_default = 1, "
            "updated_at = ? WHERE business_id = ? AND profile_id = ?",
            (now, _svc._sid(business_id), str(profile_id)))
        _svc._audit(conn, business_id=business_id, subject_type="shipping_profile",
                    subject_ref=profile_id, action="shipping_profile.set_default",
                    actor=actor_user_id)
        conn.commit()
        return _profile_public(_get_profile_row(conn, business_id, profile_id))
    finally:
        conn.close()


def archive_shipping_profile(business_id: str, actor_user_id: Any, profile_id: str,
                             *, context: Optional[dict] = None) -> dict:
    """Archive a profile. The DEFAULT cannot be archived — point the default at
    another profile first, so a configured store never silently loses its
    shipping answer."""
    _svc._require_enabled()
    _svc._require_not_held(context)
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _get_profile_row(conn, business_id, profile_id)
        if row is None:
            raise StoreError("Shipping profile not found.", 404, "not_found")
        if row.get("status") == "archived":
            return _profile_public(row)  # idempotent
        if int(row.get("is_default") or 0):
            raise StoreError(
                "This is the default shipping profile. Set another profile as "
                "default before archiving it.", 409, "default_profile")
        conn.execute(
            "UPDATE business_os_store_shipping_profiles SET status = 'archived', "
            "updated_at = ? WHERE business_id = ? AND profile_id = ?",
            (_svc._now_iso(), _svc._sid(business_id), str(profile_id)))
        _svc._audit(conn, business_id=business_id, subject_type="shipping_profile",
                    subject_ref=profile_id, action="shipping_profile.archive",
                    actor=actor_user_id, before={"status": "active"},
                    after={"status": "archived"})
        conn.commit()
        return _profile_public(_get_profile_row(conn, business_id, profile_id))
    finally:
        conn.close()


# --- return policy -----------------------------------------------------------
def _policy_public(row: dict) -> dict:
    return {
        "policy_id": row.get("policy_id"),
        "business_id": row.get("business_id"),
        "returns_accepted": bool(row.get("returns_accepted")),
        "window_days": row.get("window_days"),
        "restocking_fee_bps": row.get("restocking_fee_bps"),
        "return_shipping_paid_by": row.get("return_shipping_paid_by"),
        "policy_text": row.get("policy_text"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def get_return_policy(business_id: str, actor_user_id: Any) -> Optional[dict]:
    """None when no policy has ever been set — the dashboard says "Not set up",
    it does not invent a default."""
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.read")
        row = _svc._row(conn.execute(
            "SELECT * FROM business_os_store_return_policy WHERE business_id = ?",
            (_svc._sid(business_id),)).fetchone())
        return None if row is None else _policy_public(row)
    finally:
        conn.close()


def upsert_return_policy(business_id: str, actor_user_id: Any, payload: dict,
                         *, context: Optional[dict] = None) -> dict:
    _svc._require_enabled()
    _svc._require_not_held(context)
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    accepted = payload.get("returns_accepted")
    if not isinstance(accepted, bool):
        raise StoreError("returns_accepted must be true or false.", 400, "invalid")
    window = _clean_days(payload.get("window_days"), field="window_days",
                         maximum=MAX_WINDOW_DAYS)
    fee_bps = payload.get("restocking_fee_bps", 0)
    if isinstance(fee_bps, bool) or not isinstance(fee_bps, int) \
            or fee_bps < 0 or fee_bps > 10000:
        raise StoreError("restocking_fee_bps must be 0..10000.", 400, "invalid")
    paid_by = payload.get("return_shipping_paid_by")
    if paid_by is not None and paid_by not in WHO_PAYS:
        raise StoreError(f"return_shipping_paid_by must be one of {sorted(WHO_PAYS)}.",
                         400, "invalid")
    text = _svc._clean_str(payload.get("policy_text"), field="policy_text",
                           max_len=POLICY_TEXT_MAX)
    if accepted and window is None:
        raise StoreError("window_days is required when returns are accepted.",
                         400, "invalid")
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _svc._row(conn.execute(
            "SELECT * FROM business_os_store_return_policy WHERE business_id = ?",
            (_svc._sid(business_id),)).fetchone())
        now = _svc._now_iso()
        after = {"returns_accepted": accepted, "window_days": window,
                 "restocking_fee_bps": fee_bps,
                 "return_shipping_paid_by": paid_by}
        if row is None:
            pid = "retpol_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO business_os_store_return_policy "
                "(policy_id, business_id, returns_accepted, window_days, "
                "restocking_fee_bps, return_shipping_paid_by, policy_text, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, _svc._sid(business_id), 1 if accepted else 0, window,
                 fee_bps, paid_by, text, now, now))
            _svc._audit(conn, business_id=business_id, subject_type="return_policy",
                        subject_ref=pid, action="return_policy.create",
                        actor=actor_user_id, after=after)
        else:
            conn.execute(
                "UPDATE business_os_store_return_policy SET returns_accepted = ?, "
                "window_days = ?, restocking_fee_bps = ?, "
                "return_shipping_paid_by = ?, policy_text = ?, updated_at = ? "
                "WHERE business_id = ?",
                (1 if accepted else 0, window, fee_bps, paid_by, text, now,
                 _svc._sid(business_id)))
            _svc._audit(conn, business_id=business_id, subject_type="return_policy",
                        subject_ref=row.get("policy_id"),
                        action="return_policy.update", actor=actor_user_id,
                        before={"returns_accepted": bool(row.get("returns_accepted")),
                                "window_days": row.get("window_days"),
                                "restocking_fee_bps": row.get("restocking_fee_bps"),
                                "return_shipping_paid_by": row.get("return_shipping_paid_by")},
                        after=after)
        conn.commit()
        out = _svc._row(conn.execute(
            "SELECT * FROM business_os_store_return_policy WHERE business_id = ?",
            (_svc._sid(business_id),)).fetchone())
        return _policy_public(out)
    finally:
        conn.close()


# --- dashboard projection ----------------------------------------------------
def policies_summary(business_id: str, actor_user_id: Any) -> dict:
    """What the Store dashboard's Shipping and Returns tiles render. `configured`
    false means the tile honestly says "Not set up" and opens the setup flow —
    never a fabricated zero-state."""
    _svc._require_enabled()
    conn = db.connect()
    try:
        _svc._require_biz_permission(conn, business_id, actor_user_id, "store.read")
        profs = _svc._rows(conn.execute(
            "SELECT name, is_default FROM business_os_store_shipping_profiles "
            "WHERE business_id = ? AND status = 'active' "
            "ORDER BY is_default DESC, created_at",
            (_svc._sid(business_id),)).fetchall())
        default_name = next(
            (p["name"] for p in profs if p.get("is_default")), None)
        pol = _svc._row(conn.execute(
            "SELECT returns_accepted, window_days FROM "
            "business_os_store_return_policy WHERE business_id = ?",
            (_svc._sid(business_id),)).fetchone())
        return {
            "shipping": {
                "configured": len(profs) > 0,
                "profile_count": len(profs),
                "default_profile_name": default_name,
            },
            "returns": {
                "configured": pol is not None,
                "returns_accepted": None if pol is None else bool(pol.get("returns_accepted")),
                "window_days": None if pol is None else pol.get("window_days"),
            },
        }
    finally:
        conn.close()
