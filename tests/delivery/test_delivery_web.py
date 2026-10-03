"""The server-rendered delivery line: cache-only, and never a corridor in a shared document.

What this file is defending
---------------------------
Six failures, none of which raises and none of which is visible in a screenshot:

* **A per-visitor arrival window inside a shared cache.**
  ``_marketplace_public_product_response`` sets ``Cache-Control: public,
  max-age=300``. Resolving the reader's country there — which is the obviously
  correct thing to do everywhere else, and is what the signed-in page does two
  hundred lines away — means the *first* reader's corridor is stored by every
  proxy in front of this route and handed to the next five minutes of readers as
  though it were theirs. Both renderings are plausible sentences, so review
  cannot see it and neither can a test that only checks the sentence parses.
  ``shared_cache=True`` raises instead of dropping the inputs, and this file
  asserts the raise reaches the caller as the pending line rather than a 500.
* **A product page that blocks on CJ.** The whole reason this module is not a
  call to the endpoint's body is ``cache_only``. A cold product must cost zero
  supplier calls at render and fill in from the client. The test proves it by
  making the supplier adapter's construction fatal: if anything resolves it, the
  line comes back as a refusal rather than as pending.
* **A refusal re-asked once per visitor over HTTP.** ``pending`` is the key that
  tells the page to fetch, and only a cache miss earns it. A seller-shipped
  listing is a real, final answer; a page that fetched for it would spend a
  request per reader to be told the same thing forever.
* **A country picker offering a corridor checkout refuses.** The options come
  from ``marketplace_fulfillment.shipping_countries`` — the same allowlist Stripe
  gets — so a buyer cannot be shown a correct window for an address that is
  rejected at payment. And it is empty when there is nothing to choose.
* **A style rule that exists on one product page and not the other.** The two
  surfaces have unrelated chrome and cannot share a stylesheet, so the rules are
  written twice. A page missing one still renders a correct sentence, unstyled —
  which is exactly the kind of defect that ships. Every selector in ``web.CSS``
  is asserted present in ``_public_shell.html``.
* **A one-sided cache-token bump.** ``/static`` is served immutable for a year.
  The two product pages reference ``pulse_delivery.js`` with their own ``?v=``
  literal, so bumping one ships two different versions of the same file to the
  same feature.

The listing fixture is the one from ``test_delivery_listing.py`` — real SQL
against a temp SQLite file — because ``web.context`` reads the listing through
``listing.declaration`` and a mocked declaration would not prove the wiring.
"""

import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-delivery-web-", suffix=".db",
                                  delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import db, marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.delivery import copy as delivery_copy  # noqa: E402
from services.delivery import destination, quote, web  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]

LISTING = 8101
SELLER = 81
PID = "PID-WEB"
VID = "VID-WEB"
CONNECTION = "conn-web"
BUSINESS = "biz-web"
STORE = "store-web"

_LISTINGS_DDL = """
CREATE TABLE marketplace_listings (
    id INTEGER PRIMARY KEY,
    seller_user_id INTEGER,
    status TEXT,
    approval_status TEXT,
    listing_type TEXT,
    product_type TEXT,
    quantity INTEGER,
    estimated_delivery TEXT,
    -- Named by `lifecycle.public_sql`, which the web delivery reader reaches
    -- through its visibility check. Its absence does not surface as a missing
    -- column here -- the read fails soft -- so the symptom was four tests
    -- asserting a corridor and getting None. Left NULL by every insert: NULL is
    -- "no publication decision recorded", coalesced to not-held, so every
    -- delivery answer in this file keeps the meaning it was written with.
    commerce_publication_enabled INTEGER
)
"""

_SELLERS_DDL = """
CREATE TABLE marketplace_sellers (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE,
    display_name TEXT,
    status TEXT
)
"""


