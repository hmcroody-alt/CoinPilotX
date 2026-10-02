"""A visitor's cart, driven over HTTP exactly as a browser drives it.

`services/marketplace_guest_customer.py` has its own unit tests and they cover
the identity: that an owner id is disjoint from `users`, that a revoked token
leaves a tombstone, that ids count down. What they cannot cover is the thing
that actually ships, which is six Flask routes agreeing with a cookie.

So nothing here reaches into the module. Every test is a `test_client` holding
cookies, because that is the only form of the claim that cannot rot: a refactor
that moved ownership resolution somewhere else would still have to make these
pass, and a refactor that broke it could not.

## What is being defended

Removing `_require_user()` from a cart route is, read coldly, removing an
authentication check from an endpoint that mutates state. It is the right change
and it is also exactly the shape of a mistake, so the tests are written against
the three ways it could be one:

* **Reading or editing someone else's cart.** Ownership did not go away, it
  moved from the session to a cookie. `test_a_visitor_cannot_see_another_cart`
  and the line-scoped mutation tests are the proof.
* **Minting an identity on sight.** A crawler hitting every product page must
  not leave a row behind per request. `test_reading_a_cart_allocates_nothing`
  and its siblings pin allocation to a successful add and nothing else.
* **Taking money without a name.** `/checkout` keeps its gate, and
  `test_checkout_still_refuses_a_visitor` is what keeps it kept.

## Why the cookie assertions are not decoration

The guest cookie *is* the credential. There is no password behind it and no
second factor; whoever holds it holds the cart. That makes `HttpOnly` a real
boundary rather than hygiene -- an XSS that can read `document.cookie` would
otherwise hand over carts -- and it makes "set only on success" matter, because
`_with_db` commits even when a handler returns an error, so a browser could
otherwise end up holding a key to a cart that was never created.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# Set before importing bot: the import resolves DATABASE_URL once and runs
# init_db() at module scope.
_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="guest_cart_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("COINPILOTX_DB_INIT_STARTUP_MODE", "sync")
os.environ.setdefault("FLASK_SECRET_KEY", "guest-cart-tests")

import bot  # noqa: E402
from services import marketplace_cart_routes as cart_routes  # noqa: E402
from services import marketplace_guest_customer as guest_customer  # noqa: E402

CART = "/api/pulse/marketplace/cart"
COOKIE = guest_customer.COOKIE_NAME


def cookie_value(client):
    """The guest token this client is currently holding, or ""."""
    return client.get_cookie(COOKIE).value if client.get_cookie(COOKIE) else ""


class GuestCartTestCase(unittest.TestCase):
    """One approved seller, one live listing, and nobody signed in."""

    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["SECRET_KEY"] = "guest-cart-tests"
        cls.app = bot.webhook_app

    def setUp(self):
        with self.app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                (f"gseller{id(self)}", f"gseller{id(self)}@example.com", "Guest Seller"),
            )
            self.seller_id = cur.lastrowid
            cur.execute(
                "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                (f"gmember{id(self)}", f"gmember{id(self)}@example.com", "A Member"),
            )
            self.member_id = cur.lastrowid
            cur.execute(
                "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
                "VALUES (?, ?, ?, ?)",
                (self.seller_id, "approved", "Guest Store", "Guest Store"),
            )
            cur.execute(
                """INSERT INTO marketplace_listings
                   (seller_user_id, title, description, category, price_label, currency,
                    quantity, status, approval_status, delivery_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.seller_id, "Guest Widget", "A widget, for visitors.", "Education",
                 "$19.99", "USD", 5, "active", "approved", "digital"),
            )
            self.listing_id = cur.lastrowid
            conn.commit()

        self.visitor = self.app.test_client()

    def member_client(self):
        handle = self.app.test_client()
        with handle.session_transaction() as session:
            session["account_user_id"] = self.member_id
        return handle

    def add(self, client=None, qty=1, listing_id=None):
        return (client or self.visitor).post(CART, json={
            "listing_id": listing_id if listing_id is not None else self.listing_id,
            "qty": qty,
        })

    def lines(self, client=None):
        return ((client or self.visitor).get(CART).get_json() or {}).get("lines") or []

    def guest_owner_count(self):
        """How many guest identities exist. `revoke` keeps a tombstone row, so
        this counts allocations rather than live tokens -- which is the point
        when the question is whether a rollback took one back."""
        with self.app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {guest_customer.TABLE}")
            return int(list(cur.fetchone())[0])

    # -- the capability ------------------------------------------------------

    def test_a_visitor_can_fill_a_cart_and_read_it_back(self):
        """The whole point, in four lines.

        Asserted as a round trip rather than on the add's status code: a 200
        from `cart_add` that wrote to owner `0` would pass a status check and
        hand the visitor an empty cart on the next page.
        """
        response = self.add()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["listing_id"], self.listing_id)
        self.assertEqual(lines[0]["state"], "available")

    def test_the_badge_counts_a_visitors_lines(self):
        """`badge_count` is what the header reads. A cart the visitor can list
        but that reports zero in the badge is a cart they have no reason to
        click into."""
        self.add(qty=2)
        payload = self.visitor.get(CART).get_json()
        self.assertEqual(payload["badge_count"], 2)

    def test_a_visitor_can_change_and_remove_their_own_lines(self):
        """The line-scoped routes, which resolve the owner separately from
        `cart_add` and could each have been missed on their own."""
        self.add()
        line_id = self.lines()[0]["line_id"]

        patched = self.visitor.patch(f"{CART}/{line_id}", json={"qty": 3})
        self.assertEqual(patched.status_code, 200, patched.get_data(as_text=True))
        self.assertEqual(self.lines()[0]["qty"], 3)

        removed = self.visitor.delete(f"{CART}/{line_id}")
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        self.assertEqual(self.lines(), [])

    def test_a_visitor_can_validate_their_cart(self):
        """`/validate` is the pre-flight the cart page runs before offering to
        pay. A visitor who cannot run it sees a cart that will not tell them
        whether anything in it is still for sale."""
        self.add()
        response = self.visitor.post(f"{CART}/validate", json={})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    # -- the boundary --------------------------------------------------------

    def test_a_visitor_cannot_see_another_visitors_cart(self):
        """Two browsers, two carts. The claim ownership used to make by
        requiring a session, now made by the cookie."""
        other = self.app.test_client()
        self.add()
        self.add(client=other, qty=4)

        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines()[0]["qty"], 1)
        self.assertEqual(self.lines(other)[0]["qty"], 4)
        self.assertNotEqual(cookie_value(self.visitor), cookie_value(other))

    def test_a_visitor_cannot_touch_a_line_that_is_not_theirs(self):
        """Line ids are sequential integers, so this is the enumeration case.

        Both verbs are checked and both must answer 404 rather than 403: a
        refusal that distinguished "not yours" from "no such line" would let a
        visitor map the whole table by probing it.
        """
        other = self.app.test_client()
        self.add(client=other)
        foreign = self.lines(other)[0]["line_id"]

        self.assertEqual(self.visitor.patch(f"{CART}/{foreign}", json={"qty": 9}).status_code, 404)
        self.assertEqual(self.visitor.delete(f"{CART}/{foreign}").status_code, 404)
        self.assertEqual(self.lines(other)[0]["qty"], 1)

    def test_a_visitor_cannot_read_a_members_cart(self):
        """The direction that matters most: the guest lane must not become a way
        to reach a signed-in person's cart. Their owner id spaces are disjoint by
        construction, and this is that construction asserted end to end."""
        member = self.member_client()
        self.add(client=member, qty=7)

        self.assertEqual(self.lines(), [])
        self.assertEqual(self.lines(member)[0]["qty"], 7)

    def test_a_made_up_token_is_an_empty_cart_and_not_an_error(self):
        """A forged or expired cookie resolves to owner `0`, which matches no
        row. Answering 200-and-empty rather than 4xx is deliberate: a visitor
        whose cookie was cleared by their browser has not done anything wrong,
        and an error page would be the site blaming them for it."""
        self.visitor.set_cookie(COOKIE, "not-a-real-token", domain="localhost")
        response = self.visitor.get(CART)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["lines"], [])

    def test_losing_the_cookie_loses_the_cart_and_nothing_else(self):
        """Stated rather than implied, because it is a real limitation and the
        honest place to record it is a test. The cookie is the only credential;
        clearing it is indistinguishable from being a new visitor. Nothing is
        leaked, and nothing is recoverable either -- which is part of why
        `/checkout` asks for an account before taking money."""
        self.add()
        self.assertEqual(len(self.lines()), 1)
        self.visitor.delete_cookie(COOKIE, domain="localhost")
        self.assertEqual(self.lines(), [])

    # -- allocation ----------------------------------------------------------

    def test_reading_a_cart_allocates_nothing(self):
        """A crawler sweeping the marketplace must not leave a row per request.

        `cart_list` resolves its owner with `allocate=False`, so a visitor who
        has never added anything stays anonymous in the database as well as in
        the session.
        """
        response = self.visitor.get(CART)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["lines"], [])
        self.assertEqual(cookie_value(self.visitor), "")

    def test_a_refused_add_leaves_the_visitor_without_a_cookie(self):
        """Allocation sits downstream of every refusal `cart_add` can make.

        `_with_db` commits whatever the handler returns, including an error
        response -- it only rolls back on a raised exception. So an owner
        allocated on the way *in* would outlive the refusal, and the visitor
        would walk away holding a key to a cart they were never allowed to
        fill. The route resolves the owner after the last content check
        instead, which is why there is nothing here to clean up.
        """
        response = self.add(listing_id=10_000_000)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(cookie_value(self.visitor), "")

    def test_an_add_that_crashes_after_allocating_hands_out_no_cookie(self):
        """The other half, and the one the status check in the hook is for.

        Allocation is downstream of every *refusal*, but not of every failure:
        the INSERT and the read-back after it can still raise. `_with_db`
        rolls back when they do, which un-allocates the owner -- while the
        token is already sitting in `g`, and Flask runs after-request hooks on
        the 500 it builds from the exception. Without the status check the
        browser would keep a cookie naming a row that no longer exists, and
        every later request would resolve it to nobody and silently behave
        like a first visit.
        """
        before = self.guest_owner_count()
        propagate = self.app.config.get("PROPAGATE_EXCEPTIONS")
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            with mock.patch.object(
                cart_routes, "_serialize_lines", side_effect=RuntimeError("boom")
            ):
                response = self.add()
        finally:
            self.app.config["PROPAGATE_EXCEPTIONS"] = propagate

        self.assertEqual(response.status_code, 500)
        self.assertEqual(cookie_value(self.visitor), "")
        self.assertEqual(self.guest_owner_count(), before)

    def test_the_cookie_is_issued_once_and_then_reused(self):
        """A second add must land in the first cart.

        A route that re-allocated per request would pass every "a visitor can
        add" test above while giving the visitor a fresh empty cart each time
        -- the lines would be in the database and none of them together.
        """
        self.add()
        first = cookie_value(self.visitor)
        self.assertNotEqual(first, "")

        self.add(qty=2)
        self.assertEqual(cookie_value(self.visitor), first)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines()[0]["qty"], 3)

    def test_a_member_is_never_given_a_guest_cookie(self):
        """Signing in is not supposed to mint a second identity beside the
        first. A member's lines belong to their `users.id`, and a guest cookie
        riding along would be a second cart waiting to diverge from it."""
        member = self.member_client()
        self.add(client=member)
        self.assertEqual(cookie_value(member), "")

    def test_the_cookie_is_httponly(self):
        """The cookie *is* the credential -- there is nothing else behind it --
        so script-readability is the difference between an XSS that defaces a
        page and one that harvests carts."""
        self.add()
        self.assertTrue(self.visitor.get_cookie(COOKIE).http_only)

    # -- the line that is still drawn ----------------------------------------

    def test_checkout_still_refuses_a_visitor(self):
        """The deliberate exception, and the reason the six openings above are
        defensible. A cart holds an intention; checkout takes money, and
        PulseSoc wants a name before it does.

        Asserted on the status code rather than the body because the refusal is
        the contract. What the page *says* about it is
        `marketplace_cart_web.group_lines`'s business, and is pinned there.
        """
        self.add()
        response = self.visitor.post(f"{CART}/checkout", json={
            "seller_user_id": self.seller_id})
        self.assertEqual(response.status_code, 401)

    def test_the_cart_tells_a_visitor_about_the_one_refusal_that_is_left(self):
        """And it must be told *before* the click, not after.

        The group carries `sign_in_required` so the page can render a sign-in
        link instead of a pay button. A cart that offered to check out and then
        401'd would be the same broken promise this whole mission exists to
        remove, moved one page later.
        """
        self.add()
        group = self.visitor.get(CART).get_json()["groups"][0]
        self.assertTrue(group["sign_in_required"])
        self.assertFalse(group["checkoutable"])
        self.assertIn("Sign in", group["reason"])

    def test_a_member_is_told_no_such_thing(self):
        """The mirror, so the flag is proven to be about the asker rather than
        permanently on."""
        member = self.member_client()
        self.add(client=member)
        group = member.get(CART).get_json()["groups"][0]
        self.assertFalse(group["sign_in_required"])
        self.assertTrue(group["checkoutable"])
        self.assertEqual(group["reason"], "")


if __name__ == "__main__":
    unittest.main()
