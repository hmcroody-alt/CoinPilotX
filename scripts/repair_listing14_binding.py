"""Repair production listing 14: bind the variant it sells, restock the shelf.

DRY RUN BY DEFAULT. Pass ``--apply`` to write.

What is wrong with it
---------------------
Listing 14 is published, moderator-approved and publicly on sale, and it is in
the state the seventh seam describes (see
``CJ_IMPORT_TO_CHECKOUT_ARCHITECTURE_MAP.md``). Measured:

* ``marketplace_product_sources.provider_variant_id`` is NULL, so
  ``gateway.get_product_binding`` refuses and ``fulfillment.create_intent``
  cannot place an order for it. A buyer can pay; nothing can ship.
* ``quantity`` is 1 against 132 units at CJ, because ``publish`` seeded a unit
  ledger with a count of variants.

Both are fixed in the code now, but code does not go back and rewrite rows. This
does, for this one row.

What it does, and what it refuses to do
---------------------------------------
1. ``gateway.bind_product`` — the same call the ``bind-product`` route makes. It
   reads the product from CJ first and refuses unless the (pid, vid) pair really
   exists there, so the binding is verified against the supplier rather than
   asserted by this script. Read-only at CJ: no order, no spend.
2. ``drafts.publish`` — the pipeline's own publish, so ``quantity``,
   ``price_label`` and ``cover_image_url`` are the numbers the package computes
   and not numbers written here. Moderation state is untouched; the listing is
   already approved and ``publish`` has never written that column.

The pid and vid are **read out of production's own rows**, never typed in. This
script picks nothing: it refuses unless the listing carries exactly one variant,
because with two the question of which one the listing sells is the merchant's
to answer and a repair script answering it would be the original defect wearing
a different hat.

``published_at`` is restored afterwards. ``publish`` stamps it with now, which
would be true of a new publication and is false of this one — the product went
on sale earlier and never came off. It is one column, and the fact it records is
the reason to keep it right.

Scope identifiers are arguments, not literals: ``connection_id`` is an internal
signal the buyer must never see, and this repository is public.

Run it with the app's own environment, which is where the credential keyring
lives, and with a database URL a laptop can reach::

    export DATABASE_PUBLIC_URL="$(railway run --service Postgres \\
        printenv DATABASE_PUBLIC_URL | tail -1)"
    railway run --service CoinPilotX .venv/bin/python \\
        scripts/repair_listing14_binding.py --business ... --store ... \\
        --actor ... --connection ...
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _snapshot(url, listing_id):
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM marketplace_listings WHERE id=%s", (listing_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise SystemExit("listing %d does not exist" % listing_id)
    listing = dict(row)
    cur.execute("SELECT * FROM marketplace_product_sources WHERE listing_id=%s",
                (listing_id,))
    row = cur.fetchone()
    source = dict(row) if row else None
    cur.execute("SELECT * FROM marketplace_listing_variants WHERE listing_id=%s "
                "ORDER BY id", (listing_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return listing, source, rows


def _report(label, listing, source, rows):
    from services import marketplace_listing_lifecycle as lifecycle
    from services import marketplace_variants as variants

    print("\n--- %s ---" % label)
    print("  status / approval  : %r / %r" % (listing.get("status"),
                                              listing.get("approval_status")))
    print("  price_label        : %r" % listing.get("price_label"))
    print("  shelf quantity     : %r" % listing.get("quantity"))
    print("  published_at       : %r" % listing.get("published_at"))
    print("  provider_variant_id: %s"
          % ("NULL -- nothing can be ordered"
             if not (source or {}).get("provider_variant_id") else "set"))
    for v in rows:
        print("  variant: %-11s stock=%-6r price=%r"
              % (variants.availability(v), v.get("stock_quantity"),
                 v.get("price_cents")))
    buyable = dict(listing)
    buyable["listing_type"] = buyable["product_type"] = "physical"
    offered = [n for n in (1, 2, 40, 132, 133)
               if lifecycle.inventory_available(buyable, n)]
    print("  a buyer may take   : %s units"
          % (", ".join(str(n) for n in offered) or "nothing"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--listing", type=int, default=14)
    parser.add_argument("--business", default=os.environ.get("PUBLISH_BUSINESS_ID"))
    parser.add_argument("--store", default=os.environ.get("PUBLISH_STORE_ID"))
    parser.add_argument("--actor", type=int,
                        default=int(os.environ.get("PUBLISH_ACTOR_ID") or 0) or None)
    parser.add_argument("--connection", default=os.environ.get("PUBLISH_CONNECTION_ID"))
    args = parser.parse_args()

    missing = [n for n in ("business", "store", "actor", "connection")
               if not getattr(args, n)]
    if missing:
        parser.error("missing scope: %s" % ", ".join(missing))

    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("no DATABASE_PUBLIC_URL/DATABASE_URL")
    # `services.db` binds DATABASE_URL at connect time, and under `railway run`
    # that is the internal host, unreachable from a laptop. Point it at the URL
    # the reads use, before the import that will bind it.
    os.environ["DATABASE_URL"] = url

    listing_id = args.listing
    listing, source, rows = _snapshot(url, listing_id)

    from services import marketplace_supplier_schema as supplier_schema
    from services.business_os.suppliers import drafts, gateway

    _report("BEFORE", listing, source, rows)

    if source is None:
        raise SystemExit("listing %d has no supplier source row" % listing_id)
    mode = str(source.get("fulfillment_mode") or "").upper()
    if mode != supplier_schema.MODE_DROPSHIP:
        raise SystemExit("listing %d is %s, which needs no supplier binding"
                         % (listing_id, mode or "unset"))
    if source.get("provider_variant_id"):
        raise SystemExit("listing %d is already bound -- nothing to repair here"
                         % listing_id)
    if len(rows) != 1:
        raise SystemExit(
            "listing %d carries %d variants. Which one it sells is the merchant's "
            "choice, not this script's." % (listing_id, len(rows)))

    pid = source.get("provider_product_id")
    vid = rows[0].get("provider_variant_id")
    if not pid or not vid:
        raise SystemExit("listing %d has no provider ids to bind" % listing_id)

    print("\n  would bind the single variant this listing already carries,")
    print("  then re-publish so the shelf carries units instead of a count.")

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return 0

    # ---- 1. bind, verified against CJ's own catalogue ----
    result = gateway.bind_product(
        connection_id=args.connection, business_id=args.business,
        store_id=args.store, actor_user_id=args.actor,
        canonical_product_id=listing_id, pid=pid, vid=vid, context=None)
    print("\n  bound: %s" % ({k: v for k, v in result.items()
                              if k not in {"connection_id"}},))

    # ---- 2. re-publish, so the package computes the shelf's numbers ----
    published_at = listing.get("published_at")
    outcome = drafts.publish(args.business, args.store, args.actor,
                             args.connection, listing_id, context=None)
    print("  published: %r" % (outcome,))

    # ---- 3. keep the go-live date honest ----
    if published_at is not None:
        import psycopg2

        write = psycopg2.connect(url)
        try:
            with write, write.cursor() as cur:
                cur.execute("UPDATE marketplace_listings SET published_at=%s "
                            "WHERE id=%s", (published_at, listing_id))
        finally:
            write.close()
        print("  published_at restored to %r" % (published_at,))

    _report("AFTER", *_snapshot(url, listing_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