@pytest.fixture(autouse=True)
def schema():
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS marketplace_listings")
        conn.execute("DROP TABLE IF EXISTS marketplace_sellers")
        conn.execute(f"DROP TABLE IF EXISTS {supplier_schema.SOURCE_TABLE}")
        conn.execute(_LISTINGS_DDL)
        conn.execute(_SELLERS_DDL)
        conn.execute(supplier_schema.SOURCE_TABLE_DDL)
        conn.execute("INSERT OR REPLACE INTO marketplace_sellers "
                     "(user_id, display_name, status) VALUES (?,?,?)",
                     (SELLER, "Web Store", "approved"))
        conn.execute(
            "INSERT OR REPLACE INTO marketplace_listings "
            "(id, seller_user_id, status, approval_status, listing_type,"
            " product_type, quantity, estimated_delivery) VALUES (?,?,?,?,?,?,?,?)",
            (LISTING, SELLER, "published", "approved", "physical", "physical", 5,
             "Ships in 3-5 days"))
        conn.commit()
    finally:
        conn.close()
    yield


def dropship_source():
    """A CJ-fulfilled listing: the only shape that reaches the estimator at all."""
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
            f"(listing_id, seller_user_id, provider, provider_product_id,"
            f" fulfillment_mode, supplier_connection_id, business_id, store_id,"
            f" provider_variant_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (LISTING, SELLER, "cj", PID, supplier_schema.MODE_DROPSHIP,
             CONNECTION, BUSINESS, STORE, VID))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def one_country(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "US")


@pytest.fixture
def two_countries(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "DE,US")


# ---------------------------------------------------------------------------
# The shared-cache rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("visitor", [
    {"headers": {"CF-IPCountry": "DE"}},
    {"buyer_user_id": 42},
])
def test_a_shared_cached_render_refuses_a_visitor_destination(visitor, one_country, caplog):
    """The defect this module was built around, asserted from the caller's side.

    Not ``pytest.raises``: ``context`` is called from inside a product page
    render, so it may not raise *there*. What it must do is refuse the corridor
    and say nothing, loudly, in the log — a caller who believes they are
    rendering a personalised line gets a page that is merely non-committal
    instead of a page that is confidently wrong for everyone downstream of a
    proxy.
    """
    dropship_source()
    with caplog.at_level("ERROR"):
        line = web.context(str(LISTING), shared_cache=True, **visitor)
    assert line["pending"] is True
    assert line["country"] is None
    assert line["known"] is False
    assert "DELIVERY_WEB_CONTEXT_FAILED" in caplog.text


def test_the_shared_cached_corridor_is_the_platforms_and_is_never_claimed_as_the_readers(
        one_country):
    """One accepted country, so the corridor is a fact about checkout rather than
    about the reader — but ``known`` stays false, because nothing observed *this*
    reader. ``known`` is what licenses a surface to write "to United States"."""
    dropship_source()
    line = web.context(str(LISTING), shared_cache=True)
    assert line["country"] == "US"
    assert line["known"] is False


def test_two_accepted_countries_yield_no_server_rendered_corridor(two_countries):
    """No guess. With two corridors in play the shared document cannot name one,
    and the client fetch — which may resolve the reader — is the thing that does."""
    dropship_source()
    line = web.context(str(LISTING), shared_cache=True)
    assert line["country"] is None
    assert line["known"] is False


def test_the_policy_tier_can_never_be_reached_by_resolving_a_visitor(one_country):
    """``TIER_POLICY`` is deliberately absent from ``destination.TIERS``.

    Putting it last in that tuple would make every unresolved visitor on every
    surface inherit the single-country corridor — including at checkout, where
    the address is the thing being collected.
    """
    assert destination.TIER_POLICY not in destination.TIERS
    assert destination.resolve(buyer_user_id=None, headers={})["tier"] != \
        destination.TIER_POLICY
    assert destination.policy_destination()["tier"] == destination.TIER_POLICY


# ---------------------------------------------------------------------------
# cache_only: a product page never blocks on a supplier
# ---------------------------------------------------------------------------

