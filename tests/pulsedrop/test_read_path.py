"""End to end: does the overlay reach the client, on every surface it travels?

Why this suite exists at all
----------------------------
The composer deliberately burns no price, no CTA and no availability into the
video, and the Signal publisher writes none of them into the post body. That is
the central architectural decision of PulseDrop Reels — pixels and commerce are
separate so a price change never needs a re-render — and its entire cost is that
a Reel is only shoppable if the overlay travels *beside* the content, all the
way out of the serializer.

Nothing in the unit suites can see that. ``hydration.overlay`` can be perfect
and the feature still ship dead, because between the hydration call and the
client there are a dozen transforms — media prioritisation, management flags,
ranking, reposting, JSON encoding — and any one of them that rebuilds a reel
dict from a field list rather than copying it drops ``commerce`` silently. No
crash, no warning: just a product video with no price on it. That is exactly how
the Reel overlay first shipped broken, and it is what this suite is for.

So these tests run the real read paths: ``bot.pulse_reel_payload``,
``bot.pulse_reel_feed_payload``, ``pulse_feed_engine.list_feed`` and
``get_post``, against a real database with real rows.

Why ``bot`` arrives through a fixture
--------------------------------------
Not style — correctness. ``bot`` runs ``_load_local_environment()`` and
``init_db()`` at *module* scope, so importing it at the top of this file would
execute during collection, before ``DATABASE_URL`` is pinned, and create ~550
tables in the developer's own database. The ``monolith`` fixture in this
package's conftest handles the ordering and the schema swap it forces; its
docstring is the full account.

``init_db()`` is also how this suite gets ``pulse_posts`` and ``pulse_reels``.
Unlike the unit suites — which want three marketplace tables and are right to
declare them additively rather than drag the monolith in — an integration test
of the feed engine needs the real schema, down to columns like ``u.email`` that
no fixture author would have thought to declare. Two seconds once per session is
the cheaper honesty.
"""

from __future__ import annotations

import json

import pytest

from services.pulsedrop import hydration

SELLER_ID = 10
PULSEDROP_ID = 11
RESHARER_ID = 12

VIDEO = "https://cdn.test/reel.mp4"
WHEN = "2026-03-01T10:00:00"

#: One listing per availability state reachable from a live row. The states are
#: not invented here — they are what ``hydration.state()`` derives from the
#: quantity and status columns, which is why each row differs only in those.
#: The expected codes come from the module's own constants rather than string
#: literals, because ``AVAILABLE`` is the empty string — a deliberate choice, so
#: that a client renders no availability chip at all in the normal case. A test
#: spelling it ``"AVAILABLE"`` would be asserting a state that does not exist.
LISTINGS = (
    # (listing_id, title, price, quantity, status, expected availability)
    (77, "Aurora Desk Lamp", "$49.00", 4, "published", hydration.AVAILABLE),
    (78, "Sold Out Thing", "$9.00", 0, "published", hydration.OUT_OF_STOCK),
    (79, "Withdrawn Thing", "$19.00", 4, "withdrawn", hydration.UNAVAILABLE),
)

#: A human's reel, kept well clear of the 900-block the PulseDrop reels occupy.
HUMAN_REEL_ID = 910


@pytest.fixture(scope="session")
def app(monolith):
    """The monolith. ``monolith`` lives in the conftest; see its docstring."""
    return monolith


