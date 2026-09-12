"""Probe: what lane does a buyer's order payload actually carry?

`ordersDashboard.variantOf` read `order.delivery_type` or
`order.listing.delivery_type`, and said in its own comment that the payloads "do
not always carry" it. This publishes a pickup-only listing, buys it through the
real checkout route, and prints the served order payload, so the word "always"
can be replaced with a number.

The number was zero. Neither field is served on any order, at the top level or
on the joined listing — `pulse_buyer_order_response` names its listing columns
explicitly and `delivery_type` is not among them. So the argument was always
`undefined`, the `"pickup"` branch was unreachable, and `escrowPresentable`
(`variant === "pickup"`) was false for every order the app had ever rendered.

Meanwhile the answer was already on the wire: checkout freezes the *settled*
kind into `metadata_json.fulfillment.kind`, and the serializer parses that
metadata without reading the key. The fix serves it as `fulfillment_kind`.

Kept as a script because it is the measurement the fix rests on and it runs in
about three seconds. The assertions live in `tests/test_marketplace_order_lane.py`;
this prints the payload, which is what makes the claim checkable by eye.
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_H, _DB = tempfile.mkstemp(suffix=".db", prefix="order_lane_probe_")
os.close(_H)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402

SELLER, BUYER = 99801, 99802
NOW = "2026-09-12T00:00:00"

bot.init_db()
conn = sqlite3.connect(_DB)
conn.row_factory = sqlite3.Row
conn.execute(
    "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
    "VALUES (?,'approved','Probe Store',?,?)", (SELLER, NOW, NOW))
conn.execute(
    "INSERT INTO marketplace_product_media (merchant_id, product_id, media_url, media_type, "
    "is_cover, moderation_status, created_at) VALUES (?,0,?,'image',1,'approved',?)",
    (SELLER, "https://cdn.example/c.jpg", NOW))
media_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
conn.commit()

bot.api_account_user = lambda *a, **k: {"user_id": SELLER, "username": "probe_seller"}
bot.webhook_app.config["TESTING"] = True
client = bot.webhook_app.test_client()

resp = client.post("/api/pulse/marketplace/listings/create", json={
    "title": "Pickup only chair", "description": "d" * 40, "short_description": "s",
    "category": "Home", "price": "25.00", "currency": "USD", "quantity": 5,
    "product_type": "physical", "listing_type": "physical", "submission_action": "submit",
    "media_ids": [media_id],
    "listing_metadata": {"condition": "new", "location": "Testville",
                         "delivery_options": "pickup"},
})
assert resp.status_code == 200, resp.get_json()
row = dict(conn.execute(
    "SELECT * FROM marketplace_listings ORDER BY id DESC LIMIT 1").fetchone())
listing_id = int(row["id"])

print("STORED ROW")
print(f"  delivery_type   = {row['delivery_type']!r}")
print(f"  product_type    = {row['product_type']!r}")
print(f"  listing_type    = {row['listing_type']!r}")
print(f"  delivery_options= "
      f"{json.loads(row['listing_metadata_json'] or '{}').get('delivery_options')!r}")

from services import marketplace_fulfillment as mf  # noqa: E402
kind = mf.resolve_kind(row["listing_type"] or row["product_type"], row["delivery_type"],
                       json.loads(row["listing_metadata_json"] or "{}"))
print(f"  server resolve_kind -> {kind!r}")

conn.execute("UPDATE marketplace_listings SET status='published', approval_status='approved', "
             "price_label='$25.00' WHERE id=?", (listing_id,))
conn.commit()

# Check out through the real route, so the frozen snapshot is the one a buyer
# actually produces rather than one this probe invented.
bot.api_account_user = lambda *a, **k: {"user_id": BUYER, "username": "probe_buyer"}
checkout = client.post("/api/pulse/payments/checkout", json={
    "item_type": "marketplace_product", "item_id": listing_id, "quantity": 1,
    "payment_mode": "cash",
    "fulfillment_details": {"contact_name": "Probe Buyer", "contact_phone": "+15125550123"},
})
print(f"\nCHECKOUT -> {checkout.status_code} {str(checkout.get_json())[:160]}")

tx = dict(conn.execute(
    "SELECT * FROM seller_transactions ORDER BY id DESC LIMIT 1").fetchone() or {})
if tx:
    md = json.loads(tx.get("metadata_json") or "{}")
    print(f"  frozen metadata_json.fulfillment = {md.get('fulfillment')!r}")
else:
    print("  no transaction row written")
    conn.execute(
        "INSERT INTO seller_transactions (buyer_user_id, seller_user_id, seller_type, item_type, "
        "item_id, amount_cents, currency, status, metadata_json, created_at, updated_at) "
        "VALUES (?,?,'marketplace','marketplace_product',?,2500,'USD','paid',?,?,?)",
        (BUYER, SELLER, listing_id,
         json.dumps({"title": "Pickup only chair",
                     "fulfillment": {"kind": "pickup", "details": {}}}), NOW, NOW))
    conn.commit()
payload = client.get("/api/pulse/orders?limit=10").get_json()
order = payload["orders"][0]

print("\nSERVED ORDER PAYLOAD")
print(f"  order.delivery_type          present? {'delivery_type' in order}")
print(f"  order.listing.delivery_type  present? "
      f"{'delivery_type' in (order.get('listing') or {})}")
print(f"  order.listing keys = {sorted((order.get('listing') or {}).keys())}")
meta = (order.get("listing") or {}).get("listing_metadata") or {}
print(f"  order.listing.listing_type   = {(order.get('listing') or {}).get('listing_type')!r}")
print(f"  order.listing.listing_metadata.delivery_options = "
      f"{meta.get('delivery_options')!r}")
print(f"  order.fulfillment_kind       present? {'fulfillment_kind' in order}")
frozen = json.loads(order.get("metadata_json") or "{}").get("fulfillment") or {}
print(f"  frozen kind on the wire      = {frozen.get('kind')!r}"
      f"  (inside metadata_json, which the serializer parses and used to ignore)")

IN_PERSON = {"pickup", "service_in_person", "booking_in_person", "event_in_person"}

# What the app used to do: read a field nothing serves.
old_input = order.get("delivery_type") or (order.get("listing") or {}).get("delivery_type")
old_variant = "pickup" if str(old_input or "").lower() in ("pickup", "local") else "shipping"
# What it does now: fold down from the lane the order itself records.
new_variant = "pickup" if str(order.get("fulfillment_kind") or "") in IN_PERSON else "shipping"

print("\nWHAT THE APP RENDERS")
print(f"  before: variantOf({old_input!r}) -> {old_variant!r}"
      f"   escrowPresentable={old_variant == 'pickup'}")
print(f"  after : variantOf({order.get('fulfillment_kind')!r}) -> {new_variant!r}"
      f"   escrowPresentable={new_variant == 'pickup'}")
print(f"  server resolved this order as {kind!r}")
print(f"\n  agrees with the server? {new_variant == ('pickup' if kind in IN_PERSON else 'shipping')}")

conn.close()
os.unlink(_DB)
