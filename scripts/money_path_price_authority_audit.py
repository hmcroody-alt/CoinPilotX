"""Read-only: does the price a shopper sees match the price checkout would charge?

The display layer treats `marketplace_listing_variants.price_cents` as the price
authority (bot.py:41664, bot.py:55708). Every checkout lane instead parses
`marketplace_listings.price_label` (bot.py:95819 buy-now,
marketplace_cart_routes.py price_snapshot_minor, marketplace_offers_routes.py).
This measures how far apart those two answers are on live rows.

SELECT only. Run: railway run --service Postgres .venv/bin/python <this>
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from decimal import Decimal

import psycopg2
import psycopg2.extras

PRICE_LABEL_UNPRICED = {"free", "request access", "paid later", "premium later"}


def parse_price_label_to_cents(value, default_currency="USD"):
    """Mirror of bot.py:4893 so the audit prices a row exactly as checkout does."""
    text = (value or "").strip()
    if not text or text.lower() in PRICE_LABEL_UNPRICED:
        return 0, default_currency
    match = re.search(r"([A-Z]{3})?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text.upper())
    if not match:
        return 0, default_currency
    currency = (match.group(1) or default_currency or "USD").upper()
    digits = match.group(2).replace(",", "")
    if text.lstrip().startswith("-"):
        return 0, currency
    return int(round(Decimal(digits) * 100)), currency


def main() -> int:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL") or ""
    if not dsn:
        print("no DSN in env (DATABASE_PUBLIC_URL / DATABASE_URL)")
        return 2
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT count(*) AS n FROM marketplace_listings")
    print(f"marketplace_listings total: {cur.fetchone()['n']}")
    cur.execute(
        "SELECT status, COALESCE(approval_status,'<null>') AS appr, count(*) AS n "
        "FROM marketplace_listings GROUP BY 1,2 ORDER BY n DESC"
    )
    print("status / approval_status distribution:")
    for row in cur.fetchall():
        print(f"    {str(row['status']):<14} {row['appr']:<16} {row['n']}")
    print()

    cur.execute("SELECT * FROM marketplace_listings ORDER BY id")
    listings = cur.fetchall()

    cur.execute(
        """
        SELECT listing_id, price_cents, currency, status, stock_state
        FROM marketplace_listing_variants
        WHERE COALESCE(status,'active') NOT IN ('archived','deleted')
        """
    )
    variants: dict[int, list[dict]] = {}
    for row in cur.fetchall():
        variants.setdefault(int(row["listing_id"] or 0), []).append(row)

    charged_zero_but_displayed = []
    amount_disagrees = []
    variant_spread = []
    label_counter: Counter = Counter()

    for listing in listings:
        lid = int(listing["id"])
        label_cents, _c = parse_price_label_to_cents(
            listing.get("price_label"), listing.get("currency") or "USD"
        )
        label_counter[repr((listing.get("price_label") or "").strip()[:20])] += 1
        vs = [int(v["price_cents"] or 0) for v in variants.get(lid, []) if v["price_cents"]]
        display_cents = min(vs) if vs else label_cents

        if vs and len(set(vs)) > 1:
            variant_spread.append((lid, listing.get("title"), min(vs), max(vs), label_cents))
        if label_cents == 0 and display_cents > 0:
            charged_zero_but_displayed.append(
                (lid, listing.get("title"), repr(listing.get("price_label")),
                 display_cents, listing.get("status"))
            )
        elif vs and label_cents > 0 and display_cents != label_cents:
            amount_disagrees.append((lid, listing.get("title"), label_cents, display_cents,
                                     listing.get("status")))

    print(f"listings with live variants: {len(variants)}")
    print(f"most common price_label values: {label_counter.most_common(8)}")
    print()
    print(f"[A] CHARGED 0 BUT DISPLAYS A PRICE: {len(charged_zero_but_displayed)}")
    for r in charged_zero_but_displayed[:30]:
        print(f"    id={r[0]:<6} status={str(r[4]):<10} label={r[2]:<16} displays={r[3]/100:>9.2f}  {str(r[1])[:40]}")
    print()
    print(f"[B] LABEL AND VARIANT DISAGREE: {len(amount_disagrees)}")
    for r in amount_disagrees[:30]:
        print(f"    id={r[0]:<6} status={str(r[4]):<10} charges={r[2]/100:>9.2f} displays={r[3]/100:>9.2f}  {str(r[1])[:36]}")
    print()
    print(f"[C] VARIANTS SPAN MORE THAN ONE PRICE: {len(variant_spread)}")
    for r in variant_spread[:30]:
        print(f"    id={r[0]:<6} variants {r[2]/100:.2f}..{r[3]/100:.2f}  charges={r[4]/100:.2f}  {str(r[1])[:34]}")
    print()

    print("=== seller_payout_accounts (Connect readiness) ===")
    try:
        cur.execute("SELECT * FROM seller_payout_accounts ORDER BY id")
        for row in cur.fetchall():
            safe = {
                k: row.get(k)
                for k in (
                    "id", "user_id", "seller_type", "provider", "provider_account_id",
                    "charges_enabled", "payouts_enabled", "details_submitted",
                    "disabled_reason", "status", "created_at", "updated_at",
                )
                if k in row
            }
            print(f"    {safe}")
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR {exc}")
    print()

    for table in (
        "marketplace_orders",
        "marketplace_commercial_settlements",
        "marketplace_commercial_refunds",
        "seller_transactions",
        "marketplace_cart_items",
        "marketplace_inventory_reservations",
        "payment_webhook_events",
        "stripe_events",
    ):
        try:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            print(f"{table:<42} {cur.fetchone()['n']}")
        except Exception as exc:  # noqa: BLE001
            print(f"{table:<42} ERROR {str(exc)[:60]}")

    print()
    print("=== seller_transactions status breakdown ===")
    try:
        cur.execute(
            "SELECT status, count(*) AS n, min(amount_cents) AS lo, max(amount_cents) AS hi "
            "FROM seller_transactions GROUP BY 1 ORDER BY n DESC"
        )
        for row in cur.fetchall():
            print(f"    {str(row['status']):<34} n={row['n']:<4} amount {row['lo']}..{row['hi']}")
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR {exc}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
