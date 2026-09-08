"""Which store a merchant is acting for when they use dropshipping.

The problem this exists to solve
-------------------------------
PulseSoc grew two separate merchant identities:

* ``marketplace_sellers`` — one row per user, created by the merchant
  application in ``services/seller_lifecycle.py``, ``status='approved'`` once
  they may sell. This is what the Store dashboard means by "your store"; the
  name on it is what buyers see (``services/marketplace_seller_identity.py``).
* ``business_os_business`` + ``business_os_store_storefront`` — an explicitly
  created management workspace. Nothing creates it automatically.

There is no column, join, or foreign key between them. A merchant trading as
"M&W Store · Open for orders" therefore had a store by the only definition the
rest of the app uses, while the supplier gateway — which only ever asked
Business OS — told them to go and set a business up first. They were being
asked to invent a second identity to connect a supplier to the store they
already had.

The rule
--------
Store ownership is the authority. Business OS is management metadata, and a
merchant who has one keeps using it, but its absence is not a blocker.

Resolution order, and why it is this order
------------------------------------------
1. **A scope that already owns supplier connections.** A merchant who connects
   a supplier and *then* creates a Business OS workspace must not have their
   live connection disappear because a later step changed which branch wins.
   Whatever scope their connections are filed under stays their scope.
2. **Business OS business + storefront.** Existing workspace merchants keep the
   exact scope they have today; this module changes nothing for them.
3. **An approved marketplace seller**, addressed by :func:`seller_scope`.

Only when none of those hold is the merchant genuinely without a store.

Why a namespaced id rather than a Business OS row
--------------------------------------------------
Auto-creating a business row for every seller would seed a second commerce
authority — rows nothing else reads, that drift from the seller record, and
that make "does this merchant have a business" permanently untrue as an answer.
Instead a seller-backed scope is *derived* from the seller: ``mkt-seller:<id>``.

It is not a capability. It carries the owner's user id in plain sight
deliberately, because it is re-verified against the authenticated caller on
every request (:func:`verify_seller_scope`) — a merchant who sends someone
else's scope is rejected on ownership, not on the id being unguessable.
"""
from __future__ import annotations

from services import marketplace_seller_identity

#: Namespace for a scope backed by a marketplace seller rather than a Business
#: OS workspace. The colon cannot occur in a Business OS id, so the two id
#: spaces cannot collide.
SELLER_SCOPE_PREFIX = "mkt-seller:"

#: The one seller status that means "may sell" (``seller_lifecycle``).
APPROVED = "approved"

#: Why a merchant cannot enter dropshipping. Each is a different screen; see
#: the mission note in the mobile client. Collapsing them is how "you need a
#: business" came to be shown to someone who had a store.
GAPS = ("NO_STORE", "STORE_PENDING_REVIEW", "NO_STOREFRONT")


