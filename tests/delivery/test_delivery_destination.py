"""The destination resolver: what it will guess, and what it refuses to invent.

The failures worth catching here are not arithmetic. They are:

* a default country, which invents a promise for everyone it is wrong about;
* a country coerced out of something that is not one ("USA", "united states"),
  which then owns a cache entry no carrier can service;
* a postal code inherited from a past purchase into a page the buyer is merely
  browsing;
* a buyer identity travelling out of here and into a cache key;
* a device-location parameter appearing on the signature;
* "the geo tier is switched off" reported as "this visitor cannot be placed".

The database is a real temp SQLite file built from the production DDL for the one
table this reads, because the thing being tested is a query against frozen
checkout JSON and a fake dict would not have the JSON in it.
"""
from __future__ import annotations

import inspect
import json




import pytest

from services import client_address, db
from services.delivery import cache, destination

BUYER = 4242
OTHER_BUYER = 5151
GEO_HEADER = "X-Test-Country"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def database():
    """A real table, because the value under test lives inside a JSON column.

    Built through ``db.connect`` rather than a bare ``sqlite3`` handle so it lands
    wherever the suite's own isolation put the database -- the sibling delivery
    suites do the same, and a second mechanism here would write to the dev file.
    """
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS seller_transactions")
        conn.execute(
            """
            CREATE TABLE seller_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_user_id INTEGER,
                status TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def no_edge(monkeypatch):
    """No geo header by default, which is production's actual configuration."""
    monkeypatch.delenv(client_address.GEO_HEADER_ENV, raising=False)
    client_address.reset_for_tests()
    yield
    client_address.reset_for_tests()


#: So that ``metadata=None`` can mean "this row has a NULL metadata column"
#: rather than "build me the default blob". The distinction matters: a NULL
#: column is one of the shapes the history tier has to survive.
DEFAULT = object()


def order(*, buyer=BUYER, status="paid", country="US", postal="90210", kind="shipping",
          details=True, metadata=DEFAULT):
    """One row in the shape checkout actually freezes."""
    if metadata is DEFAULT:
        if details:
            inner = {"kind": kind, "details": {"address_country": country,
                                               "address_postal_code": postal,
                                               "address_city": "Beverly Hills"}}
            metadata = {"fulfillment": inner}
        else:
            metadata = {"cart": {"items": 1}}
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seller_transactions (buyer_user_id, status, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (buyer, status, json.dumps(metadata) if metadata is not None else None, "2026-03-01"),
        )
        conn.commit()


def with_edge(monkeypatch, country):
    monkeypatch.setenv(client_address.GEO_HEADER_ENV, GEO_HEADER)
    return {GEO_HEADER: country}


def recording_connect(rows=()):
    """A connect callable that records every statement instead of running one.

    Returned alongside the list it appends to, so a test can assert on what the
    history tier *asked* rather than on what it returned. That distinction is
    load-bearing here: ``_from_history`` wraps its read in ``except Exception``
    on purpose, so a fake that raises to signal "you should not have called me"
    is swallowed and the test passes either way. Observing the call is the only
    channel that survives that guard.
    """
    seen = []

    class Cursor:
        def execute(self, sql, params=()):
            seen.append((sql, params))

        def fetchall(self):
            return list(rows)

    class Conn:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return seen, Conn


# ---------------------------------------------------------------------------
# Nothing is invented
# ---------------------------------------------------------------------------

def test_no_source_at_all_resolves_to_nothing_rather_than_a_default():
    """The single most damaging possible defect in this module is a default."""
    got = destination.resolve()
    assert got["known"] is False
    assert got["tier"] == destination.TIER_NONE
    assert got["destination"] == {"country": None, "postal": None}
    assert got["precision"] == destination.PRECISION_NONE


def test_an_unknown_destination_is_not_named_after_any_country():
    """Guards against the plausible-looking `"country": "ZZ"` or `"XX"` sentinel,
    which would key a cache entry and read as a real place to every layer above."""
    got = destination.resolve()
    assert got["destination"]["country"] is None


@pytest.mark.parametrize("value", ["USA", "united states", "U", "", "  ", "U1", "12",
                                   "ZZZ", None, 7, True, ["US"], {"country": "USA"},
                                   {"country": 7}, {}])
def test_a_country_that_is_not_two_letters_is_discarded_not_coerced(value):
    """Coercion is the trap: "USA"[:2] is "US" and happens to be right, which is
    exactly why a truncating implementation survives review. "GBR"[:2] is "GB" and
    also right. "CHN"[:2] is "CH", which is Switzerland."""
    got = destination.resolve(stated=value)
    assert got["known"] is False
    assert got["tier"] == destination.TIER_NONE


def test_a_three_letter_code_is_not_truncated_into_the_wrong_country():
    """CHN -> CH would quote a Chinese buyer a Swiss window, and Switzerland is a
    supported destination, so nothing downstream would refuse it."""
    got = destination.resolve(stated="CHN")
    assert got["destination"]["country"] is None


def test_a_lowercase_country_is_accepted_and_normalised():
    got = destination.resolve(stated="de")
    assert got["destination"]["country"] == "DE"
    assert got["tier"] == destination.TIER_STATED


def test_a_padded_country_is_accepted_and_normalised():
    got = destination.resolve(stated="  fr  ")
    assert got["destination"]["country"] == "FR"


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------

def test_the_address_for_this_purchase_outranks_everything(monkeypatch):
    order(country="GB")
    got = destination.resolve(
        checkout={"address_country": "JP", "address_postal_code": "100-0001"},
        stated="DE", buyer_user_id=BUYER, headers=with_edge(monkeypatch, "BR"),
    )
    assert got["tier"] == destination.TIER_CHECKOUT
    assert got["destination"]["country"] == "JP"


def test_a_stated_country_outranks_history_and_the_edge(monkeypatch):
    order(country="GB")
    got = destination.resolve(stated="DE", buyer_user_id=BUYER,
                              headers=with_edge(monkeypatch, "BR"))
    assert got["tier"] == destination.TIER_STATED
    assert got["destination"]["country"] == "DE"


def test_history_outranks_the_edge(monkeypatch):
    """A place the buyer actually had a parcel delivered beats a network guess."""
    order(country="GB")
    got = destination.resolve(buyer_user_id=BUYER, headers=with_edge(monkeypatch, "BR"))
    assert got["tier"] == destination.TIER_LAST_ORDER
    assert got["destination"]["country"] == "GB"


def test_the_edge_answers_when_nothing_else_can(monkeypatch):
    got = destination.resolve(headers=with_edge(monkeypatch, "BR"))
    assert got["tier"] == destination.TIER_EDGE
    assert got["destination"]["country"] == "BR"


def test_a_higher_tier_that_yields_nothing_falls_through_rather_than_stopping(monkeypatch):
    """An unparseable stated country must not shadow a usable one below it."""
    got = destination.resolve(stated="not a country", headers=with_edge(monkeypatch, "BR"))
    assert got["tier"] == destination.TIER_EDGE
    assert got["destination"]["country"] == "BR"


def test_precedence_is_declared_once_and_matches_what_resolve_does():
    """TIERS is the contract; a reordering must show up as a change to it rather
    than as a reshuffle of `if` statements nothing is watching."""
    assert destination.TIERS == (destination.TIER_CHECKOUT, destination.TIER_STATED,
                                 destination.TIER_LAST_ORDER, destination.TIER_EDGE)
    assert destination.TIER_NONE not in destination.TIERS


def test_every_tier_a_resolution_can_report_is_a_declared_one(monkeypatch):
    order(country="GB")
    seen = {
        destination.resolve()["tier"],
        destination.resolve(stated="DE")["tier"],
        destination.resolve(checkout={"country": "JP"})["tier"],
        destination.resolve(buyer_user_id=BUYER)["tier"],
        destination.resolve(headers=with_edge(monkeypatch, "BR"))["tier"],
    }
    assert seen <= set(destination.TIERS) | {destination.TIER_NONE}
    assert seen == set(destination.TIERS) | {destination.TIER_NONE}


# ---------------------------------------------------------------------------
# Postal precision is not inherited
# ---------------------------------------------------------------------------

def test_the_checkout_address_carries_its_postal_code():
    got = destination.resolve(checkout={"country": "US", "postal": "90210"})
    assert got["destination"]["postal"] == "90210"
    assert got["precision"] == destination.PRECISION_POSTAL


def test_a_past_orders_postal_code_is_not_reused_for_a_browse():
    """The order has one. Inheriting it would put a fragment of an address given
    for one purchase into the cache key of a page being browsed."""
    order(country="GB", postal="SW1A 1AA")
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["destination"]["country"] == "GB"
    assert got["destination"]["postal"] is None
    assert got["precision"] == destination.PRECISION_COUNTRY


def test_the_history_tier_never_reads_the_postal_code_at_all():
    """Asserted on the tier and not through ``resolve``, because there are two
    guards here and the outer one hides the inner one.

    ``resolve`` drops a postal code from any tier outside ``POSTAL_TIERS``, so the
    test above passes whether or not this tier ever read one -- which was a
    surviving mutant until this test existed. The inner restraint is not
    redundant: it means the buyer's postal code is never lifted out of the frozen
    blob in the first place, so it does not exist in this process's memory on a
    browse. Defence in depth only counts as depth if both layers are observed.
    """
    order(country="GB", postal="SW1A 1AA")
    assert destination._from_history(BUYER, db.connect) == ("GB", None)


def test_a_stated_country_cannot_smuggle_a_postal_code_in_with_it():
    """STATED is not in POSTAL_TIERS, so a caller handing it a postal code gets a
    country-precise answer -- enforced on the record rather than trusted at the
    call site."""
    got = destination.resolve(stated={"country": "DE", "postal": "10115"})
    assert got["tier"] == destination.TIER_STATED
    assert got["destination"]["postal"] is None
    assert got["precision"] == destination.PRECISION_COUNTRY


def test_only_the_checkout_tier_may_carry_a_postal_code():
    assert destination.POSTAL_TIERS == frozenset({destination.TIER_CHECKOUT})


def test_a_postal_code_with_no_country_is_not_a_place():
    """route_key would accept postal-without-country and file an entry under a
    destination it cannot name."""
    got = destination.resolve(checkout={"postal": "90210"})
    assert got["known"] is False
    assert got["destination"]["postal"] is None


def test_an_address_with_no_country_yields_no_postal_code_either():
    """The same masking as the history tier, in the other direction.

    ``resolve`` advances on a country, so a ``(None, "90210")`` from this function
    falls through and the test above passes regardless -- also a surviving mutant
    until this test existed. Asserted here because this function's own contract is
    "a place or nothing", and a caller that reached for the postal alone would get
    one for a destination nobody named.
    """
    assert destination._from_address({"postal": "90210"}) == (None, None)
    assert destination._from_address({"country": "USA", "postal": "90210"}) == (None, None)


def test_precision_is_derived_from_what_was_found():
    """Not settable, so it cannot claim POSTAL over a country-only answer and earn
    a postal-precise promise."""
    assert destination.resolve(checkout={"country": "US", "postal": "90210"})["precision"] \
        == destination.PRECISION_POSTAL
    assert destination.resolve(checkout={"country": "US"})["precision"] \
        == destination.PRECISION_COUNTRY
    assert destination.resolve()["precision"] == destination.PRECISION_NONE


def test_a_blank_postal_code_is_absent_rather_than_empty():
    got = destination.resolve(checkout={"country": "US", "postal": "   "})
    assert got["destination"]["postal"] is None
    assert got["precision"] == destination.PRECISION_COUNTRY


def test_the_postal_code_is_handed_on_unshortened():
    """cache._postal owns the prefix rule for the whole domain. Truncating here as
    well would make the prefix depend on which path an address arrived by."""
    got = destination.resolve(checkout={"country": "US", "postal": "90210-4455"})
    assert got["destination"]["postal"] == "90210-4455"


# ---------------------------------------------------------------------------
# The checkout field names this platform actually froze
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["country", "address_country", "countryCode"])
def test_the_country_is_found_under_each_spelling_in_use(field):
    got = destination.resolve(checkout={field: "IT"})
    assert got["destination"]["country"] == "IT"