def test_a_cold_product_spends_no_supplier_call_and_says_so(one_country, monkeypatch):
    """Rendering must not reach CJ, and the proof is that reaching it would be fatal.

    ``_adapter_source`` is replaced with one that raises on *construction*. If
    either the origin lookup or the freight quote resolved it, the line would come
    back as a refusal (``connection_unavailable``) rather than as pending — which
    is also the shape the bug had before ``cache_only`` was threaded through
    ``origin.resolver``: freight was skipped and ``getInventoryByPid`` was not.
    """
    dropship_source()

    def explode():
        raise AssertionError("the web render reached the supplier")

    monkeypatch.setattr(web, "_adapter_source", lambda supplier: explode)
    line = web.context(str(LISTING), shared_cache=True)
    assert line["pending"] is True
    assert line["text"] == delivery_copy.loading_copy()["text"]


def test_a_sellers_own_estimated_delivery_column_is_never_the_sentence(one_country):
    """The listing fixture carries ``estimated_delivery = 'Ships in 3-5 days'`` --
    a hard-coded duration in a database column, which §5 forbids and which the
    storefront printed verbatim under the same label a computed window uses."""
    dropship_source()
    line = web.context(str(LISTING), shared_cache=True)
    assert "3-5" not in line["text"]
    assert "3-5" not in web.html(line)


# ---------------------------------------------------------------------------
# pending is a cache miss and nothing else
# ---------------------------------------------------------------------------

def test_a_seller_shipped_listing_is_answered_not_deferred(one_country):
    """No supplier row, so the answer is final. Marking it pending would spend one
    HTTP request per reader, forever, to be told the same thing."""
    line = web.context(str(LISTING), shared_cache=True)
    assert line["pending"] is False
    assert line["tone"] == delivery_copy.TONE_TERMINAL


def test_an_unparseable_reference_is_pending_rather_than_an_error_sentence(one_country):
    """A caller bug. The endpoint answers the same request with a 400 and the
    reason, which is where a bug report belongs; a buyer is not told about it."""
    line = web.context("not-a-listing", shared_cache=True)
    assert line["pending"] is True


def test_an_unpublished_listing_has_nothing_to_say(one_country):
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_listings SET status='draft' WHERE id=?",
                     (LISTING,))
        conn.commit()
    finally:
        conn.close()
    dropship_source()
    line = web.context(str(LISTING), shared_cache=True)
    assert line["pending"] is True


# ---------------------------------------------------------------------------
# The picker
# ---------------------------------------------------------------------------

def test_a_single_destination_deployment_offers_no_picker(one_country):
    dropship_source()
    assert web.context(str(LISTING), shared_cache=True)["countries"] == ()
    assert "data-delivery-country" not in web.html(
        web.context(str(LISTING), shared_cache=True))


def test_the_picker_offers_exactly_what_checkout_accepts_sorted_by_name(two_countries):
    dropship_source()
    line = web.context(str(LISTING), shared_cache=True)
    assert [entry["code"] for entry in line["countries"]] == ["DE", "US"]
    assert [entry["name"] for entry in line["countries"]] == ["Germany", "United States"]
    assert "data-delivery-country" in web.html(line)


def test_a_country_the_name_table_does_not_know_is_still_selectable(monkeypatch):
    """The platform accepts it, so a buyer has to be able to choose it. This is the
    checkout picker's rule and the opposite of ``country_name``'s, which returns
    ``""`` because it is naming a country to a supplier."""
    monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "US,XK")
    assert {"code": "XK", "name": "XK"} in web.accepted_countries()


# ---------------------------------------------------------------------------
# The markup
# ---------------------------------------------------------------------------

def test_the_pending_line_ships_its_sentence_and_announces_the_replacement(one_country):
    dropship_source()
    markup = web.html(web.context(str(LISTING), shared_cache=True))
    # The sentence is in the document at first paint, not created by the script: a
    # div that grows a line of text after a fetch pushes the buy control down the
    # page, which is the layout shift Core Web Vitals measures.
    assert delivery_copy.loading_copy()["text"] in markup
    assert 'data-pending="1"' in markup
    assert 'aria-live="polite"' in markup
    assert 'data-loading-text=' in markup


def test_a_settled_line_does_not_keep_an_aria_live_region(one_country):
    """A live region that never changes again is one a screen reader may re-announce
    on an unrelated DOM mutation."""
    markup = web.html(web.context(str(LISTING), shared_cache=True))  # seller-shipped
    assert 'data-pending="0"' in markup
    assert "aria-live" not in markup


