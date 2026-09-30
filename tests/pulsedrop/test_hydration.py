"""The live commerce overlay: what a PulseDrop post is allowed to claim.

Why this is the most load-bearing module in PulseDrop
-----------------------------------------------------
Nothing commercial is baked into a PulseDrop publication. The Reel's pixels
carry no price and no call to action, and a Signal's body text is written once
and never rewritten. Every perishable fact — the price, the stock state, the
store's name, whether there is anything to tap at all — is read out of
``marketplace_listings`` on *every* serialization and assembled here.

That makes this the module where a mistake becomes a false claim published under
a verified badge. A frozen price is the platform lying about a seller's terms; a
route emitted for a withdrawn listing is a button to a 404; a price surviving a
withdrawal is an offer that no longer exists. So the tests below are mostly
negative: they pin what the overlay refuses to say.

The two-layer design is deliberate and is tested as two layers. ``overlay()`` is
pure — a row in, a payload out, no I/O — so every availability state can be
exercised as a table. ``commerce_for_posts()`` is the batched reader, tested
against a real database because the property that matters there is that it is
*one statement per page*: the pool is eight connections with a three-second
checkout timeout, and a payload builder that opens a connection per row is a
recorded outage in this codebase.
"""

from __future__ import annotations

import pytest

from services.pulsedrop import hydration


# The column set the real query projects. Blanking all of them is how a deleted
# listing arrives: the publication row survives, the LEFT JOIN finds nothing.
def _row(**overrides):
    base = {
        "publication_id": 5,
        "post_id": 900,
        "surface": "reel",
        "ref_listing_id": 77,
        "ref_seller_user_id": 10,
        "editorial_label": "TRENDING",
        "reel_id": 3,
        "published_at": "2026-03-01T10:00:00",
        "listing_id": 77,
        "listing_seller_user_id": 10,
        "listing_title": "Aurora Desk Lamp",
        "listing_price_label": "$49.00",
        "listing_currency": "USD",
        "listing_quantity": 4,
        "listing_product_type": "physical",
        "listing_listing_type": "physical",
        "listing_status": "published",
        "listing_approval_status": "approved",
        "listing_cover_image_url": "https://cdn.test/lamp.jpg",
        "listing_category": "home",
        "seller_status": "approved",
        "seller_store_name": "Northlight Studio",
        "seller_username": "northlight",
        "seller_account_name": "Ann",
    }
    base.update(overrides)
    return base


def _deleted_row():
    return _row(**{f"listing_{column}": None for column in hydration._LISTING_COLUMNS})


#: Every way a listing can stop being buyable, and the state the overlay names.
#: Written as a table because the interesting property is that the list is
#: *complete* — an unhandled state that fell through to "available" would render
#: a live price for something nobody can buy.
UNBUYABLE = {
    "sold_out": _row(listing_quantity=0),
    "withdrawn": _row(listing_status="archived"),
    "seller_suspended": _row(seller_status="suspended"),
    "unpriced": _row(listing_price_label=""),
    "deleted": _deleted_row(),
}


class TestAnAvailableProduct:
    def test_carries_a_price_and_an_enabled_call_to_action(self):
        result = hydration.overlay(_row())
        assert result["product"]["price_label"] == "$49.00"
        assert result["product"]["buyer_visible"] is True
        assert result["cta"]["enabled"] is True
        assert result["cta"]["route"] == "/pulse/marketplace/77"

    def test_keeps_the_publisher_the_merchant_and_the_product_apart(self):
        # The whole point of the attribution block. PulseDrop published it, the
        # seller sells it, and the listing is the commerce object. Collapsing any
        # two of those would make an automated curator look like a storefront
        # owner — which is a misrepresentation, not a display bug.
        attribution = hydration.overlay(_row())["attribution"]
        assert attribution["publisher_role"] == "publisher"
        assert attribution["merchant_role"] == "merchant"
        assert attribution["seller_user_id"] == 10
        assert attribution["listing_id"] == 77

    def test_the_attribution_token_round_trips(self):
        # Forward-compatible: nothing consumes this yet, but a conversion funnel
        # that has to reconstruct Reel -> PulseDrop -> product -> seller after the
        # fact cannot do it from a post id alone.
        token = hydration.overlay(_row())["attribution"]["token"]
        assert hydration.parse_attribution_token(token) == {
            "publication_id": 5,
            "surface": "reel",
            "listing_id": 77,
        }

    def test_the_seller_link_is_a_different_destination_from_the_product(self):
        result = hydration.overlay(_row())
        assert result["seller"]["route"] == "/pulse/merchant/10"
        assert result["seller"]["route"] != result["cta"]["route"]
        assert result["seller"]["store_name"] == "Northlight Studio"


