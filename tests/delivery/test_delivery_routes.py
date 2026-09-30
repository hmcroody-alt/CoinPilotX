"""The endpoint is the boundary, and three of its properties are load-bearing.

What this file is defending
---------------------------
* **``fulfillment`` is never taken from a request.** §47-48 makes it a declared
  fact. A request parameter for it would let anyone turn a seller-posted item
  into a supplier-fulfilled one and get a warehouse-to-door window for a parcel
  the seller is about to hand to the post office -- and spend a CJ call per
  request doing it. The test for this sends a body that names ``SUPPLIER`` for a
  listing the source row declares ``SELLER``, and asserts the declaration wins.
  A test that sent no ``fulfillment`` at all would pass against a route that
  read one.
* **The buyer's identity is never taken from a request.** It is only used to read
  this buyer's past shipping addresses, so a parameter would make one visitor's
  order history readable-by-effect to any other, one country at a time. Same
  shape of test: a body naming a *different* user id, asserted to lose to the
  session.
* **Only the buyer half is serialized.** The internal half carries the freight
  PulseSoc pays CJ, which §33-35 forbids showing a shopper. The mistake that
  ships it is one character wide -- ``result`` instead of ``result["buyer"]`` --
  and it raises nothing, renders fine, and is invisible in a screenshot. The test
  walks the whole JSON response for any value the internal half holds.

Two further properties are asserted because they were decided against a tempting
alternative and nothing else would notice them changing: that the caller's
asserted country enters as ``STATED`` and never as ``CHECKOUT`` (which would let
a request body claim postal precision), and that ``now_at`` is handed over as
``policy.now_at`` itself rather than as a moment this process computed in UTC.

No ``bot`` import
-----------------
The route is exercised on a bare ``Flask`` app with the blueprint registered, and
``delivery_routes._bot`` is replaced by a fake. Importing the real ``bot`` boots a
132k-line monolith and runs ``init_db()``, which would make this the slowest file
in a package that currently runs in about a second. What that costs is the URL
map: these tests do not prove the pack is registered. ``test_the_pack_is_loaded_by_bot``
below asserts that from the source of ``bot.py`` instead, which is the only part
of it that matters here.
"""

import json
import os
import sys
import tempfile

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-delivery-routes-", suffix=".db",
                                  delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import delivery_routes  # noqa: E402
from services.delivery import destination as destinations  # noqa: E402
from services.delivery import entry, estimate, listing, policy, quote, store  # noqa: E402
from services.delivery import variant_facts  # noqa: E402

REF = "8801:blue"
BUYER = 4242
OTHER_BUYER = 9999

SUPPLIER_TRIPLE = {"connection_id": "conn-1", "business_id": "biz-1",
                   "store_id": "store-1"}

#: What the composed answer looks like, in both halves. Built from `quote`'s own
#: composition rather than typed out, so a field moving from one half to the other
#: moves here too -- a hand-written pair would keep asserting against the old
#: split and the leak test would stop watching the field that moved.
ANSWER = quote._compose(
    estimate.arrival_window(
        transit={"min_days": 6, "max_days": 11, "basis": estimate.BASIS_BUSINESS},
        handling={"min_days": 1, "max_days": 2},
        buffer_days=1,
        now=policy.now_at("CN", clock=lambda: __import__("datetime").datetime(
            2026, 3, 4, 10, 0, tzinfo=__import__("datetime").timezone.utc)),
    ),
    source="PROVIDER",
    route={"option_id": "cj-packet", "provider_total": "7.43", "currency": "USD"},
)


class FakeBot:
    """Only the two members this route reaches for."""

    def __init__(self):
        self.user = None
        self.admin_denied = None

    def api_account_user(self):
        return self.user

    def require_admin_api(self, scope):
        self.scope = scope
        return (None, self.admin_denied) if self.admin_denied else ({"id": 1}, None)


FAKE_BOT = FakeBot()

#: Every call `entry.delivery_for_variant` received, as its kwargs. The route's
#: whole job is composition, so what it passed down is most of what there is to
#: assert about it.
CALLS: list = []

DECLARED = {}


def _fake_entry(**kwargs):
    CALLS.append(kwargs)
    return ANSWER


def _fake_declaration(variant_ref, **_kwargs):
    if DECLARED.get("raise"):
        raise variant_facts.VariantRefInvalid("not a listing id")
    return {k: v for k, v in DECLARED.items() if k != "raise"}


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    CALLS.clear()
    DECLARED.clear()
    DECLARED.update(visible=True, fulfillment=quote.FULFILLMENT_SUPPLIER,
                    supplier=dict(SUPPLIER_TRIPLE))
    FAKE_BOT.user = None
    FAKE_BOT.admin_denied = None
    monkeypatch.setattr(delivery_routes, "_bot", lambda: FAKE_BOT)
    monkeypatch.setattr(delivery_routes.listing, "declaration", _fake_declaration)
    monkeypatch.setattr(delivery_routes.entry, "delivery_for_variant", _fake_entry)
    yield


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    delivery_routes.register(app)
    return app.test_client()


