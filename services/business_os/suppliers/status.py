"""One answer to "what is the state of this store's suppliers, and what next?".

Why this module exists
----------------------
Every dropshipping screen used to assemble its own version of this. The hub
called the connections list and the imported-products list and inferred health
from the pair; the suppliers screen read a status string; the sync screen read
``sync_state`` alone. Four readers, four rules, and no way to make them agree --
so a merchant could be told "Connected" on one screen, "Needs attention" on the
next, and nothing at all on a third, about the same connection at the same
moment.

The specific harm is not inconsistency, it is that inference invents. A client
that has the connections list and the products list does not have the fact it
wants to display, so it derives one, and the derivation is a second
implementation of a rule that lives somewhere else. That is how a screen ends up
showing a green badge over a connection that cannot fulfil anything.

So the states are computed once, here, and the clients render them.

Nothing here decides anything
-----------------------------
Two facts in this payload are *policy*, not observation, and this module
deliberately does not compute them:

* ``environment`` comes from :func:`policy.fulfillment_environment`, which
  probes the real gate rather than re-reading ``CJ_ENVIRONMENT_MODE``.
* ``real_order_submission_enabled`` comes from
  :func:`policy.live_fulfillment_path_exists`, which is presently ``False``
  everywhere -- not because the code is missing, but because the decision to
  send real supplier orders has not been made.

Re-deriving either from an environment variable here would produce a badge that
disagrees with the gate it claims to describe, which is worse than no badge:
it would say "Live" over a runtime that rejects live orders. This module reports
what those functions return and has no opinion of its own.

``next_action`` is a rendering of state, not a new authority
------------------------------------------------------------
:func:`_next_action` picks the one thing a merchant should do next. It is
ordered by what blocks what -- a connection that cannot authenticate makes the
shop question moot, and an unbound shop makes the product questions moot -- so
the merchant is never sent to fix a symptom of something further up. Every
branch is reachable only from a state already in this payload, so a client can
always show the reason beside the action, and a client that prefers its own
ordering can ignore the field without losing information.
"""

from __future__ import annotations

from services.business_os.suppliers import connections, drafts, fulfillment, policy


#: The next thing to do, in the order things block each other.
RECONNECT = "RECONNECT_SUPPLIER"
CHOOSE_SHOP = "CHOOSE_FULFILLMENT_SHOP"
RETRY_SYNC = "RETRY_SYNC"
RESOLVE_ISSUES = "RESOLVE_PRODUCT_ISSUES"
IMPORT_PRODUCTS = "IMPORT_FIRST_PRODUCT"
REVIEW_DRAFTS = "REVIEW_DRAFTS"

#: Shop binding, as a state rather than as "is this string empty". A client
#: testing the id for truthiness cannot distinguish "not chosen yet" from
#: "chosen, and the provider later stopped offering it".
SHOP_BOUND = "BOUND"
SHOP_NOT_SELECTED = "NOT_SELECTED"

#: Sync rollups that mean the catalogue data cannot be trusted right now, as
#: opposed to being trustworthy and reporting bad news. ``UNKNOWN`` is here
#: because a state we do not recognise is one we cannot vouch for.
_SYNC_BROKEN = {"ERROR", "DISCONNECTED", "UNKNOWN"}

#: How far back one status read counts obligations. The supplier-orders screen
#: asks the same question with the same ceiling, so a tile and the list it opens
#: are counting the same rows; `counted_through` reports the ceiling rather than
#: hiding it, because a number a merchant cannot tell is partial is one they will
#: read as complete.
_ORDERS_LIMIT = 200


def _orders(connection_id, business_id, store_id, actor_user_id, *, context=None):
    """Paid sales still owing a supplier purchase, or ``None`` when unreadable.

    Counted through :func:`fulfillment.list_obligations` rather than through a
    second query. A tile saying "3 waiting" over a screen listing four is the
    drift this module exists to end, and the count is not reproducible in SQL
    anyway: an obligation is only real if ``effective_listing_type`` calls the
    listing physical, which is app logic applied to the rows *after* they are
    fetched.

    ``None`` on failure, never zero. "Nothing is waiting" is a claim about the
    merchant's orders, and a read that did not happen cannot support it -- a
    fulfilment table that was briefly unreachable must not be rendered as a
    quiet store.
    """
    try:
        rows = fulfillment.list_obligations(connection_id, business_id, store_id,
                                            actor_user_id, limit=_ORDERS_LIMIT,
                                            context=context)["obligations"]
    except Exception:
        return None
    awaiting = [row for row in rows if not row.get("supplier_order_placed")]
    # Split by whether the merchant can act, because the two need opposite
    # things from them: `ready` is a button, `blocked` is a reason to read.
    ready = [row for row in awaiting if row.get("can_place_supplier_order")]
    return {
        "awaiting_supplier_order": len(awaiting),
        "ready_to_place": len(ready),
        "blocked": len(awaiting) - len(ready),
        "placed": len(rows) - len(awaiting),
        "counted_through": _ORDERS_LIMIT,
    }


