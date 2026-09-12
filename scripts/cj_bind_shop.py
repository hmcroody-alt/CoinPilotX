"""List a CJ connection's shops and bind the one it will fulfil through.

DRY RUN BY DEFAULT. Pass ``--apply`` to write.

Why this script exists
----------------------
``services/business_os/suppliers/fulfillment.py`` refuses to place a supplier
order when the connection has no bound shop::

    if not meta.get("external_shop_id"):
        raise FulfillmentError("shop_binding_required")

That refusal is correct -- it is what lets the read-back check prove a placed
order came back on *the shop we bound*. But until commit b9538c8c the column had
exactly one writer, ``connect_cj``, and ``connect_cj`` refuses to change an
existing binding, treating ``""`` as a value for that comparison. So a
connection that landed unbound could not be bound by reconnecting either: that
answered ``connection_binding_conflict``. The state was terminal in both
directions.

``connections.bind_shop`` is the transition that was missing, and
``POST /api/business-os/suppliers/cj/connections/<id>/bind-shop`` routes to it.
No merchant-facing UI calls that route yet. A guard is only finished when
something can satisfy it, so this is that something, in operator form.

What it will and will not do
----------------------------
* Binding is **none-to-one only**. If the connection already names a shop,
  ``bind_shop`` answers 409 ``connection_binding_conflict`` and this script
  reports it rather than replacing anything.
* The chosen shop must be one CJ can actually receive an API order on:
  ``status == 1``, ``platform == "API"``, and a name unique within the list.
  Shops failing that are printed with the reason and cannot be bound. A Shopify
  or Woo storefront the merchant authorized in CJ is listed by CJ but is not a
  destination for an order placed over the API.
* It places **no supplier order** and spends nothing. Binding writes one column.

Scope identifiers are arguments or environment, never literals -- ``connection_id``
is an internal signal the buyer must never see, and this repo is public.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def render(payload):
    bound = payload.get("external_shop_id") or ""
    print("\n  currently bound: %s" % (repr(bound) if bound else "(none)"))
    shops = payload.get("shops") or []
    if not shops:
        print("  no shops listed for this credential")
        return shops
    print("\n  %-28s %-10s %-7s %-12s %s" % ("shop_id", "platform", "status", "fulfillable", "name"))
    for shop in shops:
        print("  %-28s %-10s %-7s %-12s %s%s" % (
            shop.get("shop_id"), shop.get("platform"), shop.get("status"),
            "yes" if shop.get("fulfillable") else "no",
            shop.get("name"),
            "" if shop.get("fulfillable") else "   <- %s" % shop.get("unfulfillable_reason")))
    return shops


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write the binding; without this the script only lists")
    parser.add_argument("--business", default=os.environ.get("CJ_BUSINESS_ID"))
    parser.add_argument("--store", default=os.environ.get("CJ_STORE_ID"))
    parser.add_argument("--actor", type=int,
                        default=int(os.environ.get("CJ_ACTOR_ID") or 0) or None)
    parser.add_argument("--connection", default=os.environ.get("CJ_CONNECTION_ID"))
    parser.add_argument("--shop", default=os.environ.get("CJ_SHOP_ID"),
                        help="the CJ shop_id to bind; required with --apply")
    args = parser.parse_args()

    missing = [name for name in ("business", "store", "actor", "connection")
               if not getattr(args, name)]
    if missing:
        parser.error("missing scope: %s (pass --%s, or set CJ_%s_ID)"
                     % (", ".join(missing), missing[0], missing[0].upper()))
    if args.apply and not args.shop:
        parser.error("--apply needs --shop (or CJ_SHOP_ID); run without --apply first to list")

    # `services.db` resolves DATABASE_URL at connect time, and under
    # `railway run` that is the *internal* host, unreachable from a laptop.
    # Point it at the public URL before the import that will bind it.
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("set DATABASE_PUBLIC_URL or DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("BUSINESS_OS_SUPPLIERS_CJ", "1")

    from services.business_os.suppliers import connections
    from services.business_os.suppliers.errors import SupplierError

    scope = (args.connection, args.business, args.store, args.actor)

    try:
        listing = connections.connection_shops(*scope)
    except connections.SupplierConnectionError as exc:
        print("  connection_shops refused: %s (%s)" % (exc, getattr(exc, "code", "")))
        return 1
    except SupplierError as exc:
        # CJ itself would not answer `shop/getShops`. `_verify` documents the
        # case: an account that owns no external storefront is answered with a
        # business code CJ's own documentation does not list. Connecting
        # survives that (importing needs no shop); binding cannot, because there
        # is nothing to choose from. Report the two diagnostic coordinates the
        # error is allowed to carry and stop -- this is a fact about the CJ
        # account, not something the code can route around.
        print("\n  CJ refused the shop list: %s endpoint=%s provider_code=%s"
              % (exc.code, exc.endpoint, exc.provider_code))
        print("  No shop can be bound until this CJ account owns an API-platform shop.")
        return 2
    shops = render(listing)

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply --shop <shop_id>.")
        return 0

    chosen = [s for s in shops if s.get("shop_id") == args.shop]
    if not chosen:
        print("\n  %r is not in this credential's shop list; refusing to call bind_shop."
              % args.shop)
        return 1
    if not chosen[0].get("fulfillable"):
        print("\n  %r cannot receive an API order (%s); bind_shop would refuse."
              % (args.shop, chosen[0].get("unfulfillable_reason")))
        return 1

    try:
        result = connections.bind_shop(*scope, args.shop)
    except connections.SupplierConnectionError as exc:
        print("\n  bind_shop refused: %s (%s)" % (exc, getattr(exc, "code", "")))
        return 1
    print("\n  bind_shop -> %s" % json.dumps(result, default=str))

    render(connections.connection_shops(*scope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