class TestAProductNobodyCanBuy:
    @pytest.mark.parametrize("name", sorted(UNBUYABLE))
    def test_emits_no_route_no_url_and_a_disabled_call_to_action(self, name):
        # A route here is a button that navigates to an error screen:
        # `/pulse/marketplace/<id>` 404s for anything not publicly visible, on
        # purpose. Refusing to emit one is the only way the client cannot render
        # a broken promise, whatever it decides to do with the rest.
        result = hydration.overlay(UNBUYABLE[name])
        assert result["cta"]["enabled"] is False
        assert result["cta"]["route"] == ""
        assert result["cta"]["url"] == ""

    @pytest.mark.parametrize("name", ["withdrawn", "seller_suspended", "deleted"])
    def test_withholds_the_price_entirely(self, name):
        # Not "shows it struck through" — omits it. A price on a withdrawn
        # listing is an offer that does not exist, and the client cannot
        # resurrect what it was never sent.
        assert hydration.overlay(UNBUYABLE[name])["product"]["price_label"] == ""

    @pytest.mark.parametrize("name", ["sold_out", "unpriced"])
    def test_still_shows_the_price_when_the_listing_is_merely_unbuyable(self, name):
        # Sold out is not withdrawn. The product is still on sale, still has
        # public terms, and hiding its price would make a restock look like a
        # different product. `unpriced` has no price to show in the first place.
        result = hydration.overlay(UNBUYABLE[name])
        assert result["product"]["buyer_visible"] is True

    def test_names_the_state_rather_than_going_silent(self):
        # A card with no price, no button and no explanation reads as broken.
        for name, row in UNBUYABLE.items():
            availability = hydration.overlay(row)["availability"]
            assert availability["code"], f"{name} produced no availability code"
            assert availability["fallback"], f"{name} produced no human-readable state"
            assert availability["purchasable"] is False


class TestTheEditorialLabel:
    @pytest.mark.parametrize("stored", ["TRENDING", "NEW_DROP", "POPULAR", "TOP_PICK", "DISCOVERY"])
    def test_is_read_from_the_stored_row_not_recomputed(self, stored):
        # The label was earned against the catalogue as it stood at publication.
        # Re-deriving it on read would let a post silently relabel itself months
        # later — "New drop" on a nine-month-old listing — and the editorial
        # claim would stop being something anyone could audit.
        assert hydration.overlay(_row(editorial_label=stored))["label"]["key"] == stored

    @pytest.mark.parametrize("stored", ["trending ", " Trending", "trending"])
    def test_case_and_padding_are_normalised_rather_than_rejected(self, stored):
        # These are the *same* claim, written by a different writer. Degrading
        # them to DISCOVERY would quietly downgrade a label the curator did
        # earn, so the normalisation is pinned rather than merely tolerated —
        # and pinning it is also what stops someone "tidying up" the resolver
        # into an exact-match lookup.
        assert hydration.overlay(_row(editorial_label=stored))["label"]["key"] == "TRENDING"

    @pytest.mark.parametrize("stored", ["", "NOT_A_LABEL", "TRENDING_NOW"])
    def test_an_unrecognised_label_degrades_to_none_rather_than_a_guess(self, stored):
        label = hydration.overlay(_row(editorial_label=stored))["label"]
        assert label["key"] in ("", "DISCOVERY")
        # Whatever it resolves to, it must not be a claim the data cannot support.
        assert label["key"] != "TRENDING"


