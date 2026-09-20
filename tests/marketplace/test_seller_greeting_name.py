"""Whether a payment email greets the seller by name, or by nobody.

The name lookups selected ``id`` and ``first_name`` from ``users``, which has
neither — it has ``user_id``, ``full_name``, ``display_name`` and ``username``.
Both lookups catch the resulting error and return empty, and every template
renders ``ctx.get('seller_first_name') or 'there'``, so the failure arrived as
a well-formed email that said "Hi there" and nothing else looked wrong.

These tests therefore assert on the *rendered body*, not on the lookup's return
value: an empty dict and a populated one both produce a valid email, and only
the text distinguishes them. ``test_blank_name_still_greets`` is the negative
control that proves the assertion can fail.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="seller_greeting_name_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_payout_scheduler  # noqa: E402
from services import payments_email_templates as templates  # noqa: E402

FULL_NAME = 80801
DISPLAY_ONLY = 80802
USERNAME_ONLY = 80803
BLANK = 80804
ABSENT = 80899

#: Enough context for a dispute email to render; the greeting is the variable.
DISPUTE_CONTEXT = {
    "order_id": "55",
    "order_reference": "#55",
    "amount_cents": 2500,
    "currency": "USD",
    "dispute_id": "dp_1",
    "dispute_reason": "fraudulent",
    "evidence_due_by": "2026-10-01",
    "store_name": "Cherie Goods",
}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    bot.init_db()
    conn = bot.db()
    for user_id, username, full_name, display_name in (
        (FULL_NAME, "mcherie", "Cherie Roody", "cherie's store"),
        (DISPLAY_ONLY, "handleco", None, "Dana Fox"),
        (USERNAME_ONLY, "lonewolf", None, None),
        (BLANK, "   ", "   ", "  "),
    ):
        conn.execute(
            "INSERT INTO users (user_id, username, full_name, display_name) VALUES (?,?,?,?)",
            (user_id, username, full_name, display_name),
        )
    conn.commit()
    conn.close()
    yield
    os.unlink(_DB)


def test_names_resolve_against_the_real_columns():
    names = bot.seller_first_names([FULL_NAME, DISPLAY_ONLY, USERNAME_ONLY, BLANK, ABSENT])
    assert names[FULL_NAME] == "Cherie"
    assert names[DISPLAY_ONLY] == "Dana"
    assert names[USERNAME_ONLY] == "lonewolf"
    assert names[BLANK] == ""
    assert ABSENT not in names


def test_full_name_wins_over_display_name():
    """display_name is free-form — often a handle or a store name, not a person."""
    assert bot.seller_first_names([FULL_NAME])[FULL_NAME] == "Cherie"


def test_rendered_dispute_email_contains_the_name():
    names = bot.seller_first_names([FULL_NAME])
    body = str(templates.render(
        "dispute_opened", dict(DISPUTE_CONTEXT, seller_first_name=names[FULL_NAME])))
    assert "Cherie" in body
    assert "Hi there" not in body


def test_blank_name_still_greets():
    """Negative control: "Hi there" is reachable, so the assertions above can fail."""
    body = str(templates.render("dispute_opened", dict(DISPUTE_CONTEXT, seller_first_name="")))
    assert "Hi there" in body


class _Capture:
    """Stands in for emit_payment_notification and keeps what it was handed."""

    def __init__(self):
        self.calls = []

    def __call__(self, event, user_id, context=None, email_only=False):
        self.calls.append((event, int(user_id or 0), dict(context or {})))
        return {"ok": True}

    def context_for(self, event):
        return next(ctx for name, _, ctx in self.calls if name == event)


def test_paid_order_emails_name_both_sides(monkeypatch):
    """The buyer's copy and the seller's copy greet different people."""
    capture = _Capture()
    monkeypatch.setattr(bot, "emit_payment_notification", capture)
    bot.emit_marketplace_paid_order_emails([
        {"id": 5501, "buyer_user_id": DISPLAY_ONLY, "seller_user_id": FULL_NAME,
         "amount_cents": 2500, "currency": "usd", "seller_net_cents": 2000},
    ])
    assert capture.context_for("payment_succeeded")["buyer_first_name"] == "Dana"
    assert capture.context_for("new_paid_order")["seller_first_name"] == "Cherie"


def test_paid_order_batch_resolves_names_in_one_query(monkeypatch):
    """A large charge is a batch; a lookup per row would exhaust the pool."""
    capture = _Capture()
    monkeypatch.setattr(bot, "emit_payment_notification", capture)
    queries = []
    real = bot.seller_first_names

    def counting(user_ids):
        queries.append(list(user_ids))
        return real(user_ids)

    monkeypatch.setattr(bot, "seller_first_names", counting)
    bot.emit_marketplace_paid_order_emails([
        {"id": 5000 + n, "buyer_user_id": DISPLAY_ONLY, "seller_user_id": FULL_NAME,
         "amount_cents": 100, "currency": "usd", "seller_net_cents": 90}
        for n in range(6)
    ])
    assert len(queries) == 1, queries
    assert len(capture.calls) == 12
    assert all(ctx.get("seller_first_name") == "Cherie"
               for name, _, ctx in capture.calls if name == "new_paid_order")


def test_payout_failed_from_the_scheduler_carries_a_name(monkeypatch):
    """The scheduler cannot import bot, so it resolves the name on its own."""
    from services import marketplace_payout_scheduler as scheduler
    from services import payments_notifications

    sent = []
    monkeypatch.setattr(payments_notifications, "emit",
                        lambda event, user_id, ctx: sent.append((event, user_id, dict(ctx))))
    scheduler._notify_seller(
        {"seller_id": str(FULL_NAME), "seller_transaction_id": 9001,
         "net_seller_earnings_minor": 2000, "currency": "usd", "order_id": "55"},
        {"failure_code": "account_closed"}, attempt=1)
    assert sent, "the scheduler sent nothing"
    assert sent[0][2]["seller_first_name"] == "Cherie"
    body = str(templates.render("payout_failed", dict(DISPUTE_CONTEXT, **sent[0][2])))
    assert "Cherie" in body
    assert "Hi there" not in body


def test_scheduler_and_bot_derive_the_same_name():
    """Two call sites, one rule — they must not drift apart."""
    from services import payments_notifications
    for user_id, expected in ((FULL_NAME, "Cherie"), (DISPLAY_ONLY, "Dana"),
                              (USERNAME_ONLY, "lonewolf"), (BLANK, "")):
        assert marketplace_payout_scheduler._greeting_first_name(user_id) == expected
        assert bot.seller_first_names([user_id])[user_id] == expected
    assert payments_notifications.greeting_first_name(None) == ""


def test_application_email_greets_the_applicant_by_name():
    """seller_approved and seller_declined come through this lookup, not seller_first_names."""
    conn = bot.db()
    try:
        context = bot.seller_application_email_context(
            conn.cursor(),
            {"id": 77, "user_id": FULL_NAME, "business_name": "Cherie Goods", "status": "approved"},
        )
    finally:
        conn.close()
    assert context["seller_first_name"] == "Cherie"
    for template in ("seller_approved", "seller_declined"):
        body = str(templates.render(template, dict(DISPUTE_CONTEXT, **context)))
        assert "Cherie" in body, template
        assert "Hi there" not in body, template