@pytest.fixture(scope="session")
def seeded(app):
    """A seller, a PulseDrop account, three listings, and posts for each."""
    from services import db as platform_db

    conn = platform_db.connect()
    cur = conn.cursor()
    for user_id, username, name in (
        (SELLER_ID, "northlight", "Ann"),
        (PULSEDROP_ID, "pulsedrop", "PulseDrop"),
        (RESHARER_ID, "resharer", "Bee"),
    ):
        cur.execute(
            "INSERT OR REPLACE INTO users (user_id, username, display_name) VALUES (?,?,?)",
            (user_id, username, name),
        )
    cur.execute(
        "INSERT OR REPLACE INTO marketplace_sellers (user_id, status, display_name) "
        "VALUES (?,'approved','Northlight Studio')",
        (SELLER_ID,),
    )

    for index, (listing_id, title, price, quantity, status, _) in enumerate(LISTINGS):
        cur.execute(
            """
            INSERT OR REPLACE INTO marketplace_listings
                (id, seller_user_id, title, price_label, currency, quantity, product_type,
                 listing_type, status, approval_status, cover_image_url, category)
            VALUES (?,?,?,?,'USD',?,'physical','physical',?,'approved','','home')
            """,
            (listing_id, SELLER_ID, title, price, quantity, status),
        )

        # A Signal (800+) and a Reel (900+) for every listing, so each read path
        # below can be asked about every availability state.
        signal_id = 800 + index
        _post(cur, signal_id, PULSEDROP_ID, f"{title} from Northlight Studio.", "text")
        _publication(cur, f"s{signal_id}", "signal", listing_id, signal_id, "TRENDING")

        reel_id = 900 + index
        _post(cur, reel_id, PULSEDROP_ID, f"{title} from Northlight Studio.", "reel")
        cur.execute(
            """
            INSERT OR REPLACE INTO pulse_reels
                (id, post_id, user_id, video_url, caption, category, status,
                 moderation_status, created_at)
            VALUES (?,?,?,?,?,'Community','active','approved',?)
            """,
            (reel_id, reel_id, PULSEDROP_ID, VIDEO, f"{title}.", WHEN),
        )
        _publication(cur, f"r{reel_id}", "reel", listing_id, reel_id, "POPULAR")

    # A human's ordinary reel. Its job is to prove ``commerce`` is *absent* for
    # normal content rather than present-and-null — a null would make every
    # client-side `if (commerce)` check pass and render an empty overlay.
    _post(cur, HUMAN_REEL_ID, SELLER_ID, "just a normal reel", "reel")
    cur.execute(
        """
        INSERT OR REPLACE INTO pulse_reels
            (id, post_id, user_id, video_url, caption, category, status,
             moderation_status, created_at)
        VALUES (?,?,?,?,'just a normal reel','Community','active','approved',?)
        """,
        (HUMAN_REEL_ID, HUMAN_REEL_ID, SELLER_ID, VIDEO, WHEN),
    )

    # Bee reshares the first PulseDrop Signal.
    cur.execute(
        """
        INSERT OR REPLACE INTO pulse_posts
            (id, user_id, body, post_type, visibility, created_at,
             moderation_status, status, repost_of_post_id)
        VALUES (950,?,'love this','text','public','2026-03-02T10:00:00','approved','published',800)
        """,
        (RESHARER_ID,),
    )
    conn.commit()
    conn.close()
    return True


def _post(cur, post_id, user_id, body, post_type):
    cur.execute(
        """
        INSERT OR REPLACE INTO pulse_posts
            (id, user_id, body, post_type, visibility, created_at, moderation_status, status)
        VALUES (?,?,?,?,'public',?,'approved','published')
        """,
        (post_id, user_id, body, post_type, WHEN),
    )


def _publication(cur, key, surface, listing_id, post_id, label):
    cur.execute(
        """
        INSERT OR REPLACE INTO pulsedrop_publications
            (idempotency_key, surface, listing_id, seller_user_id, editorial_label,
             post_id, state, published_at)
        VALUES (?,?,?,?,?,?,'published',?)
        """,
        (key, surface, listing_id, SELLER_ID, label, post_id, WHEN),
    )


@pytest.fixture()
def feed_engine(seeded):
    from services import pulse_feed_engine

    return pulse_feed_engine


