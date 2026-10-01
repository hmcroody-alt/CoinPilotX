"""Business OS — Marketplace LISTING DRAFTS: the add-listing engine's backend.

Phase 2 of the Store OS plan: the multi-step composer (identity → media →
offer → fulfillment → inventory → compliance) needs server-side draft
persistence so a seller can leave mid-flow and resume, and so publish is a
single validated server action instead of a client-assembled product row.

Design points (mirroring the sibling engines):

  * a draft is a SCRATCHPAD — it owns no live listing state and is invisible
    to buyers; publishing routes through the ONE existing catalog engine
    (``service.create_product`` + ``transition_product('publish')``), so every
    catalog invariant (seller approval, validation, audit) applies unchanged;
  * sections are updated independently with per-section field allowlists
    (unknown fields rejected loudly, 400 ``unknown_field``);
  * ``completeness`` is an HONEST server-computed checklist — the client
    renders exactly what the server says is missing, never its own guess;
  * publish on an incomplete draft is a 409 ``incomplete`` naming the missing
    requirements; publishing twice is a 409 ``already_published``;
  * scoped reads: a foreign seller's draft answers 404 (existence not leaked);
  * account-hold gate on every mutation; audit rows on create/publish/discard.

Flag-gated by ``BUSINESS_OS_MARKETPLACE``. Additive only: one new table, no
edits to the catalog engine.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError


DRAFT_STATUSES = {"in_progress", "published", "discarded"}

# Section name -> allowed fields. The composer writes one section at a time.
SECTION_FIELDS = {
    "identity": {"title", "description", "category"},
    "media": {"items"},                      # list of media refs (R2 ids/urls)
    "offer": {"price_cents", "currency"},
    "fulfillment": {"fulfillment_type"},
    "inventory": {"inventory_qty"},
    "attributes": {"attributes"},            # free-form dict, size-capped
    "compliance": {"acknowledged"},
}

MEDIA_MAX_ITEMS = 12
ATTRIBUTES_MAX_JSON = 4000

# Requirements for publish: (section, field, human label).
PUBLISH_REQUIREMENTS = (
    ("identity", "title", "identity.title"),
    ("media", "items", "media.items"),
    ("offer", "price_cents", "offer.price_cents"),
    ("fulfillment", "fulfillment_type", "fulfillment.fulfillment_type"),
    ("compliance", "acknowledged", "compliance.acknowledged"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_listing_drafts ("
            "draft_id TEXT PRIMARY KEY, "
            "seller_user_id TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'in_progress', "
            "sections TEXT NOT NULL DEFAULT '{}', "
            "published_product_id TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_busos_drafts_seller "
            "ON business_os_mkt_listing_drafts (seller_user_id, status)")
        conn.commit()
    finally:
        conn.close()


# --- internal ----------------------------------------------------------------
def _get_scoped(conn, draft_id: Any, seller_user_id: Any) -> dict:
    row = _row(conn.execute(
        "SELECT * FROM business_os_mkt_listing_drafts WHERE draft_id = ?",
        (str(draft_id),)).fetchone())
    if row is None or row["seller_user_id"] != _svc._sid(seller_user_id):
        raise MarketplaceError("Draft not found.", 404, "not_found")
    row["sections"] = json.loads(row.get("sections") or "{}")
    return row


def _validate_section(section: str, fields: dict) -> None:
    """Early, per-write validation so the composer surfaces errors at the step
    where the seller can fix them — publish re-checks everything anyway."""
    if section == "identity":
        title = fields.get("title")
        if title is not None:
            if not str(title).strip():
                raise MarketplaceError("title is required.", 400, "title_required")
            if len(str(title)) > _svc.TITLE_MAX:
                raise MarketplaceError("title is too long.", 400, "title_too_long")
        desc = fields.get("description")
        if desc is not None and len(str(desc)) > _svc.DESC_MAX:
            raise MarketplaceError("description is too long.", 400,
                                   "description_too_long")
    elif section == "media":
        items = fields.get("items")
        if items is not None:
            if not isinstance(items, list) or \
                    any(not isinstance(i, str) or not i.strip() for i in items):
                raise MarketplaceError("media items must be a list of refs.",
                                       400, "invalid_media")
            if len(items) > MEDIA_MAX_ITEMS:
                raise MarketplaceError("too many media items.", 400,
                                       "too_many_media")
    elif section == "offer":
        price = fields.get("price_cents")
        if price is not None:
            if isinstance(price, bool) or not isinstance(price, int) or \
                    price < 0 or price > _svc.PRICE_MAX_CENTS:
                raise MarketplaceError("price_cents out of range.", 400,
                                       "invalid_price")
    elif section == "fulfillment":
        ft = fields.get("fulfillment_type")
        if ft is not None and ft not in _svc.FULFILLMENT_TYPES:
            raise MarketplaceError(
                "fulfillment_type must be 'physical' or 'digital'.", 400,
                "invalid_fulfillment")
    elif section == "inventory":
        qty = fields.get("inventory_qty")
        if qty is not None:
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
                raise MarketplaceError("inventory_qty must be a non-negative "
                                       "integer.", 400, "invalid_inventory")
    elif section == "attributes":
        attrs = fields.get("attributes")
        if attrs is not None:
            if not isinstance(attrs, dict):
                raise MarketplaceError("attributes must be an object.", 400,
                                       "invalid_attributes")
            if len(json.dumps(attrs)) > ATTRIBUTES_MAX_JSON:
                raise MarketplaceError("attributes too large.", 400,
                                       "attributes_too_large")
    elif section == "compliance":
        ack = fields.get("acknowledged")
        if ack is not None and not isinstance(ack, bool):
            raise MarketplaceError("acknowledged must be a boolean.", 400,
                                   "invalid_compliance")


def _completeness(sections: dict) -> dict:
    """Honest checklist: which publish requirements are satisfied, which are
    missing. The client renders this verbatim — no client-side guessing."""
    missing = []
    for sec, field, label in PUBLISH_REQUIREMENTS:
        val = (sections.get(sec) or {}).get(field)
        satisfied = val is not None and val != "" and val != [] and val is not False
        if not satisfied:
            missing.append(label)
    # Conditional: the catalog engine refuses to publish a PHYSICAL product
    # with no inventory, so the checklist must say so up front rather than
    # claiming ready and letting publish fail.
    ful = (sections.get("fulfillment") or {}).get("fulfillment_type")
    if ful == "physical" and \
            (sections.get("inventory") or {}).get("inventory_qty") is None:
        missing.append("inventory.inventory_qty")
    return {"ready": not missing, "missing": missing}


def _present(row: dict) -> dict:
    out = dict(row)
    out["completeness"] = _completeness(row["sections"])
    return out


# --- verbs -------------------------------------------------------------------
def create_draft(seller_user_id: Any, *, context: Optional[dict] = None,
                 conn=None) -> dict:
    """Start an empty draft. Requires an approved, un-held seller — eligibility
    is checked FIRST (spec: fail fast before the seller invests in the flow)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        _svc.require_active_seller(seller_user_id, context, conn=conn)
        did = "mkdraft_" + uuid.uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_listing_drafts "
            "(draft_id, seller_user_id, status, sections, created_at, updated_at) "
            "VALUES (?, ?, 'in_progress', '{}', ?, ?)",
            (did, _svc._sid(seller_user_id), now, now))
        _svc._audit(conn, subject_type="listing_draft", subject_ref=did,
                    action="draft_create", actor=seller_user_id,
                    after={"status": "in_progress"})
        if owned:
            conn.commit()
        return get_draft(did, seller_user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def get_draft(draft_id: Any, seller_user_id: Any, conn=None) -> dict:
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return _present(_get_scoped(conn, draft_id, seller_user_id))
    finally:
        if owned:
            conn.close()


def list_drafts(seller_user_id: Any, *, status: str = "in_progress",
                limit: int = 100, conn=None) -> list:
    _svc._require_enabled()
    if status not in DRAFT_STATUSES:
        raise MarketplaceError("Invalid status filter.", 400, "invalid_status")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_listing_drafts "
            "WHERE seller_user_id = ? AND status = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (_svc._sid(seller_user_id), status, int(limit))).fetchall()]
        for r in rows:
            r["sections"] = json.loads(r.get("sections") or "{}")
        return [_present(r) for r in rows]
    finally:
        if owned:
            conn.close()


def update_section(draft_id: Any, seller_user_id: Any, section: str,
                   fields: dict, *, context: Optional[dict] = None,
                   conn=None) -> dict:
    """Merge one section's fields into the draft. Unknown sections/fields are
    rejected loudly; per-field validation runs at write time."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    if section not in SECTION_FIELDS:
        raise MarketplaceError(f"Unknown section '{section}'.", 400,
                               "invalid_section")
    if not isinstance(fields, dict) or not fields:
        raise MarketplaceError("Section fields required.", 400, "bad_body")
    unknown = set(fields) - SECTION_FIELDS[section]
    if unknown:
        raise MarketplaceError(f"Unknown field(s): {sorted(unknown)}.", 400,
                               "unknown_field")
    _validate_section(section, fields)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_scoped(conn, draft_id, seller_user_id)
        if row["status"] != "in_progress":
            raise MarketplaceError("Draft is no longer editable.", 409,
                                   "draft_not_editable")
        sections = row["sections"]
        merged = dict(sections.get(section) or {})
        merged.update(fields)
        sections[section] = merged
        conn.execute(
            "UPDATE business_os_mkt_listing_drafts "
            "SET sections = ?, updated_at = ? WHERE draft_id = ?",
            (json.dumps(sections), _now_iso(), row["draft_id"]))
        if owned:
            conn.commit()
        return get_draft(draft_id, seller_user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def discard_draft(draft_id: Any, seller_user_id: Any, *,
                  context: Optional[dict] = None, conn=None) -> dict:
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_scoped(conn, draft_id, seller_user_id)
        if row["status"] != "in_progress":
            raise MarketplaceError("Draft is no longer editable.", 409,
                                   "draft_not_editable")
        conn.execute(
            "UPDATE business_os_mkt_listing_drafts "
            "SET status = 'discarded', updated_at = ? WHERE draft_id = ?",
            (_now_iso(), row["draft_id"]))
        _svc._audit(conn, subject_type="listing_draft",
                    subject_ref=row["draft_id"], action="draft_discard",
                    actor=seller_user_id,
                    before={"status": "in_progress"},
                    after={"status": "discarded"})
        if owned:
            conn.commit()
        return get_draft(draft_id, seller_user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def publish_draft(draft_id: Any, seller_user_id: Any, *, publish: bool = True,
                  context: Optional[dict] = None, conn=None) -> dict:
    """Validate the whole draft, create the real product through the ONE
    catalog engine, and (by default) publish it live. ``publish=False`` stops
    at a draft product (the composer's "save as listing draft" exit).

    Incomplete drafts get a 409 naming every missing requirement. A draft
    publishes at most once."""
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _get_scoped(conn, draft_id, seller_user_id)
        if row["status"] == "published":
            raise MarketplaceError("Draft already published.", 409,
                                   "already_published")
        if row["status"] != "in_progress":
            raise MarketplaceError("Draft is no longer editable.", 409,
                                   "draft_not_editable")
        sections = row["sections"]
        check = _completeness(sections)
        if not check["ready"]:
            raise MarketplaceError(
                f"Draft incomplete: missing {check['missing']}.", 409,
                "incomplete")
        for sec in SECTION_FIELDS:
            _validate_section(sec, sections.get(sec) or {})

        ident = sections.get("identity") or {}
        offer = sections.get("offer") or {}
        ful = sections.get("fulfillment") or {}
        inv = sections.get("inventory") or {}
        product = _svc.create_product(
            seller_user_id,
            title=ident.get("title"),
            description=ident.get("description"),
            price_cents=offer.get("price_cents"),
            currency=offer.get("currency") or "usd",
            fulfillment_type=ful.get("fulfillment_type"),
            inventory_qty=inv.get("inventory_qty"),
            context=context, conn=conn)
        if publish:
            product = _svc.transition_product(
                seller_user_id, product["product_id"], "publish",
                context=context, conn=conn)
        conn.execute(
            "UPDATE business_os_mkt_listing_drafts "
            "SET status = 'published', published_product_id = ?, updated_at = ? "
            "WHERE draft_id = ?",
            (product["product_id"], _now_iso(), row["draft_id"]))
        _svc._audit(conn, subject_type="listing_draft",
                    subject_ref=row["draft_id"], action="draft_publish",
                    actor=seller_user_id,
                    before={"status": "in_progress"},
                    after={"status": "published",
                           "product_id": product["product_id"],
                           "published_live": bool(publish)})
        if owned:
            conn.commit()
        out = get_draft(draft_id, seller_user_id, conn=conn)
        out["product"] = product
        return out
    finally:
        if owned:
            conn.close()
