"""Business OS — Marketplace LISTING DRAFTS: framework-agnostic HTTP controller.

Same contract as the sibling controllers: bot.py passes an already-
authenticated identity + parsed input; every handler returns
``(int status_code, dict body)`` with an ``ok`` bool; DARK (404) when
``BUSINESS_OS_MARKETPLACE`` is off; only curated ``MarketplaceError``
messages surface. Section/field allowlisting lives in the ENGINE here (it is
the single validation point the composer steps depend on), so the controller
just relays.

Intended mount (when bot.py is quiet enough to touch):

    POST  /api/business-os/marketplace/listing-drafts                    -> create
    GET   /api/business-os/marketplace/listing-drafts                    -> list_own
    GET   /api/business-os/marketplace/listing-drafts/<id>               -> get
    PATCH /api/business-os/marketplace/listing-drafts/<id>/<section>     -> update_section
    POST  /api/business-os/marketplace/listing-drafts/<id>/publish       -> publish
    POST  /api/business-os/marketplace/listing-drafts/<id>/discard       -> discard

Route-pack init must call ``listing_drafts.ensure_schema()`` once.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.marketplace import listing_drafts as drafts
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


PUBLISH_FIELDS = {"publish"}


def _dark():
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


# --- handlers ----------------------------------------------------------------
def create(seller_user_id: Any, *, context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        d = drafts.create_draft(seller_user_id, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "draft": d})


def get(seller_user_id: Any, draft_id: str):
    if not mkt.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True, "draft": drafts.get_draft(draft_id,
                                                            seller_user_id)})
    except MarketplaceError as exc:
        return _err(exc)


def list_own(seller_user_id: Any, *, status: str = "in_progress",
             limit: int = 100):
    if not mkt.is_enabled():
        return _dark()
    try:
        rows = drafts.list_drafts(seller_user_id, status=status, limit=limit)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "drafts": rows})


def update_section(seller_user_id: Any, draft_id: str, section: str,
                   payload: Any = None, *, context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        if not isinstance(payload, dict):
            raise MarketplaceError("Invalid request body.", 400, "bad_body")
        d = drafts.update_section(draft_id, seller_user_id, section, payload,
                                  context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "draft": d})


def publish(seller_user_id: Any, draft_id: str, payload: Any = None, *,
            context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        payload = payload or {}
        if not isinstance(payload, dict):
            raise MarketplaceError("Invalid request body.", 400, "bad_body")
        unknown = set(payload) - PUBLISH_FIELDS
        if unknown:
            raise MarketplaceError(f"Unknown field(s): {sorted(unknown)}.",
                                   400, "unknown_field")
        d = drafts.publish_draft(draft_id, seller_user_id,
                                 publish=bool(payload.get("publish", True)),
                                 context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "draft": d, "product": d.get("product")})


def discard(seller_user_id: Any, draft_id: str, *,
            context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        d = drafts.discard_draft(draft_id, seller_user_id, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "draft": d})