class TestTheReelDeepLink:
    """``pulse_reel_payload`` — what a shared link or a push notification opens."""

    def test_a_pulsedrop_reel_arrives_with_its_overlay(self, app, seeded):
        reel = app.pulse_reel_payload(
            post_id=900, viewer_user_id=SELLER_ID, include_preview_comments=False
        )
        commerce = reel["commerce"]
        assert commerce["pulsedrop"] is True
        assert commerce["surface"] == "reel"
        assert commerce["product"]["price_label"] == "$49.00"
        assert commerce["cta"]["enabled"] is True
        assert commerce["cta"]["route"]

    def test_an_ordinary_reel_has_no_commerce_key_at_all(self, app, seeded):
        reel = app.pulse_reel_payload(
            post_id=HUMAN_REEL_ID, viewer_user_id=SELLER_ID, include_preview_comments=False
        )
        # Absent, not ``None``. A null would satisfy every client-side presence
        # check and render an overlay with nothing in it.
        assert "commerce" not in reel

    def test_the_video_still_plays(self, app, seeded):
        # The overlay is worthless if adding it broke the thing it sits on.
        reel = app.pulse_reel_payload(
            post_id=900, viewer_user_id=SELLER_ID, include_preview_comments=False
        )
        assert reel["video_url"]


class TestTheReelFeed:
    """``pulse_reel_feed_payload`` — the scrolling surface."""

    def test_pulsedrop_reels_carry_the_overlay_through_the_feed(self, app, seeded):
        payload = app.pulse_reel_feed_payload(viewer_user_id=SELLER_ID, limit=8, lane="for_you")
        reels = {int(reel.get("post_id") or 0): reel for reel in payload.get("reels") or []}
        assert 900 in reels, f"PulseDrop reel missing from the feed: {sorted(reels)}"
        # The feed path and the deep-link path are different functions over the
        # same rows; a feed that dropped the overlay would still deep-link fine,
        # and almost nobody arrives by deep link.
        assert reels[900]["commerce"]["product"]["price_label"] == "$49.00"

    def test_the_feed_and_the_deep_link_agree(self, app, seeded):
        payload = app.pulse_reel_feed_payload(viewer_user_id=SELLER_ID, limit=8, lane="for_you")
        from_feed = next(
            reel for reel in payload["reels"] if int(reel.get("post_id") or 0) == 900
        )
        direct = app.pulse_reel_payload(
            post_id=900, viewer_user_id=SELLER_ID, include_preview_comments=False
        )
        # Two prices for one product, depending on how the user got there, is
        # the failure this rules out.
        assert from_feed["commerce"]["product"] == direct["commerce"]["product"]
        assert from_feed["commerce"]["availability"] == direct["commerce"]["availability"]


class TestTheTransformChain:
    def test_the_overlay_survives_every_reel_transform(self, app, seeded):
        """Each transform in the pipeline must copy ``commerce``, not rebuild around it.

        Run over a marked dict rather than a real row: the question is whether
        an *unknown* key survives the chain, and a real overlay would be
        re-derivable by a later step even if an earlier one dropped it — which
        would hide exactly the bug being looked for.
        """
        seed = {
            "id": 900,
            "post_id": 900,
            "user_id": PULSEDROP_ID,
            "created_at": WHEN,
            "media": [{"media_type": "video", "playback_url": VIDEO}],
            "commerce": {"marker": "intact"},
        }
        step = app.reel_prioritize_video_media(seed)
        step = app.pulse_reel_apply_management_flags(step, SELLER_ID)
        step.update(app.reel_ranking_engine.score_reel(step))
        ranked = app.reel_ranking_engine.rank_reels([step])
        assert ranked[0]["commerce"] == {"marker": "intact"}

    def test_the_overlay_is_json_serialisable(self, app, seeded):
        reel = app.pulse_reel_payload(
            post_id=900, viewer_user_id=SELLER_ID, include_preview_comments=False
        )
        # It crosses the wire. A Decimal price or a datetime would raise here and
        # take the whole response down, not just the overlay.
        restored = json.loads(json.dumps(reel["commerce"]))
        assert restored == reel["commerce"]


