"""Probe: which timeline does each of the eleven fulfilment kinds get?

Gap 11 made `fulfillment_kind` reachable by the orders dashboard, and folded the
timeline choice down from it. But `OrderTimelineVariant` is binary — `"shipping"
| "pickup"` — so `variantOf` sorts eleven kinds into two strips, and everything
that is not one of the four in-person kinds lands on the parcel strip.

That means a buyer who downloaded a file is told "Being packed", then "On its
way", then "Delivered"; and a seller of that download is offered "Mark packed"
and "Mark shipped", the latter disabled with "Add a tracking number before
marking this order shipped" — a precondition a digital delivery can never meet.

This publishes one listing per lane through the real create route, buys each one
through the real checkout route, and prints the kind the server froze next to the
words the app would put on the screen. Run it before and after the fix.
"""
import json
import logging
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_H, _DB = tempfile.mkstemp(suffix=".db", prefix="order_timeline_probe_")
os.close(_H)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_fulfillment as mf  # noqa: E402

logging.disable(logging.CRITICAL)  # boot chatter would bury the one table that matters

SELLER, BUYER = 99811, 99812
NOW = "2026-09-12T00:00:00"

bot.init_db()
conn = sqlite3.connect(_DB)
conn.row_factory = sqlite3.Row
conn.execute(
    "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
    "VALUES (?,'approved','Timeline Store',?,?)", (SELLER, NOW, NOW))
conn.commit()

bot.webhook_app.config["TESTING"] = True
client = bot.webhook_app.test_client()


def cover():
    conn.execute(
        "INSERT INTO marketplace_product_media (merchant_id, product_id, media_url, media_type, "
        "is_cover, moderation_status, created_at) VALUES (?,0,?,'image',1,'approved',?)",
        (SELLER, "https://cdn.example/c.jpg", NOW))
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def publish(title, listing_type, metadata):
    bot.api_account_user = lambda *a, **k: {"user_id": SELLER, "username": "probe_seller"}
    resp = client.post("/api/pulse/marketplace/listings/create", json={
        "title": title, "description": "d" * 40, "short_description": "s",
        "category": "Home", "price": "25.00", "currency": "USD", "quantity": 5,
        "product_type": listing_type, "listing_type": listing_type,
        "submission_action": "submit", "media_ids": [cover()],
        "listing_metadata": metadata,
    })
    assert resp.status_code == 200, (title, resp.get_json())
    row = dict(conn.execute(
        "SELECT * FROM marketplace_listings ORDER BY id DESC LIMIT 1").fetchone())
    conn.execute(
        "UPDATE marketplace_listings SET status='published', approval_status='approved', "
        "price_label='$25.00' WHERE id=?", (int(row["id"]),))
    conn.commit()
    return int(row["id"]), row


def buy(listing_id, details, choice=None):
    bot.api_account_user = lambda *a, **k: {"user_id": BUYER, "username": "probe_buyer"}
    body = {"item_type": "marketplace_product", "item_id": listing_id, "quantity": 1,
            "payment_mode": "cash", "fulfillment_details": details}
    if choice:
        body["fulfillment_choice"] = choice
    return client.post("/api/pulse/payments/checkout", json=body)


CONTACT = {"contact_name": "Probe Buyer", "contact_phone": "+15125550123"}
WHEN = {"scheduled_date": "2026-10-01", "scheduled_time": "14:00", "timezone": "UTC"}
ADDRESS = {"address_line1": "1 Main St", "address_city": "Austin",
           "address_region": "TX", "address_postal_code": "78701", "address_country": "US"}

LANES = [
    ("physical shipping", "physical", {"condition": "new", "location": "Testville",
                                       "delivery_options": "shipping"},
     {**CONTACT, **ADDRESS}, None),
    ("physical pickup", "physical", {"condition": "new", "location": "Testville",
                                     "delivery_options": "pickup"}, CONTACT, None),
    ("digital download", "digital", {"license": "personal", "delivery": "automatic"}, {}, None),
    ("service remote", "service", {"pricing_mode": "fixed", "delivery_time_days": 3,
                                   "service_location": "remote"},
     {**CONTACT, **WHEN}, None),
    ("service in person", "service", {"pricing_mode": "fixed", "delivery_time_days": 3,
                                      "service_location": "in_person"},
     {**CONTACT, **WHEN, **ADDRESS}, None),
    ("event online", "event", {"venue_mode": "online", "event_date": "2026-10-05",
                               "online_url": "https://example.com/live"},
     {"attendee_name": "Probe Buyer"}, None),
    ("event in person", "event", {"venue_mode": "in_person", "event_date": "2026-10-05",
                                  "location": "Austin"},
     {"attendee_name": "Probe Buyer"}, None),
    # The seller's vocabulary here is audio/video/in_person; anything that is not
    # in_person resolves to booking_remote.
    ("booking remote", "booking", {"meeting_mode": "video"}, {**CONTACT, **WHEN}, None),
    ("booking in person", "booking", {"meeting_mode": "in_person"},
     {**CONTACT, **WHEN, **ADDRESS}, None),
]

IN_PERSON = {"pickup", "service_in_person", "booking_in_person", "event_in_person"}
SHIPPING_BUYER = ["Order placed", "Being packed", "On its way", "Delivered"]
PICKUP_BUYER = ["Reserved", "Pickup scheduled", "Picked up", "Complete"]

print(f"{'lane published':<20} {'server kind':<20} {'served':<20} {'strip':<9} buyer sees")
print("-" * 110)

rows = []
for title, listing_type, metadata, details, choice in LANES:
    listing_id, row = publish(title, listing_type, metadata)
    server_kind = mf.resolve_kind(row["listing_type"] or row["product_type"],
                                  row["delivery_type"],
                                  json.loads(row["listing_metadata_json"] or "{}"))
    resp = buy(listing_id, details, choice)
    if resp.status_code != 200:
        print(f"{title:<20} {server_kind:<20} CHECKOUT {resp.status_code} "
              f"{str(resp.get_json())[:60]}")
        continue
    payload = client.get("/api/pulse/orders?limit=1").get_json()
    order = payload["orders"][0]
    served = order.get("fulfillment_kind")
    variant = "pickup" if str(served or "") in IN_PERSON else "shipping"
    steps = PICKUP_BUYER if variant == "pickup" else SHIPPING_BUYER
    rows.append((title, server_kind, served, variant))
    print(f"{title:<20} {server_kind:<20} {str(served):<20} {variant:<9} "
          f"{' -> '.join(steps)}")

print("\nEvery kind the server can resolve:")
for kind in mf.KINDS:
    variant = "pickup" if kind in IN_PERSON else "shipping"
    print(f"  {kind:<22} -> {variant}")

goods_travel = {"shipping", "shipping_or_pickup"}
wrong = [k for k in mf.KINDS if k not in IN_PERSON and k not in goods_travel]
print(f"\nKinds put on the parcel strip that ship no parcel: {wrong}")
print("These orders are told 'Being packed' and 'On its way', and their sellers")
print("are asked for a tracking number they can never have.")

conn.close()
os.unlink(_DB)