def post(client, **body):
    body.setdefault("variant_ref", REF)
    return client.post("/api/pulse/delivery/estimate", json=body)


# The two inputs a request may not decide


def test_the_body_cannot_declare_who_ships_it(client):
    """The listing says SELLER; the body says SUPPLIER. The listing wins.

    Asserted with the body pointing the *other* way on purpose. A test that
    omitted `fulfillment` would pass against a route that read one whenever it
    was present, which is every request an attacker would send.
    """
    DECLARED["fulfillment"] = quote.FULFILLMENT_SELLER
    DECLARED["supplier"] = None
    assert post(client, fulfillment=quote.FULFILLMENT_SUPPLIER).status_code == 200
    assert CALLS[0]["fulfillment"] == quote.FULFILLMENT_SELLER


def test_the_body_cannot_choose_whose_order_history_resolves_the_country(client,
                                                                        monkeypatch):
    """Session identity only. A body naming another user id must not reach resolve."""
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return destinations._record(destinations.TIER_NONE, None, None)

    monkeypatch.setattr(delivery_routes.destinations, "resolve", spy)
    FAKE_BOT.user = {"id": BUYER}
    post(client, buyer_user_id=OTHER_BUYER, user_id=OTHER_BUYER)
    assert seen["buyer_user_id"] == BUYER


def test_an_anonymous_visitor_is_a_buyer_of_none_and_not_an_error(client):
    """A product page is public and most of its traffic has no session."""
    FAKE_BOT.user = None
    assert post(client).status_code == 200


# The asserted country enters at the tier it was actually asserted at


def test_the_asserted_country_enters_as_stated_and_never_as_checkout(client,
                                                                    monkeypatch):
    """``CHECKOUT`` is the only tier ``POSTAL_TIERS`` lets carry a postal code.

    Passing the request body as the checkout tier would promote a caller's
    assertion into postal precision the buyer never confirmed for this purchase,
    and that precision goes into a cache key. There is no behavioural difference
    while the body carries no postal, which is exactly why it is asserted here.
    """
    seen = {}
    monkeypatch.setattr(delivery_routes.destinations, "resolve",
                        lambda **kw: (seen.update(kw),
                                      destinations._record(destinations.TIER_NONE,
                                                           None, None))[1])
    post(client, destination={"country": "DE", "postal": "10115"})
    assert seen["stated"] == {"country": "DE"}
    assert seen.get("checkout") is None