class TestTheReshare:
    def test_a_reshared_signal_carries_the_price_on_the_nested_original(self, feed_engine):
        post = feed_engine.get_post(950, viewer_user_id=RESHARER_ID) or {}
        # The wrapper is Bee's ordinary post and is not a publication, so it must
        # not claim to be one...
        assert post.get("commerce") is None
        # ...and the overlay has to ride on the nested original, or the reshare
        # shows the picture and the caption and no price.
        assert post["original_post"]["commerce"]["product"]["price_label"] == "$49.00"

    def test_resharing_does_not_change_the_original(self, feed_engine):
        original = feed_engine.get_post(800, viewer_user_id=RESHARER_ID) or {}
        assert original["commerce"]["product"]["price_label"] == "$49.00"
        assert original["commerce"]["attribution"]["seller_user_id"] == SELLER_ID

    def test_the_reshare_appears_in_a_feed_with_its_overlay(self, feed_engine):
        feed = feed_engine.list_feed(viewer_user_id=RESHARER_ID, feed="for_you", limit=20)
        posts = {int(post.get("id") or 0): post for post in feed.get("posts") or []}
        assert 950 in posts, f"reshare missing from the feed: {sorted(posts)}"
        assert posts[950]["original_post"]["commerce"]["product"]["price_label"] == "$49.00"


class TestEveryAvailabilityState:
    @pytest.mark.parametrize(
        "post_id,expected",
        [(800 + index, row[5]) for index, row in enumerate(LISTINGS)],
    )
    def test_the_state_reaches_the_client(self, feed_engine, post_id, expected):
        post = feed_engine.get_post(post_id, viewer_user_id=PULSEDROP_ID) or {}
        assert post["commerce"]["availability"]["code"] == expected

    @pytest.mark.parametrize(
        "post_id,purchasable",
        [(800, True), (801, False), (802, False)],
    )
    def test_only_an_available_product_offers_a_route(self, feed_engine, post_id, purchasable):
        commerce = (feed_engine.get_post(post_id, viewer_user_id=PULSEDROP_ID) or {})["commerce"]
        assert commerce["availability"]["purchasable"] is purchasable
        # ``/pulse/marketplace/<id>`` 404s for anything not publicly listed, so
        # an enabled CTA on a withdrawn product is a button that goes to an error
        # page. The route has to be empty, not merely disabled.
        assert bool(commerce["cta"]["route"]) is purchasable
        assert commerce["cta"]["enabled"] is purchasable


class TestTheKeysOnTheWire:
    def test_every_translatable_string_is_sent_as_a_registered_key(self, feed_engine):
        """The overlay never sends English — it sends keys the app resolves.

        i18n is a gated CI check on the client, but the server can still put an
        unregistered key on the wire, and an unregistered key does not fail: the
        app humanises it into plausible English and no test anywhere notices.
        """
        keys = set()
        for index in range(len(LISTINGS)):
            commerce = (
                feed_engine.get_post(800 + index, viewer_user_id=PULSEDROP_ID) or {}
            )["commerce"]
            for section in ("label", "cta", "availability"):
                if commerce[section]["i18n_key"]:
                    keys.add(commerce[section]["i18n_key"])

        assert keys, "no i18n keys on the wire at all — the overlay would be blank"
        assert sorted(key for key in keys if not key.startswith("commerce:")) == []

    def test_every_key_ships_with_a_fallback(self, feed_engine):
        commerce = (feed_engine.get_post(800, viewer_user_id=PULSEDROP_ID) or {})["commerce"]
        # An older client that has not shipped the key yet renders the fallback.
        # Without one it renders the raw key, in the middle of a product card.
        assert commerce["label"]["fallback"]
        assert commerce["availability"]["fallback"] or not commerce["availability"]["i18n_key"]