def test_every_interpolated_value_is_escaped():
    line = dict(web.context("x", shared_cache=True))
    line["text"] = 'Arrives <script>alert("x")</script>'
    markup = web.html(line)
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_the_root_element_carries_what_the_script_needs():
    line = web.context("x", shared_cache=True)
    markup = web.html(line)
    for hook in (web.ROOT_ATTRIBUTE, "data-variant-ref", "data-quantity",
                 "data-endpoint"):
        assert hook in markup
    assert web.ESTIMATE_ENDPOINT in markup


# ---------------------------------------------------------------------------
# The two surfaces cannot drift
# ---------------------------------------------------------------------------

def test_the_public_shell_carries_every_rule_the_markup_needs():
    """``web.CSS`` is the canonical copy; the shell holds a literal one because a
    template cannot interpolate this without being handed a delivery context, and
    eight feature pages share that shell. This is what stops them diverging."""
    shell = (REPO / "templates" / "_public_shell.html").read_text()
    missing = [selector for selector in web.selectors() if selector not in shell]
    assert missing == [], missing
    assert web.selectors(), "a CSS constant with no selectors proves nothing"


def test_both_product_pages_reference_the_same_script_version():
    """A one-sided ``?v=`` bump ships two versions of one file to one feature.

    /static is served ``immutable`` for a year, so the token is the only thing that
    invalidates it, and the two product pages hold their own literal.
    """
    sources = [(REPO / "templates" / "marketplace_product_public.html").read_text(),
               (REPO / "bot.py").read_text()]
    tokens = set()
    for text in sources:
        found = re.findall(r"pulse_delivery\.js\?v=(\d+)", text)
        assert found, "a product page stopped loading the delivery script"
        tokens.update(found)
    assert len(tokens) == 1, tokens


def test_the_script_holds_no_delivery_vocabulary_of_its_own():
    """Two copy implementations exist by design -- ``delivery/copy.py`` and
    ``deliveryCopy.ts``, pinned to each other by a contract test. A third one in a
    browser file would sit outside that test, so the script has no sentence at all:
    it prints what the endpoint composed, and on failure leaves the server's
    pending line alone.
    """
    source = (REPO / "static" / "js" / "pulse_delivery.js").read_text()
    # Comments stripped first. A substring check over the whole file reads the
    # header -- which names the sentences precisely because it is explaining that
    # the code does not hold them -- and fails on the documentation of the rule it
    # is enforcing. Crude on purpose: `//` inside a string literal would confuse
    # this, and there are no string literals in the file to confuse it with, which
    # is the property under test.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for phrase in ("Estimated delivery", "FREE Shipping", "Arrives",
                   "Checking delivery", "delivery options"):
        assert phrase not in code, phrase
    # And the vocabulary it *does* need is read from the document, not held here.
    assert "dataset.loadingText" in code


def test_the_public_product_page_renders_the_markup_it_is_handed():
    """Not ``{{ delivery_line.text }}``: a template with the estimate in hand can
    print a field the copy layer never reviewed, and the structure would then be
    Jinja's on one page and ``web.html``'s on the other."""
    page = (REPO / "templates" / "marketplace_product_public.html").read_text()
    assert "{{ delivery_html | safe }}" in page


def test_the_signed_in_page_resolves_the_visitor_and_the_public_one_does_not():
    """The two calls in ``bot.py``, asserted as a pair: the difference between them
    is the shared-cache rule, and a copy-paste that made them identical would be a
    correctness bug in whichever direction it went -- a per-visitor date in a
    cached document, or a member shown the platform's corridor instead of theirs.
    """
    source = (REPO / "bot.py").read_text()
    assert "delivery_web.context(str(listing_id), shared_cache=True)" in source
    assert re.search(r"delivery_web\.context\(\s*str\(listing_id\),\s*"
                     r"buyer_user_id=user\.get\(\"user_id\"\),\s*headers=request\.headers\)",
                     source)