@pytest.mark.parametrize("field", ["postal", "address_postal_code", "postal_code", "zip"])
def test_the_postal_code_is_found_under_each_spelling_in_use(field):
    got = destination.resolve(checkout={"country": "US", field: "90210"})
    assert got["destination"]["postal"] == "90210"


def test_a_bare_string_is_read_as_a_country_code():
    """Which is what a "deliver to" picker produces."""
    got = destination.resolve(stated="NL")
    assert got["destination"]["country"] == "NL"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def test_an_unpaid_order_is_not_inherited():
    """An abandoned checkout holds an address no purchase was completed with, and
    one plausible reason for abandoning is that the address was wrong."""
    order(country="GB", status="checkout_created")
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["known"] is False


def test_another_buyers_order_is_never_inherited():
    order(buyer=OTHER_BUYER, country="GB")
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["known"] is False


def test_the_most_recent_shipped_order_wins():
    order(country="GB")
    order(country="FR")
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["destination"]["country"] == "FR"


def test_a_digital_purchase_does_not_shadow_the_last_shipped_one():
    """The newest paid row is often not the newest shipped one, because most
    purchases on this platform carry no address at all."""
    order(country="GB")
    order(details=False)
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["destination"]["country"] == "GB"


#: The depth pinned as a literal rather than read off the module. Reading it made
#: the test below adapt to whatever it was set to -- inserting N filler rows for
#: any N -- so it could never notice the bound being raised. A test that derives
#: its input from the value it is pinning has pinned nothing.
EXPECTED_HISTORY_DEPTH = 5