def test_a_bare_country_string_is_accepted_too(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(delivery_routes.destinations, "resolve",
                        lambda **kw: (seen.update(kw),
                                      destinations._record(destinations.TIER_NONE,
                                                           None, None))[1])
    post(client, country="GB")
    assert seen["stated"] == {"country": "GB"}


def test_the_resolved_destination_reaches_entry_and_is_reported_back(client):
    got = post(client, country="de")
    assert CALLS[0]["destination"]["country"] == "DE"
    body = got.get_json()
    assert body["destination"] == {"country": "DE",
                                  "precision": destinations.PRECISION_COUNTRY,
                                  "tier": destinations.TIER_STATED, "known": True}


def test_the_postal_code_is_not_echoed_back(client):
    """A stated destination never carries one, and returning an address fragment
    the client already has is a habit rather than a decision."""
    body = post(client, destination={"country": "DE", "postal": "10115"}).get_json()
    assert "postal" not in body["destination"]
    assert "10115" not in json.dumps(body)


# What comes back


def test_only_the_buyer_half_is_serialized(client):
    """The internal half holds the freight PulseSoc pays. §33-35.

    Walked over the whole JSON rather than checked key by key: the mistake is
    `result` instead of `result["buyer"]`, and that nests the forbidden values one
    level down where a top-level key check would not see them.
    """
    body = post(client).get_json()
    assert body["delivery"] == ANSWER["buyer"]
    text = json.dumps(body)
    assert "internal" not in body
    # Every key of the internal half, by name. This is the half that catches the
    # one-character mistake wherever it nests the values.
    for key in ANSWER["internal"]:
        assert key not in text, f"{key} belongs to the internal half"
    # And the values that would be an actual disclosure rather than an
    # untidiness: what the platform pays, in what currency, over which route.
    #
    # Swept by value and not by key because the leak worth catching is one where
    # the field arrives under some other name. Deliberately not every internal
    # value: `source` is `"PROVIDER"`, which is a substring of the buyer half's
    # own `confidence` vocabulary (`PROVIDER_QUOTED`), so sweeping it would fail
    # on a collision rather than on a leak -- and `source` is already covered by
    # the key check above.
    route = ANSWER["internal"]["route"]
    for secret in (ANSWER["internal"]["freight_total"],
                   ANSWER["internal"]["freight_currency"],
                   route["option_id"]):
        assert str(secret) not in text, f"{secret!r} is the platform's business"


def test_the_answer_is_never_stored_by_an_intermediary(client):
    """This domain already has a cache with a chosen freshness policy; a proxy
    caching the composed response would add a second, dumber layer on top."""
    got = post(client)
    assert "no-store" in got.headers["Cache-Control"]


def test_the_estimate_is_never_a_guarantee(client):
    """§58 and §125. Held here as well as in `quote` because this is the surface
    a client reads, and a buyer half that lost the field would be an omission the
    renderer could not see."""
    body = post(client).get_json()
    assert body["delivery"]["guaranteed"] is False
    assert body["delivery"]["is_estimate"] is True
    assert body["delivery"]["shipping_price"] == quote.SHIPPING_FREE


# Refusals, and the shape each one has


def test_an_invisible_listing_is_a_404_and_says_no_more_than_that(client):
    """Same answer for a draft as for a listing that does not exist.

    `listing.declaration` conflates those two deliberately, and a different
    status for "exists but you cannot see it" would be an oracle for enumerating
    other sellers' drafts.
    """
    DECLARED.update(visible=False, fulfillment=None, supplier=None)
    got = post(client)
    assert got.status_code == 404
    assert got.get_json()["reason"] == delivery_routes.REASON_LISTING_UNAVAILABLE
    assert CALLS == [], "an invisible listing must not cost a supplier call"


def test_a_malformed_reference_is_the_callers_mistake(client):
    DECLARED["raise"] = True
    got = post(client, variant_ref="not-a-listing")
    assert got.status_code == 400
    assert got.get_json()["reason"] == delivery_routes.REASON_REF_INVALID


def test_a_missing_reference_is_refused_before_anything_is_read(client):
    got = client.post("/api/pulse/delivery/estimate", json={})
    assert got.status_code == 400
    assert got.get_json()["reason"] == delivery_routes.REASON_BODY_INVALID
    assert CALLS == []


def test_a_body_that_is_not_an_object_is_refused_rather_than_coerced(client):
    got = client.post("/api/pulse/delivery/estimate", json=["8801"])
    assert got.status_code == 400


def test_an_absent_quantity_is_one(client):
    post(client)
    assert CALLS[0]["quantity"] == 1


def test_a_quantity_above_the_carts_own_ceiling_is_refused_not_clamped(client):
    """Clamping would return a window for a parcel the caller did not describe,
    labelled as though it were theirs."""
    got = post(client, quantity=delivery_routes.MAX_QUANTITY + 1)
    assert got.status_code == 400
    assert CALLS == []


def test_the_ceiling_is_the_carts_and_not_a_second_opinion(client):
    """Borrowed, because the cart is the only thing that turns a quantity into an
    order: a quote above its per-line ceiling is a quote nobody can buy."""
    from services.marketplace_cart_routes import MAX_QTY_PER_LINE
    assert delivery_routes.MAX_QUANTITY == MAX_QTY_PER_LINE
    assert post(client, quantity=MAX_QTY_PER_LINE).status_code == 200
    # The equality above is satisfied by `MAX_QUANTITY = 20`, which is the whole
    # defect: a retyped literal agrees with the cart today and stops agreeing the
    # day the cart moves its ceiling, at which point this route quietly refuses a
    # quantity the cart accepts. There is nothing to observe at runtime, so the
    # only place to catch it is the text -- the same argument as
    # `test_the_accepted_route_policies_are_borrowed_and_not_retyped`.
    source = open(delivery_routes.__file__).read()
    assert "MAX_QUANTITY = MAX_QTY_PER_LINE" in source, (
        "borrow the cart's ceiling; do not restate the number")


@pytest.mark.parametrize("bad", [0, -3, "4", 2.5, True])
def test_a_quantity_that_is_not_a_count_is_refused(client, bad):
    """``True`` is in this list because it is an ``int`` in Python: a caller who
    sent a flag would otherwise be quoted a quantity of one. ``"4"`` is here
    because a JSON body from a form-driven client sends numbers as strings often
    enough that coercing it looks like kindness -- and a coercion that accepts
    ``"4"`` is one that has to decide about ``"4.5"`` and ``" 4 "`` as well."""
    assert post(client, quantity=bad).status_code == 400


# What entry is handed


def test_the_clock_is_handed_over_as_the_policy_function_not_as_a_moment(client):
    """``policy.now_at`` itself. The origin is not known until the facts read
    inside `entry` has happened, and this process runs in UTC, which is behind
    every warehouse zone CJ uses -- 18:00 UTC is already 02:00 tomorrow in
    Shenzhen, so a moment computed here would start handling a day early."""
    post(client)
    assert CALLS[0]["now_at"] is policy.now_at


def test_every_declared_policy_input_is_passed_through(client):
    """Read off `policy`'s own readers rather than listed, so a new declared input
    that this route forgets to pass is a failure here."""
    post(client)
    sent = CALLS[0]
    for name in ("handling", "buffer_days", "dispatch_cutoff_hour", "ceiling_days",
                 "unspecified_basis"):
        assert sent[name] == getattr(policy, name)()
    assert sent["policy"] == policy.route_policy()
    assert list(sent["holidays"]) == list(policy.closures())


def test_the_cache_tier_is_wired_in(client):
    """Without a store every request is a supplier call. §18-21 is not satisfied
    by a cache that exists and is never passed."""
    post(client)
    assert isinstance(CALLS[0]["store"], store.CacheEngineStore)


def test_an_incomplete_connection_raises_from_the_adapter_source(client):
    """`listing._supplier` answers None beside `fulfillment=SUPPLIER` when the
    connection mapping is incomplete. The source must raise so the answer is
    `connection_unavailable` -- an operator sent to the credential mapping --
    rather than a missing weight, which sends them to the catalogue."""
    DECLARED["supplier"] = None
    post(client)
    with pytest.raises(Exception):
        CALLS[0]["adapter_source"]()


def test_a_complete_connection_builds_the_adapter_from_the_worker_capability(client,
                                                                            monkeypatch):
    """`worker_adapter` and not `adapter_for`: there is no actor to authorize, and
    routing a buyer's page load through a user-authorization path would mean
    inventing an actor id or widening one."""
    seen = []
    monkeypatch.setattr(delivery_routes.connections, "worker_adapter",
                        lambda *args, **kw: seen.append(args) or "ADAPTER")
    post(client)
    assert CALLS[0]["adapter_source"]() == "ADAPTER"
    assert seen == [("conn-1", "biz-1", "store-1")]


# Registration and the auth declaration


def test_the_estimate_route_declares_itself_public_with_a_reason(client):
    from services import route_auth
    found = route_auth.declaration_of(delivery_routes.delivery_estimate)
    assert found["kind"] == route_auth.AUTH_PUBLIC
    assert len(found["reason"]) > 40, "an allowlist entry is an argument, not a tick"


def test_the_health_route_is_admin_gated_in_declaration_and_in_effect(client):
    """Declared *and* enforced. `admin_required` is declarative -- it adds no
    check -- so a route wearing it without calling the gate is an open endpoint
    that reads as a closed one."""
    from services import route_auth
    found = route_auth.declaration_of(delivery_routes.delivery_health)
    assert found["kind"] == route_auth.AUTH_ADMIN
    FAKE_BOT.admin_denied = ({"ok": False}, 403)
    assert client.get("/health/delivery").status_code == 403


def test_the_health_surface_separates_an_unset_variable_from_an_unlocatable_buyer(client):
    """Both produce the same silence to a buyer and call for completely different
    work. Conflating them is how a missing environment variable is diagnosed as a
    population of visitors nothing could place."""
    body = client.get("/health/delivery").get_json()["delivery"]
    assert body["can_estimate"] == policy.declared()["can_estimate"]
    assert body["edge_tier_configured"] == destinations.edge_configured()


def test_the_health_surface_is_not_where_a_buyer_reads_a_date(client):
    """It reports configuration. A date appearing here would be a second surface
    composing a delivery answer."""
    body = client.get("/health/delivery").get_json()["delivery"]
    assert "earliest" not in body and "latest" not in body


def test_the_pack_is_loaded_by_bot():
    """Asserted from source, because importing `bot` here would boot a 132k-line
    monolith and run init_db() for one string. Registration itself is wrapped in
    `except Exception` upstream, so what this proves is that the call exists --
    `/health/routes` is what proves it succeeded on a given deploy."""
    import pathlib
    root = pathlib.Path(delivery_routes.__file__).resolve().parents[1]
    source = (root / "bot.py").read_text(errors="ignore")
    assert '_load_route_pack("pulse_delivery", "services.delivery_routes")' in source


def test_this_route_is_the_only_delivery_surface_in_the_pack(client):
    """Two endpoints, and the estimate one is the only POST. A second route
    composing a delivery answer is the failure §91-93 names, and it would arrive
    here first."""
    app = Flask(__name__)
    delivery_routes.register(app)
    rules = {str(r): sorted(m for m in r.methods if m in {"GET", "POST"})
             for r in app.url_map.iter_rules() if "delivery" in str(r)}
    assert rules == {"/api/pulse/delivery/estimate": ["POST"],
                     "/health/delivery": ["GET"]}
