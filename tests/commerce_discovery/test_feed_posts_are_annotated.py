"""Every post in a feed page says whether commerce may sit beside it.

The feed's exposure is the one the serve endpoint cannot close. Its commerce row
is a sibling row inserted *between* posts by ``injectCommerceRows``, so the
request that fetched the products never learns which posts it will land between:
``useFeedCommerce.ts`` sends no context at all, and there is nothing dishonest
about that — the row makes no claim about its neighbour. But a product strip
directly under a bereavement is offensive whether or not it claims a connection,
and only the feed *response* knows which posts are in the page.

So the server answers the adjacency question per post and the client chooses a
position with the answers. This file guards the server half, and specifically the
part the client cannot: ``neighbourAllowsCommerce`` treats a missing flag as
permission, because a native build ships on App Store review time while the
server ships in minutes, so "client newer than server" is the normal state for
days. The cost of that default is that a server path which forgets to annotate
loses the protection in total silence. That is what these tests exist to prevent,
which is why the central one asserts the *key is present on every post* rather
than only that sensitive posts are marked false.
"""

import pytest

from services.commerce_discovery import suitability


#: The measured post, quoted from the report. Field names and types are
#: `pulse_feed_engine`'s serialized shape, not the database row's: `tags` is a
#: decoded list rather than `tags_json` text, `risk_score` an int, and
#: `moderation_status` defaulted to "approved" by the serializer. Testing the
#: payload shape is the point — `annotate` runs on the response, and a check that
#: only ever saw raw rows would pass while reading nothing.
GRIEF_POST = {
    "id": 4101,
    "post_type": "text",
    "title": "",
    "body": "we lost my father this morning, rest in peace dad",
    "moderation_status": "approved",
    "ai_summary": "",
    "ai_tags": [],
    "tags": ["memorial", "grief"],
    "risk_score": 0,
}

#: Grief expressed through an object. The one that defeats a score-based
#: defence: it matches product tokens *better* than the plain-grief post, so
#: every floor in the system clears.
GRIEF_THROUGH_AN_OBJECT = {
    **GRIEF_POST,
    "id": 4102,
    "body": "wearing dad's old watch to the funeral today, miss you",
    "tags": ["memorial", "watches"],
}

SHOES_POST = {
    "id": 4103,
    "post_type": "text",
    "title": "",
    "body": "finally found the perfect running shoes, obsessed",
    "moderation_status": "approved",
    "ai_summary": "",
    "ai_tags": ["footwear"],
    "tags": ["running", "sneakers"],
    "risk_score": 0,
}

#: A caption-less photo. Extremely common, and the case that separates the two
#: questions this module answers: the server can derive no subject from it, but
#: no subject is not a harm, so commerce may still sit beside it.
WORDLESS_PHOTO = {
    "id": 4104,
    "post_type": "image",
    "title": "",
    "body": "",
    "moderation_status": "approved",
    "ai_summary": "",
    "ai_tags": [],
    "tags": [],
    "risk_score": 0,
}


@pytest.fixture(scope="module")
def feed_route_source() -> str:
    """The text of ``api_pulse_feed``, for the wiring assertions below."""
    import pathlib
    import re

    source = (pathlib.Path(__file__).resolve().parents[2] / "bot.py").read_text()
    start = source.index('@webhook_app.route("/api/pulse/feed", methods=["GET"])')
    body = source[start:]
    # To the next route decorator, so the assertions cannot accidentally be
    # satisfied by a neighbouring handler.
    end = re.search(r"\n@webhook_app\.route|\n@app\.route", body[10:])
    return body[: end.start() + 10] if end else body[:8000]


class TestEveryPostCarriesAnAnswer:
    def test_the_key_is_present_on_every_post(self):
        posts = [dict(GRIEF_POST), dict(SHOES_POST), dict(WORDLESS_PHOTO)]

        suitability.annotate(posts)

        for post in posts:
            assert suitability.PAYLOAD_KEY in post, (
                f"post {post['id']} carries no answer, and the client reads a "
                f"missing flag as permission"
            )

    def test_the_answer_is_a_real_boolean(self):
        # `commerce_suitable: 0` or `"false"` would both be truthy-or-falsy in
        # ways that differ between the JSON encoder and the client's `!== false`,
        # which compares against the boolean and nothing else.
        posts = [dict(GRIEF_POST), dict(SHOES_POST)]

        suitability.annotate(posts)

        for post in posts:
            assert post[suitability.PAYLOAD_KEY] in (True, False)
            assert isinstance(post[suitability.PAYLOAD_KEY], bool)

    def test_the_list_is_annotated_in_place(self):
        # `api_pulse_feed` calls this for its effect and ignores the return, the
        # same way `pulse_attach_video_detail_links` is called. A version that
        # only returned a new list would be a no-op at that call site.
        posts = [dict(GRIEF_POST)]

        suitability.annotate(posts)

        assert posts[0][suitability.PAYLOAD_KEY] is False