class ScopeError(ValueError):
    def __init__(self, message, http_status=403, code="forbidden"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


def seller_scope(user_id):
    """The scope id pair for a seller-backed store."""
    ident = SELLER_SCOPE_PREFIX + str(user_id).strip()
    return ident, ident


def seller_scope_owner(business_id, store_id):
    """The user id a seller-backed scope names, or ``None`` if it is not one.

    Both halves must be the same seller scope. A request pairing a seller
    business with someone else's store id is not a seller scope at all, and
    falls through to the Business OS check, which will not find it.
    """
    business_id, store_id = str(business_id or ""), str(store_id or "")
    if business_id != store_id or not business_id.startswith(SELLER_SCOPE_PREFIX):
        return None
    owner = business_id[len(SELLER_SCOPE_PREFIX):]
    return owner if owner.isdigit() else None


def _actor_text(user_id):
    return str(user_id).strip()


def _actor_int(user_id):
    """``marketplace_sellers.user_id`` is an integer column, while the Business OS
    identity keys off text ones. Postgres refuses to compare across the two —
    ``operator does not exist: text = integer`` — so each query is given the form
    its own column is declared in. SQLite compares the two loosely, which is why
    every test passed against a store identity that could not be read at all.
    """
    try:
        return int(_actor_text(user_id))
    except ValueError:
        return None


def _seller_row(conn, user_id):
    actor = _actor_int(user_id)
    if actor is None:
        return None
    return conn.execute(
        "SELECT user_id, status, display_name, business_name FROM marketplace_sellers "
        "WHERE user_id=?", (actor,)).fetchone()


def seller_merchant(conn, owner_user_id):
    """The merchant id for a seller-backed store, or refuse if it cannot sell.

    No actor argument on purpose: the worker and hydrate paths resolve a
    merchant from a persisted scope tuple with nobody logged in, exactly as they
    do for Business OS scopes. Caller ownership is a separate question, asked by
    :func:`verify_seller_scope` on the request paths that have an actor.
    """
    row = _seller_row(conn, owner_user_id)
    if row is None:
        raise ScopeError("Supplier connection not found.", 404, "not_found")
    if str(row["status"] or "").lower() != APPROVED:
        raise ScopeError("Your store is not approved to sell yet.", 403, "store_not_approved")
    return str(owner_user_id)


def verify_seller_scope(conn, owner_user_id, actor_user_id):
    """Confirm the caller owns this seller-backed store. Returns the merchant id.

    This is the whole tenancy check for the seller branch, and it is deliberately
    the same shape as the Business OS one it replaces: prove the authenticated
    caller owns the store named in the request, or refuse. Merchant A sending
    merchant B's scope fails here — and fails as 404, so the response cannot be
    used to learn whether another merchant's store exists.
    """
    if str(owner_user_id) != str(actor_user_id or ""):
        raise ScopeError("Supplier connection not found.", 404, "not_found")
    return seller_merchant(conn, owner_user_id)


def _business_os_scope(conn, user_id):
    """The caller's Business OS business and storefront, if they have both."""
    row = conn.execute(
        "SELECT b.business_id, s.storefront_id, b.display_name, b.legal_name "
        "FROM business_os_business b "
        "JOIN business_os_store_storefront s ON s.business_id=b.business_id "
        "WHERE b.owner_user_id=? AND COALESCE(b.status,'') NOT IN ('archived','suspended') "
        "AND COALESCE(s.status,'') NOT IN ('archived','suspended') "
        "ORDER BY b.created_at LIMIT 1", (_actor_text(user_id),)).fetchone()
    if row is None:
        return None
    return {
        "business_id": str(row["business_id"]),
        "store_id": str(row["storefront_id"]),
        "store_name": str(row["display_name"] or row["legal_name"] or "").strip(),
        "source": "BUSINESS_OS",
    }


def _has_business_without_storefront(conn, user_id):
    return conn.execute(
        "SELECT 1 FROM business_os_business WHERE owner_user_id=? "
        "AND COALESCE(status,'') NOT IN ('archived','suspended') LIMIT 1",
        (_actor_text(user_id),)).fetchone() is not None


def _existing_connection_scope(conn, user_id):
    """The scope this merchant's supplier connections are already filed under."""
    try:
        row = conn.execute(
            "SELECT business_id, store_id FROM business_os_supplier_connections "
            "WHERE merchant_id=? ORDER BY created_at LIMIT 1", (str(user_id),)).fetchone()
    except Exception:
        # The table is created lazily by the supplier schema module. A merchant
        # with no connections is the common case and must not be an error.
        return None
    return (str(row["business_id"]), str(row["store_id"])) if row else None


def resolve(conn, user_id):
    """The store this merchant acts for, or why they have none.

    ``{"status": "ok", "business_id", "store_id", "store_name", "source"}``
    or ``{"status": "missing", "gap": <one of GAPS>}``.
    """
    if user_id is None or not str(user_id).strip():
        raise ScopeError("Authentication required.", 401, "unauthorized")

    seller = _seller_row(conn, user_id)
    seller_approved = seller is not None and str(seller["status"] or "").lower() == APPROVED
    business = _business_os_scope(conn, user_id)

    existing = _existing_connection_scope(conn, user_id)
    if existing is not None:
        owner = seller_scope_owner(*existing)
        if owner is not None and owner == str(user_id) and seller_approved:
            return _seller_result(seller, user_id)
        if business is not None and (business["business_id"], business["store_id"]) == existing:
            return {"status": "ok", **business}

    if business is not None:
        return {"status": "ok", **business}
    if seller_approved:
        return _seller_result(seller, user_id)
    if _has_business_without_storefront(conn, user_id):
        return {"status": "missing", "gap": "NO_STOREFRONT"}
    if seller is not None:
        return {"status": "missing", "gap": "STORE_PENDING_REVIEW"}
    return {"status": "missing", "gap": "NO_STORE"}


def _seller_result(seller, user_id):
    business_id, store_id = seller_scope(user_id)
    return {
        "status": "ok",
        "business_id": business_id,
        "store_id": store_id,
        # The buyer-facing store name, from the one module that owns it. A
        # personal name must never appear here.
        "store_name": marketplace_seller_identity.store_name(seller),
        "source": "MARKETPLACE_SELLER",
    }
