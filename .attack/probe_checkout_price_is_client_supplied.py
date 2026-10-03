"""Who decides what a lesson or a live class costs?

`/api/pulse/payments/checkout` accepts four `item_type` values and prices them
from three different places:

    marketplace_product -> marketplace_price_authority.resolve_for_listing(...)
    course              -> parse_price_label_to_cents(item["price_label"])   # the DB row
    lesson              -> parse_price_label_to_cents(payload["price_label"]) # the REQUEST
    live_class          -> parse_price_label_to_cents(payload["price_label"]) # the REQUEST

The last two read the buyer's own request body. `payload` is
`request.get_json(silent=True)`, and both rows are loaded from the database one
line earlier and then not consulted for price -- because `pulse_lessons` and
`pulse_live_classes` have **no price column at all**:

    pulse_lessons      id course_id teacher_user_id title description lesson_type
                       content_body media_url resource_url status scheduled_at
                       safety_score created_at updated_at
    pulse_live_classes id teacher_user_id course_id title description category
                       scheduled_at replay_url status attendance_count
                       created_at updated_at

So this is not two price authorities drifting apart. For these two item types
there is no server-side price to drift *from*. A comment in the route confirms
the consequence reaches the provider: *"the Session above is created for every
item_type this route accepts"*, and that Session carries
`unit_amount: amount_cents`.

Static reading is enough to see it, and not enough to publish it, so this
captures the number on its way to Stripe.

THE CONTROL IS THE POINT. The same probe drives the `course` branch, which
reads `price_label` from the database row. If a changed payload moves the
charged amount for `live_class` and leaves `course` untouched, the probe is
measuring price provenance. If it moves both, the probe is broken -- I would be
looking at a payload field that happens to be echoed, not at the price
authority.

Nothing here touches production, Stripe, or money: `stripe.checkout.Session.create`
is replaced with a capture, the DB is `.attack/scratch.db`, and no network call
is made. Authentication and the teacher-approval gate are stubbed because the
hole is *behind* them -- it is reachable by any logged-in buyer, and stubbing
them is what isolates the price question from the access question.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, ".attack", "scratch.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + DB)

import bot  # noqa: E402

logging.disable(logging.CRITICAL)

TEACHER_ID = 424242
CLASS_ID = 919191
COURSE_ID = 919192
LESSON_ID = 919193

#: What the teacher actually charges, in the only place a course can record it.
COURSE_TRUE_LABEL = "$250.00"


def seed():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_live_classes WHERE id=?", (CLASS_ID,))
    cur.execute("DELETE FROM pulse_courses WHERE id=?", (COURSE_ID,))
    cur.execute("DELETE FROM pulse_lessons WHERE id=?", (LESSON_ID,))
    # Without this the ledger cross-check below can read rows left by an earlier
    # run. That is exactly how the first version of this probe reported "never
    # reached the provider" while printing four convincing amounts.
    cur.execute("DELETE FROM seller_transactions WHERE item_id IN (?,?,?)",
                (CLASS_ID, COURSE_ID, LESSON_ID))
    cur.execute(
        "INSERT INTO pulse_live_classes (id, teacher_user_id, title, description, "
        "status, created_at, updated_at) VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (CLASS_ID, TEACHER_ID, "Masterclass: Advanced Trading", "A paid live class.", "scheduled"),
    )
    cols = {r[1] for r in cur.execute("PRAGMA table_info(pulse_courses)")}
    cur.execute(
        "INSERT INTO pulse_courses (id, teacher_user_id, title, price_label, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (COURSE_ID, TEACHER_ID, "Course: Advanced Trading", COURSE_TRUE_LABEL, "published"),
    )
    cur.execute(
        "INSERT INTO pulse_lessons (id, course_id, teacher_user_id, title, description, "
        "status, created_at, updated_at) VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (LESSON_ID, COURSE_ID, TEACHER_ID, "Lesson: Advanced Trading", "A paid lesson.",
         "published"),
    )
    conn.commit()
    conn.close()
    return cols


class FakeSession(dict):
    """Stands in for a Stripe Session so the route can finish normally.

    The first version of this raised instead, to carry the amount out. The
    route's own `except` swallowed it and answered 500, so the request never
    reached the ledger write and the probe reported "never reached the
    provider" while quietly measuring nothing -- the same failure shape as a
    control that cannot fail. Returning a session lets the route complete, which
    is what makes the `seller_transactions` reading below trustworthy.
    """

    def __init__(self):
        super().__init__(id="cs_test_probe", url="https://stripe.invalid/probe",
                         payment_intent="pi_test_probe")
        self.id = "cs_test_probe"
        self.url = "https://stripe.invalid/probe"
        self.payment_intent = "pi_test_probe"


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True

    cols = seed()
    print("=" * 90)
    print("WHO SETS THE PRICE AT /api/pulse/payments/checkout ?")
    print("=" * 90)
    print(f"  pulse_courses has a price_label column : {'price_label' in cols}")
    conn = sqlite3.connect(DB)
    for table in ("pulse_lessons", "pulse_live_classes"):
        tcols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        priceish = sorted(c for c in tcols if "price" in c or "amount" in c or "cost" in c)
        print(f"  {table:20s} price-like columns  : {priceish or 'NONE'}")
    conn.close()
    print()

    captured = {}
    sent_to_stripe = []

    def fake_create(**kwargs):
        sent_to_stripe.append(kwargs)
        return FakeSession()

    real_account_user = bot.api_account_user
    real_approved = bot.approved_teacher_for_user
    real_create = bot.stripe.checkout.Session.create
    real_key = bot.STRIPE_SECRET_KEY

    bot.api_account_user = lambda *a, **k: {"user_id": 1, "email": "buyer@example.test",
                                            "username": "buyer"}
    bot.approved_teacher_for_user = lambda *a, **k: True
    bot.stripe.checkout.Session.create = fake_create
    # `if not marketplace_cash_payment and not STRIPE_SECRET_KEY` returns 503
    # before the Session is built, so with no key configured the first run of
    # this probe could not see the amount at all. This is a configuration
    # constant, not a price authority, and `Session.create` is already a capture
    # -- so no key exists, no network call is made, and no money can move.
    bot.STRIPE_SECRET_KEY = "sk_test_probe_never_sent_anywhere"

    try:
        client = app.test_client()
        for item_type, item_id, offered in (
            ("live_class", CLASS_ID, "$0.50"),
            ("live_class", CLASS_ID, "$5000.00"),
            ("lesson", LESSON_ID, "$0.50"),
            ("lesson", LESSON_ID, "$5000.00"),
            ("course", COURSE_ID, "$0.50"),
            ("course", COURSE_ID, "$5000.00"),
        ):
            body = {"item_type": item_type, "item_id": item_id, "price_label": offered}
            before = len(sent_to_stripe)
            r = client.post("/api/pulse/payments/checkout", json=body)
            amount = None
            if len(sent_to_stripe) > before:
                price_data = sent_to_stripe[-1]["line_items"][0]["price_data"]
                amount = price_data["unit_amount"]
                note = f"HTTP {r.status_code}; Stripe asked for unit_amount={amount}"
            else:
                note = f"HTTP {r.status_code} {r.get_data(as_text=True)[:90]}"
            captured.setdefault(item_type, []).append((offered, amount))
            print(f"  {item_type:12s} buyer sends price_label={offered:10s} -> {note}")
    finally:
        bot.api_account_user = real_account_user
        bot.approved_teacher_for_user = real_approved
        bot.stripe.checkout.Session.create = real_create
        bot.STRIPE_SECRET_KEY = real_key

    # Second, independent reading. `seller_transactions` is INSERTed with
    # `amount_cents` *before* the Stripe guard, so the ledger records the price
    # whether or not the provider is ever reached. If the captured amount and the
    # recorded amount agree, the number is not an artifact of my interception.
    print()
    print("  LEDGER CROSS-CHECK -- what seller_transactions recorded:")
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT item_type, item_id, amount_cents, currency, status FROM seller_transactions "
        "WHERE item_id IN (?,?,?) ORDER BY id", (CLASS_ID, COURSE_ID, LESSON_ID)).fetchall()
    conn.close()
    for item_type, item_id, amount, currency, status in rows:
        print(f"      {item_type:12s} item={item_id} amount_cents={amount} "
              f"{currency} status={status}")

    print()
    print("=" * 90)
    for item_type, rows in captured.items():
        amounts = {a for _o, a in rows if a is not None}
        if len(amounts) > 1:
            print(f"  {item_type:12s} BUYER CONTROLS THE CHARGE -- amounts seen: "
                  f"{sorted(amounts)} <<<")
        elif len(amounts) == 1:
            print(f"  {item_type:12s} server-priced: every payload charged "
                  f"{amounts.pop()} regardless of what the buyer asked for")
        else:
            print(f"  {item_type:12s} never reached the provider; see the notes above")
    print("=" * 90)
    print("  A differing pair on live_class with a constant course is the finding.")
    print("  A differing pair on BOTH means this probe is reading an echo, not a price.")


if __name__ == "__main__":
    main()