class TestTheAccessibleSentence:
    def test_says_what_the_visual_card_says(self):
        text = hydration.overlay(_row())["accessibility_text"]
        assert "Aurora Desk Lamp" in text
        assert "$49.00" in text
        assert "Northlight Studio" in text

    @pytest.mark.parametrize("name", ["withdrawn", "seller_suspended", "deleted"])
    def test_cannot_leak_a_price_the_visual_card_hides(self, name):
        # The screen-reader surface is assembled from the same state machine, so
        # a disclosure rule that held visually but not audibly is impossible by
        # construction. This is the test that proves the construction.
        assert "$49.00" not in hydration.overlay(UNBUYABLE[name])["accessibility_text"]


class _CountingCursor:
    """A cursor that records the SQL it is asked to run and delegates the rest."""

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TestTheBatchedRead:
    """One statement per page, against a real database."""

    @staticmethod
    def _seed(cursor):
        cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, username, display_name) VALUES (10,'northlight','Ann')"
        )
        cursor.execute(
            "INSERT OR REPLACE INTO marketplace_sellers (user_id, status, display_name) "
            "VALUES (10,'approved','Northlight Studio')"
        )
        for listing_id, title, price, quantity in (
            (77, "Aurora Desk Lamp", "$49.00", 4),
            (78, "Sold Out Thing", "$9.00", 0),
        ):
            cursor.execute(
                "INSERT OR REPLACE INTO marketplace_listings "
                "(id, seller_user_id, title, price_label, currency, quantity, product_type, "
                " listing_type, status, approval_status, cover_image_url, category) "
                "VALUES (?,?,?,?,'USD',?,'physical','physical','published','approved','','home')",
                (listing_id, 10, title, price, quantity),
            )
        for index, (post_id, listing_id, surface, label) in enumerate(
            (
                (900, 77, "reel", "TRENDING"),
                (901, 78, "signal", "POPULAR"),
                # 79 does not exist: a publication whose listing was hard-deleted.
                (902, 79, "signal", "NEW_DROP"),
            ),
            start=1,
        ):
            cursor.execute(
                "INSERT OR REPLACE INTO pulsedrop_publications "
                "(idempotency_key, surface, listing_id, seller_user_id, editorial_label, post_id, "
                " state, published_at) "
                "VALUES (?,?,?,?,?,?,'published','2026-03-01T10:00:00')",
                (f"hydration-{index}", surface, listing_id, 10, label, post_id),
            )

    def test_hydrates_only_the_posts_that_are_publications(self, cursor):
        self._seed(cursor)
        found = hydration.commerce_for_posts(cursor, [900, 901, 902, 903])
        # 903 is an ordinary post. It must be *absent*, not present-and-null: a
        # client testing for the key gets a boolean, and no ordinary post grows a
        # field for a subsystem it has nothing to do with.
        assert sorted(found) == [900, 901, 902]
        assert 903 not in found

    def test_a_publication_whose_listing_was_deleted_still_renders_a_state(self, cursor):
        self._seed(cursor)
        found = hydration.commerce_for_posts(cursor, [902])
        overlay = found[902]
        # Content history and current commerce are separate. The post exists and
        # keeps existing; what it was selling does not.
        assert overlay["cta"]["enabled"] is False
        assert overlay["product"]["price_label"] == ""
        assert overlay["availability"]["purchasable"] is False

    def test_a_listing_priced_only_in_its_variants_still_gets_a_buy_cta(self, cursor):
        """82 of 123 live listings price themselves here and leave the label empty.

        Read through ``commerce_for_posts`` rather than by calling ``overlay``
        with hand-built variants: the defect this pins was that the read never
        fetched them, so an ``overlay`` test would have stayed green while every
        card in the feed said "not priced yet".
        """
        self._seed(cursor)
        cursor.execute("UPDATE marketplace_listings SET price_label='' WHERE id=77")
        cursor.execute(
            "INSERT OR REPLACE INTO marketplace_listing_variants "
            "(id, listing_id, price_cents, currency, status) VALUES (1,77,4900,'USD','active')"
        )

        found = hydration.commerce_for_posts(cursor, [900])

        assert found[900]["availability"]["code"] != "NOT_PRICED"
        assert found[900]["cta"]["enabled"] is True

    def test_attach_merges_in_place_and_leaves_ordinary_posts_untouched(self, cursor):
        self._seed(cursor)
        posts = [{"id": 900}, {"id": 901}, {"id": 903}]
        hydration.attach(cursor, posts)
        assert [("commerce" in post) for post in posts] == [True, True, False]

    def test_reads_the_whole_page_in_a_fixed_number_of_statements(self, cursor):
        self._seed(cursor)
        # Counted through a proxy rather than by monkeypatching the cursor:
        # ``connect()`` hands back a raw ``sqlite3.Cursor`` on this engine and
        # its ``execute`` is a read-only C attribute. The proxy also happens to
        # be the more honest harness, because it passes the subject exactly what
        # the feed engine passes it -- an object with ``execute`` and
        # ``fetchall`` -- rather than a cursor with a patched method.
        counted = _CountingCursor(cursor)

        # The bound is per *table*, not a literal one. Variants cannot join into
        # the page read without multiplying its rows, so they are a second
        # batched statement. What must never appear is a count that grows with
        # the page: a per-row read would be three statements here and three
        # hundred on a real page, against a pool of eight with a three-second
        # timeout. Pinned by counting a page twice the size below.
        hydration.commerce_for_posts(counted, [900, 901, 902])
        assert len(counted.statements) == 2
        assert any("marketplace_listing_variants" in s for s in counted.statements)

        for post_id, listing_id in ((910, 77), (911, 78), (912, 77)):
            cursor.execute(
                "INSERT OR REPLACE INTO pulsedrop_publications "
                "(idempotency_key, surface, listing_id, seller_user_id, editorial_label, "
                " post_id, state, published_at) "
                "VALUES (?,'signal',?,10,'NEW_DROP',?,'published','2026-03-01T10:00:00')",
                (f"hydration-wide-{post_id}", listing_id, post_id),
            )
        wider = _CountingCursor(cursor)
        hydration.commerce_for_posts(wider, [900, 901, 902, 910, 911, 912])
        assert len(wider.statements) == 2

        # And it really did the work -- a subject that returned {} without
        # querying would also "pass" the count above.
        assert sorted(hydration.commerce_for_posts(cursor, [900, 901, 902])) == [900, 901, 902]

    def test_a_deployment_without_the_tables_degrades_to_nothing(self, cursor):
        # PulseDrop is a subsystem with a kill switch, and a deployment that has
        # never run it does not have these tables. The feed must not fail for
        # that — a PulseDrop post with no overlay renders as an ordinary post,
        # which is true and harmless.
        from services.pulsedrop import schema

        self._seed(cursor)
        cursor.execute("DROP TABLE pulsedrop_publications")
        assert hydration.commerce_for_posts(cursor, [900]) == {}

        # Restore for any test that runs after this one in the same session.
        #
        # The commit is load-bearing and was missing. ``ensure_schema`` opens its
        # own connection, so an uncommitted DROP still holding SQLite's write
        # lock shuts it out — and it swallows the failure, by design: a subsystem
        # that cannot create its tables degrades rather than taking the boot
        # down. So the table stayed dropped for the rest of the session and
        # nothing said so. It went unnoticed because pytest collects this package
        # alphabetically and every suite needing the table happens to sort before
        # this file; adding one that sorted after it turned 24 unrelated tests
        # red.
        cursor.connection.commit()
        schema.reset_ready_flag()
        schema.ensure_schema()

        # Asserted, not assumed. A restore that fails quietly does its damage in
        # some other file, which is the last place anyone would look for it.
        assert cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pulsedrop_publications'"
        ).fetchall(), "not restored — every later suite in this session would fail"
