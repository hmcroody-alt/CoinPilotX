"""Business OS — COMMERCE GATEWAY: one framework-agnostic route table that
exposes the eight mount-ready canonical controller packs as HTTP endpoints.

This module contains NO Flask. It is the single source of truth for:

  * which commerce endpoints exist (``ROUTES``),
  * what auth each needs (``public`` / ``user`` / ``admin``),
  * how HTTP inputs (path params, query string, JSON body, session user)
    map onto each controller's Python signature.

The thin Flask adapter (``services/business_os_commerce_routes.py``) iterates
``ROUTES`` and registers one view per entry; it contains no business logic.
Because every controller already goes DARK (404) when its feature flag is off,
the whole surface is safe to mount at any time.

Controller packs wired here (all ``(status, body)``-tuple controllers):

  marketplace: offers_api, returns_api, inventory_api, listing_drafts_api,
               seller_dashboard_api, reports_api
  store:       policies_api, versions_api

Testable standalone (no Flask, no HTTP):

    python tests/business_os/test_commerce_gateway.py
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from services.business_os.marketplace import inventory as _inv_engine
from services.business_os.marketplace import inventory_api as inv
from services.business_os.marketplace import listing_drafts as _drafts_engine
from services.business_os.marketplace import listing_drafts_api as drafts
from services.business_os.marketplace import offers as _offers_engine
from services.business_os.marketplace import offers_api as offers
from services.business_os.marketplace import reports_api as reports
from services.business_os.marketplace import returns as _returns_engine
from services.business_os.marketplace import returns_api as returns
from services.business_os.marketplace import schema as _mkt_schema
from services.business_os.marketplace import seller_dashboard_api as dash
from services.business_os.store import policies as _policies_engine
from services.business_os.store import policies_api as pol
from services.business_os.store import schema as _store_schema
from services.business_os.store import versions as _versions_engine
from services.business_os.store import versions_api as versions


API_PREFIX = "/api/business-os"

Handler = Callable[..., Tuple[int, dict]]

_schemas_ready = False


def ensure_schemas() -> None:
    """Idempotent. Called once by the route pack at registration time so a
    fresh database has every table the controllers touch."""
    global _schemas_ready
    if _schemas_ready:
        return
    _mkt_schema.ensure_schema()
    _store_schema.ensure_schema()
    _offers_engine.ensure_schema()
    _returns_engine.ensure_schema()
    _inv_engine.ensure_schema()
    _drafts_engine.ensure_schema()
    _policies_engine.ensure_schema()
    _versions_engine.ensure_schema()
    _schemas_ready = True


def context_from_user(user: Optional[dict]) -> dict:
    """Map a bot.py session-user row onto the ``context`` dict every engine's
    ``_require_not_held`` expects. Missing fields default to an ACTIVE account
    (bot.py already refuses login to restricted accounts)."""
    user = user or {}
    try:
        access = int(user.get("access_enabled", 1))
    except (TypeError, ValueError):
        access = 1
    return {
        "account_status": (user.get("account_status") or "active"),
        "access_enabled": access,
    }


# --- query-string coercion ---------------------------------------------------
def _qint(query: dict, key: str, default: Optional[int] = None) -> Optional[int]:
    raw = (query or {}).get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _qstr(query: dict, key: str, default: Optional[str] = None) -> Optional[str]:
    raw = (query or {}).get(key)
    if raw is None or raw == "":
        return default
    return str(raw)


def _qbool(query: dict, key: str) -> bool:
    return str((query or {}).get(key, "")).strip().lower() in (
        "1", "true", "yes", "on")


# --- adapters: (actor, path, query, body, context) -> (status, body) ---------
# Marketplace: the acting user IS the buyer/seller; no business_id in the path.

def _offers_create(a, p, q, b, c):
    return offers.create_offer(a, b, context=c)


def _offers_list(a, p, q, b, c):
    return offers.list_own_offers(a, role=_qstr(q, "role", "buyer"),
                                  status=_qstr(q, "status"))


def _offers_get(a, p, q, b, c):
    return offers.get_offer(a, p["offer_id"])


def _offers_act(a, p, q, b, c):
    return offers.act_on_offer(a, p["offer_id"], p["action"], b, context=c)


def _offers_expire(a, p, q, b, c):
    return offers.run_expiry_sweep(a)


def _returns_create(a, p, q, b, c):
    return returns.create_return(a, b, context=c)


def _returns_list(a, p, q, b, c):
    return returns.list_own_returns(a, role=_qstr(q, "role", "buyer"),
                                    status=_qstr(q, "status"))


def _returns_get(a, p, q, b, c):
    return returns.get_return(a, p["return_id"])


def _returns_act(a, p, q, b, c):
    return returns.act_on_return(a, p["return_id"], p["action"], b, context=c)


def _inventory_overview(a, p, q, b, c):
    return inv.get_overview(a, low_stock_threshold=_qint(q, "low_stock_threshold"))


def _inventory_adjustments(a, p, q, b, c):
    return inv.list_adjustments(a, product_id=_qstr(q, "product_id"),
                                limit=_qint(q, "limit", 200))


def _inventory_adjust(a, p, q, b, c):
    return inv.adjust(a, p["product_id"], b, context=c)


def _drafts_create(a, p, q, b, c):
    return drafts.create(a, context=c)


def _drafts_list(a, p, q, b, c):
    return drafts.list_own(a, status=_qstr(q, "status", "in_progress"),
                           limit=_qint(q, "limit", 50))


def _drafts_get(a, p, q, b, c):
    return drafts.get(a, p["draft_id"])


def _drafts_update(a, p, q, b, c):
    return drafts.update_section(a, p["draft_id"], p["section"], b, context=c)


def _drafts_publish(a, p, q, b, c):
    return drafts.publish(a, p["draft_id"], b, context=c)


def _drafts_discard(a, p, q, b, c):
    return drafts.discard(a, p["draft_id"], context=c)


def _dash_action_center(a, p, q, b, c):
    return dash.get_action_center(a)


def _dash_sales_summary(a, p, q, b, c):
    return dash.get_sales_summary(a, currency=_qstr(q, "currency", "usd"))


def _reports_finance(a, p, q, b, c):
    return reports.get_finance(a, currency=_qstr(q, "currency", "usd"))


def _reports_sales_by_day(a, p, q, b, c):
    return reports.get_sales_by_day(a, currency=_qstr(q, "currency", "usd"),
                                    start_day=_qstr(q, "start_day"),
                                    end_day=_qstr(q, "end_day"))


# Store: business-scoped; RBAC happens inside the engines.

def _pol_summary(a, p, q, b, c):
    return pol.get_summary(a, p["business_id"])


def _pol_profiles_list(a, p, q, b, c):
    return pol.list_profiles(a, p["business_id"],
                             include_archived=_qbool(q, "include_archived"))


def _pol_profile_get(a, p, q, b, c):
    return pol.get_profile(a, p["business_id"], p["profile_id"])


def _pol_profile_create(a, p, q, b, c):
    return pol.create_profile(a, p["business_id"], b, context=c)


def _pol_profile_update(a, p, q, b, c):
    return pol.update_profile(a, p["business_id"], p["profile_id"], b, context=c)


def _pol_profile_default(a, p, q, b, c):
    return pol.make_default(a, p["business_id"], p["profile_id"], context=c)


def _pol_profile_archive(a, p, q, b, c):
    return pol.archive_profile(a, p["business_id"], p["profile_id"], context=c)


def _pol_return_get(a, p, q, b, c):
    return pol.get_return_policy(a, p["business_id"])


def _pol_return_put(a, p, q, b, c):
    return pol.put_return_policy(a, p["business_id"], b, context=c)


def _ver_publish(a, p, q, b, c):
    return versions.post_publish(p["business_id"], a, b, context=c)


def _ver_restore(a, p, q, b, c):
    return versions.post_restore(p["business_id"], a, p["version_id"], b,
                                 context=c)


def _ver_list(a, p, q, b, c):
    return versions.get_list(p["business_id"], a, limit=_qint(q, "limit", 50))


def _ver_get(a, p, q, b, c):
    return versions.get_one(p["business_id"], a, p["version_id"])


def _ver_draft_status(a, p, q, b, c):
    return versions.get_draft_status(p["business_id"], a)


def _ver_published(a, p, q, b, c):
    # PUBLIC shopper read — actor is ignored by design.
    return versions.get_published(p["business_id"])


# --- the route table ---------------------------------------------------------
# auth: "user"   -> session required (401 without one)
#       "public" -> no session needed
#       "admin"  -> bot.py's require_admin_api gate (permission in admin_perm)
ROUTES: Tuple[Dict[str, Any], ...] = (
    # Offers
    dict(name="commerce_offers_create", method="POST",
         rule="/marketplace/offers", auth="user", fn=_offers_create),
    dict(name="commerce_offers_list", method="GET",
         rule="/marketplace/offers", auth="user", fn=_offers_list),
    dict(name="commerce_offers_get", method="GET",
         rule="/marketplace/offers/<offer_id>", auth="user", fn=_offers_get),
    dict(name="commerce_offers_act", method="POST",
         rule="/marketplace/offers/<offer_id>/<action>", auth="user",
         fn=_offers_act),
    dict(name="commerce_offers_expire", method="POST",
         rule="/marketplace/offers/expire-sweep", auth="admin",
         admin_perm="marketplace.manage", fn=_offers_expire),
    # Returns
    dict(name="commerce_returns_create", method="POST",
         rule="/marketplace/returns", auth="user", fn=_returns_create),
    dict(name="commerce_returns_list", method="GET",
         rule="/marketplace/returns", auth="user", fn=_returns_list),
    dict(name="commerce_returns_get", method="GET",
         rule="/marketplace/returns/<return_id>", auth="user", fn=_returns_get),
    dict(name="commerce_returns_act", method="POST",
         rule="/marketplace/returns/<return_id>/<action>", auth="user",
         fn=_returns_act),
    # Inventory
    dict(name="commerce_inventory_overview", method="GET",
         rule="/marketplace/inventory", auth="user", fn=_inventory_overview),
    dict(name="commerce_inventory_adjustments", method="GET",
         rule="/marketplace/inventory/adjustments", auth="user",
         fn=_inventory_adjustments),
    dict(name="commerce_inventory_adjust", method="POST",
         rule="/marketplace/products/<product_id>/inventory/adjust",
         auth="user", fn=_inventory_adjust),
    # Listing drafts
    dict(name="commerce_drafts_create", method="POST",
         rule="/marketplace/listing-drafts", auth="user", fn=_drafts_create),
    dict(name="commerce_drafts_list", method="GET",
         rule="/marketplace/listing-drafts", auth="user", fn=_drafts_list),
    dict(name="commerce_drafts_get", method="GET",
         rule="/marketplace/listing-drafts/<draft_id>", auth="user",
         fn=_drafts_get),
    dict(name="commerce_drafts_update", method="PATCH",
         rule="/marketplace/listing-drafts/<draft_id>/<section>", auth="user",
         fn=_drafts_update),
    dict(name="commerce_drafts_publish", method="POST",
         rule="/marketplace/listing-drafts/<draft_id>/publish", auth="user",
         fn=_drafts_publish),
    dict(name="commerce_drafts_discard", method="POST",
         rule="/marketplace/listing-drafts/<draft_id>/discard", auth="user",
         fn=_drafts_discard),
    # Seller dashboard + reports
    dict(name="commerce_dash_action_center", method="GET",
         rule="/marketplace/seller/action-center", auth="user",
         fn=_dash_action_center),
    dict(name="commerce_dash_sales_summary", method="GET",
         rule="/marketplace/seller/sales-summary", auth="user",
         fn=_dash_sales_summary),
    dict(name="commerce_reports_finance", method="GET",
         rule="/marketplace/seller/reports/finance", auth="user",
         fn=_reports_finance),
    dict(name="commerce_reports_sales_by_day", method="GET",
         rule="/marketplace/seller/reports/sales-by-day", auth="user",
         fn=_reports_sales_by_day),
    # Store policies
    dict(name="commerce_policies_summary", method="GET",
         rule="/store/<business_id>/policies", auth="user", fn=_pol_summary),
    dict(name="commerce_shipping_profiles_list", method="GET",
         rule="/store/<business_id>/shipping-profiles", auth="user",
         fn=_pol_profiles_list),
    dict(name="commerce_shipping_profile_create", method="POST",
         rule="/store/<business_id>/shipping-profiles", auth="user",
         fn=_pol_profile_create),
    dict(name="commerce_shipping_profile_get", method="GET",
         rule="/store/<business_id>/shipping-profiles/<profile_id>",
         auth="user", fn=_pol_profile_get),
    dict(name="commerce_shipping_profile_update", method="PATCH",
         rule="/store/<business_id>/shipping-profiles/<profile_id>",
         auth="user", fn=_pol_profile_update),
    dict(name="commerce_shipping_profile_default", method="POST",
         rule="/store/<business_id>/shipping-profiles/<profile_id>/make-default",
         auth="user", fn=_pol_profile_default),
    dict(name="commerce_shipping_profile_archive", method="POST",
         rule="/store/<business_id>/shipping-profiles/<profile_id>/archive",
         auth="user", fn=_pol_profile_archive),
    dict(name="commerce_return_policy_get", method="GET",
         rule="/store/<business_id>/return-policy", auth="user",
         fn=_pol_return_get),
    dict(name="commerce_return_policy_put", method="PUT",
         rule="/store/<business_id>/return-policy", auth="user",
         fn=_pol_return_put),
    # Storefront versions
    dict(name="commerce_versions_publish", method="POST",
         rule="/store/<business_id>/storefront/versions/publish", auth="user",
         fn=_ver_publish),
    dict(name="commerce_versions_restore", method="POST",
         rule="/store/<business_id>/storefront/versions/<version_id>/restore",
         auth="user", fn=_ver_restore),
    dict(name="commerce_versions_list", method="GET",
         rule="/store/<business_id>/storefront/versions", auth="user",
         fn=_ver_list),
    dict(name="commerce_versions_get", method="GET",
         rule="/store/<business_id>/storefront/versions/<version_id>",
         auth="user", fn=_ver_get),
    dict(name="commerce_draft_status", method="GET",
         rule="/store/<business_id>/storefront/draft-status", auth="user",
         fn=_ver_draft_status),
    dict(name="commerce_storefront_published", method="GET",
         rule="/store/<business_id>/storefront/published", auth="public",
         fn=_ver_published),
)


def dispatch(name: str, actor_user_id: Any, *,
             path: Optional[dict] = None, query: Optional[dict] = None,
             body: Any = None,
             context: Optional[dict] = None) -> Tuple[int, dict]:
    """Invoke a route by name. Used by the Flask adapter and by tests, so the
    exact code path exercised standalone is the one production serves."""
    for r in ROUTES:
        if r["name"] == name:
            return r["fn"](actor_user_id, path or {}, query or {}, body,
                           context or {})
    return (404, {"ok": False, "error": "Unknown route.", "code": "not_found"})