def test_the_history_bound_is_small():
    assert destination._HISTORY_DEPTH == EXPECTED_HISTORY_DEPTH


def test_history_is_not_scanned_without_bound():
    """Bounded because this runs on a product page. Not finding a shipped order
    falls through to the next tier, which is harmless -- and that is precisely what
    makes an unbounded scan of a buyer's whole transaction history unjustifiable."""
    order(country="GB")
    for _ in range(EXPECTED_HISTORY_DEPTH + 1):
        order(details=False)
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["known"] is False


@pytest.mark.parametrize("metadata", [
    None,
    {},
    {"fulfillment": None},
    {"fulfillment": "shipping"},
    {"fulfillment": {"details": None}},
    {"fulfillment": {"details": "US"}},
    {"fulfillment": {"details": {}}},
    {"fulfillment": {"details": {"address_country": None}}},
    {"fulfillment": {"details": {"address_country": ""}}},
    {"fulfillment": {"details": {"address_country": "USA"}}},
])
def test_a_metadata_blob_without_a_usable_country_yields_nothing(metadata):
    """This column holds every kind of transaction on the platform, so a missing
    key is the common case rather than corruption."""
    order(metadata=metadata)
    got = destination.resolve(buyer_user_id=BUYER)
    assert got["known"] is False