class TestWhichPostsAreRefused:
    @pytest.mark.parametrize(
        "post",
        [GRIEF_POST, GRIEF_THROUGH_AN_OBJECT],
        ids=["plain_grief", "grief_through_an_object"],
    )
    def test_a_bereavement_gets_no_neighbouring_commerce(self, post):
        posts = [dict(post)]
        suitability.annotate(posts)
        assert posts[0][suitability.PAYLOAD_KEY] is False

    def test_a_product_post_is_suitable(self):
        posts = [dict(SHOES_POST)]
        suitability.annotate(posts)
        assert posts[0][suitability.PAYLOAD_KEY] is True, (
            "a running-shoes post was refused; a suppression that also removes "
            "commerce from commerce posts is not a safety feature"
        )

    def test_a_caption_less_photo_is_still_suitable(self):
        # The distinction `assess_adjacency` exists to draw. `assess` answers
        # NO_SUBJECT here and is right to — nothing can claim to be *about* this
        # post — but the feed's row claims nothing, and marking every wordless
        # photo unsuitable would remove commerce from a large population of the
        # feed to protect nobody.
        posts = [dict(WORDLESS_PHOTO)]
        suitability.annotate(posts)

        assert suitability.assess(WORDLESS_PHOTO, context=None)["code"] == \
            suitability.NO_SUBJECT
        assert posts[0][suitability.PAYLOAD_KEY] is True

    @pytest.mark.parametrize(
        "field,value",
        [
            ("post_type", "scam_report"),
            ("post_type", "memorial"),
            ("moderation_status", "pending"),
            ("moderation_status", "flagged"),
            ("risk_score", 90),
        ],
    )
    def test_the_structural_gates_apply_to_adjacency_too(self, field, value):
        # A scam report or an unmoderated post is an adjacency problem, not a
        # claim problem: the shelf beside it is the harm.
        posts = [{**SHOES_POST, field: value}]
        suitability.annotate(posts)
        assert posts[0][suitability.PAYLOAD_KEY] is False


class TestTheAnswerRevealsNothingFurther:
    def test_only_the_boolean_is_emitted(self):
        # A feed response is read by every viewer of the post. "This post was
        # classified as grief" is a derived sensitive attribute about its author,
        # and shipping it to other people's devices to save a debugging round
        # trip is not a trade worth making. The reason stays in the server log.
        post = dict(GRIEF_POST)
        before = set(post)

        suitability.annotate([post])

        assert set(post) - before == {suitability.PAYLOAD_KEY}
        serialized = repr(post)
        assert "grief" not in serialized.replace("'memorial', 'grief'", "")
        for leak in (suitability.SENSITIVE_CONTEXT, suitability.CERTAIN,
                     suitability.CORROBORATED, "category", "evidence"):
            assert leak not in serialized


class TestTheFeedSurvivesAnAnnotationFailure:
    """A post must render even when every commerce layer is broken."""

    @pytest.mark.parametrize(
        "posts",
        [None, "not-a-list", 17, {}],
        ids=["none", "string", "int", "dict"],
    )
    def test_a_payload_that_is_not_a_list_is_returned_untouched(self, posts):
        assert suitability.annotate(posts) is posts

    @pytest.mark.parametrize("junk", [None, "a post", 42, []])
    def test_entries_that_are_not_posts_are_skipped(self, junk):
        posts = [junk, dict(SHOES_POST)]

        suitability.annotate(posts)

        assert posts[0] is junk or posts[0] == junk
        assert posts[1][suitability.PAYLOAD_KEY] is True

    def test_one_unreadable_post_does_not_stop_the_rest(self, monkeypatch):
        # The `except` inside the loop. A row that makes the assessment raise
        # leaves that post unannotated — the client's default-allow — while every
        # other post in the page still gets its answer. The alternative, letting
        # it propagate, is a 503 on the whole feed because commerce had a
        # problem.
        real = suitability.assess_adjacency

        def explode_on_one(post):
            if post.get("id") == 4101:
                raise RuntimeError("unreadable row")
            return real(post)

        monkeypatch.setattr(suitability, "assess_adjacency", explode_on_one)
        posts = [dict(GRIEF_POST), dict(SHOES_POST)]

        suitability.annotate(posts)

        assert suitability.PAYLOAD_KEY not in posts[0]
        assert posts[1][suitability.PAYLOAD_KEY] is True


class TestTheFeedRouteActuallyCallsIt:
    """The annotation is wired into `/api/pulse/feed`, not merely available.

    Asserted from source because the alternative is importing `bot`, which
    connects to a database and runs `init_db()` at module scope. The three
    properties below are the ones that decide whether the protection exists at
    runtime, and each of them has been the failure mode here before: a module
    nothing imports, a decorator attached to the wrong response, and a guard
    that shares its `except` with the handler it is supposed to survive.
    """

    def test_the_route_annotates_its_posts(self, feed_route_source):
        assert "suitability" in feed_route_source, (
            "/api/pulse/feed does not annotate its posts; the feed's commerce "
            "rows have nothing to avoid"
        )
        assert "annotate(" in feed_route_source

    def test_the_annotation_runs_on_the_posts_it_returns(self, feed_route_source):
        # Not on a copy, and not on a different list. The route passes
        # `result["posts"]`, which is what `jsonify` serializes.
        assert 'annotate(result.get("posts"))' in feed_route_source

    def test_the_annotation_cannot_take_the_feed_down(self, feed_route_source):
        # The handler's own `except` answers 503. This call needs its own, or a
        # broken commerce import becomes a dead feed — which is the exact shape
        # of the outage the 503 branch's comment was written about.
        call = feed_route_source.index('annotate(result.get("posts"))')
        guard = feed_route_source.rindex("try:", 0, call)
        between = feed_route_source[guard:call]
        assert "list_feed" not in between, (
            "the annotation is inside the handler's own try block with nothing "
            "between; a commerce failure would answer 503 for the whole feed"
        )
        after = feed_route_source[call:call + 400]
        assert "except Exception" in after