def _next_action(connection, counts):
    """The single most upstream unmet requirement, or None when there is none.

    Ordered by dependency, not by severity. A merchant whose credentials have
    expired is not helped by being told three products are out of stock: the
    stock reading is from before the credentials expired, and fixing it first
    is work they will do twice.
    """
    if connection.get("status") != "CONNECTED":
        return RECONNECT
    if not connection.get("external_shop_id"):
        return CHOOSE_SHOP
    if counts["sync_state"] in _SYNC_BROKEN:
        return RETRY_SYNC
    if counts["attention_products"] > 0:
        return RESOLVE_ISSUES
    if counts["imported"] == 0:
        return IMPORT_PRODUCTS
    if counts["draft"] > 0:
        return REVIEW_DRAFTS
    return None


def _for_connection(connection, business_id, store_id, actor_user_id, *, context=None):
    counts = drafts.status_counts(business_id, store_id, actor_user_id,
                                  connection["id"], context=context)
    shop_state = SHOP_BOUND if connection.get("external_shop_id") else SHOP_NOT_SELECTED
    action = _next_action(connection, counts)
    return {
        "connection_id": connection["id"],
        # Lower-cased so the client never branches on a provider name; it is an
        # identity for a label and an icon, not a condition.
        "provider": str(connection.get("provider") or "").lower() or "unknown",
        "connection_state": connection.get("status") or "UNKNOWN",
        "message": connection.get("message"),
        # Both from `policy`, neither recomputed. See the module docstring.
        "environment": connection.get("environment"),
        "real_order_submission_enabled": bool(connection.get("production_fulfillment_enabled")),
        "fulfillment_shop_state": shop_state,
        "external_shop_id": connection.get("external_shop_id"),
        "credential_present": bool(connection.get("credential_present")),
        "last_verified_at": connection.get("last_verified_at"),
        "last_sync_at": connection.get("last_sync_at"),
        # When the *catalogue* last moved, which is a different clock from
        # `last_sync_at` on the connection: one connection-level call can
        # succeed while every product row stays untouched.
        "last_product_sync_at": counts["last_synced_at"],
        "products": {
            "imported": counts["imported"],
            "published": counts["published"],
            "awaiting_review": counts["awaiting_review"],
            "draft": counts["draft"],
            "blocked": counts["blocked"],
            "archived": counts["archived"],
            "other": counts["other"],
            "by_status": counts["by_status"],
        },
        "sync_state": counts["sync_state"],
        "sync": counts["sync"],
        "issues": {
            "products": counts["attention_products"],
            "cost": counts["cost_attention"],
            "stock": counts["stock_attention"],
            "by_reason": counts["attention"],
        },
        # Null rather than absent when unreadable, so a client can tell "no
        # orders are waiting" from "we could not find out" without knowing
        # which keys this payload is supposed to have.
        "orders": _orders(connection["id"], business_id, store_id, actor_user_id,
                          context=context),
        "next_action": action,
        # Not `action is not None`: REVIEW_DRAFTS is a suggestion, and a store
        # whose only outstanding item is "you have drafts" is working. Treating
        # every suggestion as an alert is how a permanent badge gets ignored.
        "needs_attention": action in {RECONNECT, CHOOSE_SHOP, RETRY_SYNC, RESOLVE_ISSUES},
    }


def supplier_status(business_id, store_id, actor_user_id, *, context=None):
    """Every supplier connection for this store, with its full operating state.

    Returns all connections rather than one, because the question a merchant
    surface asks is "what is the state of my suppliers" and answering it per
    connection would put the aggregation back in the client -- which is the
    thing this module exists to stop.
    """
    policy.require_enabled()
    rows = connections.list_connections(business_id, store_id, actor_user_id, context=context)
    suppliers = [_for_connection(row, business_id, store_id, actor_user_id, context=context)
                 for row in rows]
    return {
        # Store-wide and provider-independent: the kill switch is platform-level,
        # so a client must not read it off whichever connection happens to be
        # first in the list.
        "environment": policy.fulfillment_environment(),
        "real_order_submission_enabled": policy.live_fulfillment_path_exists(),
        "suppliers": suppliers,
        # Answered here so a hub tile does not re-implement "is anything wrong"
        # by scanning the list with its own rule.
        "needs_attention": any(s["needs_attention"] for s in suppliers),
    }