def test_unparseable_metadata_does_not_raise():
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seller_transactions (buyer_user_id, status, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)", (BUYER, "paid", "{not json", "2026-03-01"))
        conn.commit()
    assert destination.resolve(buyer_user_id=BUYER)["known"] is False


def test_a_database_failure_falls_through_instead_of_failing_the_page(monkeypatch):
    """This tier is a hint. A read that cannot answer must produce the next tier,
    not a product page that will not render."""
    def boom():
        raise RuntimeError("pool exhausted")

    got = destination.resolve(buyer_user_id=BUYER, connect=boom,
                              headers=with_edge(monkeypatch, "BR"))
    assert got["tier"] == destination.TIER_EDGE
    assert got["destination"]["country"] == "BR"


@pytest.mark.parametrize("buyer", [None, "4242", 42.0, True, False])
def test_a_buyer_id_that_is_not_an_integer_is_not_queried_with(buyer):
    """`True` is the one that matters: it is an int in Python, so an unguarded
    query would look up user 1 -- who on this deployment is the only seller, and
    therefore the one account whose order history is not empty. That mutant would
    hand a stranger's country to every logged-out visitor.

    Asserted on the statement list rather than on the returned record, and rather
    than on a fake that raises. Both of those alternatives pass against an
    unguarded implementation: the record is ``known is False`` either way once the
    fake returns no rows, and a raised ``AssertionError`` is caught by
    ``_from_history``'s own ``except Exception``.
    """
    seen, conn = recording_connect()
    got = destination.resolve(buyer_user_id=buyer, connect=conn)
    assert seen == []
    assert got["known"] is False


def test_the_history_query_reads_only_paid_orders_for_one_buyer():
    """Pinned because the alternative failure is silent: a dropped buyer predicate
    resolves *someone else's* country and the page still renders a plausible
    window."""
    seen, conn = recording_connect()
    destination.resolve(buyer_user_id=BUYER, connect=conn)
    sql, params = seen[0]
    assert "buyer_user_id = ?" in sql
    assert "status = ?" in sql
    assert params == (BUYER, "paid", destination._HISTORY_DEPTH)


# ---------------------------------------------------------------------------
# The edge tier, and the difference between "off" and "could not place you"
# ---------------------------------------------------------------------------

def test_the_geo_tier_is_reported_as_unconfigured_rather_than_silently_empty():
    """Production has no PULSESOC_TRUSTED_GEO_HEADER set. Reported as a tier that
    is switched off, because the alternative diagnosis -- a population of
    unlocatable buyers -- sends nobody to the variable that would fix it."""
    assert destination.edge_configured() is False


def test_a_configured_geo_tier_says_so(monkeypatch):
    monkeypatch.setenv(client_address.GEO_HEADER_ENV, GEO_HEADER)
    assert destination.edge_configured() is True


def test_a_configured_tier_that_placed_nobody_is_still_configured(monkeypatch):
    """The two conditions that both produce TIER_NONE, held apart."""
    headers = with_edge(monkeypatch, "")
    got = destination.resolve(headers=headers)
    assert got["tier"] == destination.TIER_NONE
    assert destination.edge_configured() is True


def test_a_country_header_is_ignored_when_no_edge_is_configured():
    """Otherwise any client could name its own country by sending the header --
    and while a wrong estimate is not an authorization bug, an unconfigured
    deployment trusting client headers is how one starts."""
    got = destination.resolve(headers={GEO_HEADER: "BR"})
    assert got["known"] is False


def test_the_edge_rule_is_delegated_rather_than_restated(monkeypatch):
    """A second, looser notion of a trusted source always wins in practice,
    because it is the one that returns an answer."""
    monkeypatch.setenv(client_address.GEO_HEADER_ENV, GEO_HEADER)
    calls = []

    def spy(headers):
        calls.append(headers)
        return "BR"

    monkeypatch.setattr(client_address, "client_country", spy)
    got = destination.resolve(headers={GEO_HEADER: "BR"})
    assert got["destination"]["country"] == "BR"
    assert calls == [{GEO_HEADER: "BR"}]


def test_headers_that_cannot_be_read_do_not_raise(monkeypatch):
    monkeypatch.setenv(client_address.GEO_HEADER_ENV, GEO_HEADER)

    class Hostile:
        def get(self, *args, **kwargs):
            raise RuntimeError("header store is broken")

    assert destination.resolve(headers=Hostile())["known"] is False


def test_no_headers_at_all_is_not_an_error():
    assert destination.resolve(headers=None)["known"] is False


# ---------------------------------------------------------------------------
# What must not leave this module
# ---------------------------------------------------------------------------

def test_the_destination_handed_onward_holds_nothing_but_a_place():
    """`quote_delivery` passes the whole destination dict to the supplier, so a
    tier label or a buyer id inside it is a field sent to CJ."""
    order(country="GB")
    got = destination.resolve(buyer_user_id=BUYER,
                              checkout={"country": "US", "postal": "90210"})
    assert set(got["destination"]) == {"country", "postal"}


def test_the_buyer_identity_does_not_travel_with_the_answer():
    """The country produced here becomes part of a cache key. If identity could
    travel with it, one buyer's page load would file an entry another reads."""
    order(country="GB")
    got = destination.resolve(buyer_user_id=BUYER)
    assert str(BUYER) not in repr(got)


def test_the_resolution_is_a_usable_cache_key_input():
    """The whole point of the record: what comes out of here goes straight into
    route_key without a translation step that could disagree with it."""
    got = destination.resolve(checkout={"country": "US", "postal": "90210"})
    key = cache.route_key(supplier="cj", variant_ref="7001",
                          destination=got["destination"]["country"],
                          postal=got["destination"]["postal"])
    assert "US" in key
    # The prefix rule is cache's; this asserts only that the full code did not
    # survive into the key, which is the privacy property cache owns.
    assert "90210" not in key


def test_an_unknown_destination_cannot_be_keyed_at_all():
    """The two halves of the same rule, asserted together.

    ``route_key`` refuses a missing country outright -- keying without one would
    serve one country's transit time to another country's buyer -- so an unknown
    destination is not a cache miss, it is unaskable. That is why ``known`` is a
    field on the record rather than something a caller infers: whoever holds this
    answer has to stop before the cache, and ``quote_delivery`` does exactly that,
    refusing ``destination_unresolved`` before any key is built.
    """
    got = destination.resolve()
    assert got["known"] is False
    with pytest.raises(cache.DeliveryCacheRejected):
        cache.route_key(supplier="cj", variant_ref="7001",
                        destination=got["destination"]["country"],
                        postal=got["destination"]["postal"])


# ---------------------------------------------------------------------------
# Shape of the interface
# ---------------------------------------------------------------------------

def test_the_resolver_has_no_device_location_parameter():
    """Mission constraint: an estimate must not require precise device location.
    Asserted structurally, because the way that requirement returns is as a
    parameter someone adds "just for accuracy"."""
    names = set(inspect.signature(destination.resolve).parameters)
    for banned in ("lat", "latitude", "lon", "lng", "longitude", "coords",
                   "coordinates", "location", "gps", "ip", "ip_address",
                   "remote_addr", "city", "address"):
        assert banned not in names


def test_every_source_is_optional():
    """A product page has none of them, and that must be a call it can make."""
    for name, param in inspect.signature(destination.resolve).parameters.items():
        assert param.default is not inspect.Parameter.empty, name


def test_every_argument_is_keyword_only():
    for param in inspect.signature(destination.resolve).parameters.values():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_record_always_has_the_same_keys(monkeypatch):
    order(country="GB")
    expected = {"destination", "tier", "precision", "known"}
    for kwargs in ({}, {"stated": "DE"}, {"buyer_user_id": BUYER},
                   {"checkout": {"country": "US", "postal": "90210"}},
                   {"headers": with_edge(monkeypatch, "BR")},
                   {"stated": "nonsense"}):
        assert set(destination.resolve(**kwargs)) == expected
