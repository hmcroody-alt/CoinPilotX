"""One authoritative copy of a caption, and the correction path that proves it.

What this suite is defending
----------------------------
A PulseDrop caption used to be written into ``pulse_posts`` twice. ``body`` held
it, and so did ``ai_summary`` — byte for byte, because nothing in this codebase
has ever summarised a post. Three separate writers produced that copy:

* ``pulse_moderation_engine.moderate_text`` returned ``body[:180]`` under the
  name ``ai_summary``;
* ``create_post``'s INSERT stored that value, falling back to ``(body or
  title)[:220]`` — another copy — when it was empty;
* the ``generate_ai_summary`` job then overwrote the row with a *third* copy,
  ``body[:220]``.

None of them summarised anything. The column was a duplicate wearing the name of
a feature that does not exist.

Why a duplicate was worse than untidy
--------------------------------------
``jobs`` are one-shot. The summary job read ``body`` at run time — so it would
have self-healed if it ever ran again — but it runs once, minutes after the
post is created, and never again. From that moment the copy was frozen, and an
edit to ``body`` could not reach it.

That is not hypothetical. Post 2500 shipped with a malformed hashtag. ``body``,
``tags_json`` and ``ai_tags_json`` were corrected and the visible page came out
right, while ``bot.pulse_post_page`` built its ``<meta name="description">`` and
``og:description`` from ``ai_summary`` — so the withdrawn text kept going to
crawlers and link unfurls after the post had been fixed. It was found by curling
production and grepping for the stale token. A reviewer looking at the post in
the app could not have seen it.

``editorial.caption`` already argues this exact point for the opposite reason:
it refuses to freeze a price into a caption because "a seller re-prices on
Tuesday and every PulseDrop caption quoting the old number becomes a lie the
platform published under a verified badge." A second stored copy of the caption
is the same failure one level down — the caption is corrected on Tuesday and the
copy keeps publishing the old one.

So the invariant is: **``body`` is the only stored copy of a post's caption.**
Anything derived from it is derived when it is read, where it cannot go stale.

What the tests below pin
-------------------------
:class:`TestPublishingStoresOneCopy` covers the write side — neither the INSERT
nor the job queue may leave a second copy behind.

:class:`TestACorrectionReachesTheMetaTags` covers the read side, and it is the
one that matters for content already in production. PulseDrop is live and every
post it published before this change still carries the duplicate; those rows are
deliberately not being rewritten. So the fixture seeds a row in exactly the
broken shape — ``body`` and ``ai_summary`` both holding the bad caption — then
corrects ``body`` alone, which is what an operator does, and asserts the meta
tags follow. A fix that only stopped the write would leave this test red.

:class:`TestABodylessPostKeepsItsDescription` is the guard on the other side.
``ai_summary`` is not dead: ``live_feed_service`` writes ``"Live now: ..."`` into
it, and that is a real value of its own rather than a copy. The read path still
falls back to it when there is no body at all, and this class stops a later
tidy-up from deleting the fallback along with the duplication.
"""

from __future__ import annotations

import re

import pytest

from services.pulsedrop import editorial

#: The account PulseDrop publishes as. The number matches production so a row
#: seeded here has the same shape as the one this suite was written about.
PULSEDROP_ID = 42

#: A listing shaped like the one behind post 2500 — the category is a breadcrumb,
#: which is what ``marketplace_listings.category`` actually holds, so the caption
#: below is built by the real tag path rather than from a tidy invented string.
LISTING = {
    "id": 4100,
    "title": "Merino Wool Crew Sweater",
    "category": "Women's Clothing > Tops & Sets > Sweaters",
}

#: The defect that was corrected on post 2500: a category path flattened into one
#: run of characters and clipped mid-word. Used as the "withdrawn" text below.
BROKEN_TAG = "#womensclothingtopssw"


@pytest.fixture(scope="session")
def app(monolith):
    """The monolith. ``monolith`` lives in the conftest; see its docstring."""
    return monolith


@pytest.fixture(scope="session")
def _author(app):
    from services import db as platform_db

    conn = platform_db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, username, display_name) VALUES (?,?,?)",
        (PULSEDROP_ID, "pulsedrop", "PulseDrop"),
    )
    conn.commit()
    conn.close()
    return PULSEDROP_ID


@pytest.fixture()
def feed_engine(_author):
    from services import pulse_feed_engine

    return pulse_feed_engine


@pytest.fixture()
def published(feed_engine):
    """Publish one Signal the way ``publisher.publish_signal`` does.

    The body is built by ``editorial.caption`` from a real breadcrumb category,
    and handed to ``create_post`` as ``body=`` — the same two calls the publisher
    makes. Going through the publisher itself would need a lease, a publication
    row and a listing in the database to reach the identical INSERT.
    """
    label = editorial.classify(LISTING)
    caption = editorial.caption(LISTING, label)
    result = feed_engine.create_post(
        PULSEDROP_ID,
        body=caption,
        post_type="text",
        title=editorial.clean_title(LISTING),
        tags=editorial.hashtags(LISTING),
        visibility="public",
    )
    assert result.get("post_id"), result
    return caption, int(result["post_id"])


def _query(sql, *params):
    """Rows as value tuples on either engine.

    ``services.db.row_values`` rather than ``tuple(row)``: SQLite hands back a
    sequence and Postgres a Mapping, so ``tuple(row)`` is the values on one and
    the *column names* on the other. Its docstring has the incident.
    """
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        return [platform_db.row_values(row)
                for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _execute(sql, *params):
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _column(name, post_id):
    rows = _query(f"SELECT {name} FROM pulse_posts WHERE id=?", post_id)
    assert rows, f"post {post_id} was not written"
    return rows[0][0] or ""


def _is_a_copy_of(summary: str, body: str) -> bool:
    """Is ``summary`` just the stored caption, whole or truncated?

    The three removed writers produced ``body[:180]``, ``body[:220]`` and
    ``body`` itself, so "starts the body" catches all of them and any new
    truncation someone reintroduces. A genuine summary — a different sentence —
    does not start the body, which is deliberate: this suite forbids the
    duplicate, not the feature the column is named after.
    """
    summary = summary.strip().rstrip(".… ")
    return bool(summary) and body.startswith(summary)


class TestPublishingStoresOneCopy:
    """The write side: what ``create_post`` and the job queue leave on the row."""

    def test_the_caption_is_stored_in_body(self, published):
        """``create_post`` collapses whitespace, so the stored body is the
        caption with the newline between title and tags turned into a space."""
        caption, post_id = published
        assert _column("body", post_id) == " ".join(caption.split())

    def test_the_insert_does_not_store_a_second_copy(self, published):
        """The INSERT used to write ``moderation['ai_summary']`` — ``body[:180]``."""
        _caption, post_id = published
        summary = _column("ai_summary", post_id)
        assert not _is_a_copy_of(summary, _column("body", post_id)), (
            f"ai_summary is a copy of the caption: {summary!r}"
        )

    def test_the_job_queue_does_not_restore_the_copy(self, feed_engine, published):
        """``generate_ai_summary`` overwrote the row after the INSERT had run.

        It was the last writer, so a fix confined to ``create_post`` would be
        undone here a few minutes after every post is published.
        """
        _caption, post_id = published
        for _ in range(4):
            if not feed_engine.process_pending_jobs(batch_size=50)["processed"]:
                break
        summary = _column("ai_summary", post_id)
        assert not _is_a_copy_of(summary, _column("body", post_id)), (
            f"the job queue restored a copy of the caption: {summary!r}"
        )

    def test_the_summary_job_is_not_queued_at_all(self, published):
        """Nothing should be scheduling work whose only effect was the duplicate."""
        _caption, post_id = published
        queued = {
            row[0] for row in _query(
                "SELECT job_type FROM pulse_jobs WHERE target_type='post' AND target_id=?",
                post_id,
            )
        }
        assert "generate_ai_summary" not in queued
        # The sibling job is real work and must survive the removal.
        assert "generate_ai_tags" in queued


class TestACorrectionReachesTheMetaTags:
    """The read side, against a row in the shape production already has.

    PulseDrop's published back catalogue is not being rewritten, so every post it
    made before this change still carries the duplicate. Correcting one of them
    means correcting ``body``; the page has to follow.
    """

    @pytest.fixture()
    def corrected_post(self, feed_engine):
        """A legacy row: both columns hold the bad caption, then ``body`` is fixed."""
        post_id = 2501
        broken = f"Merino Wool Crew Sweater {BROKEN_TAG}"
        fixed = "Merino Wool Crew Sweater #sweaters #womensclothing"

        _execute(
            """
            INSERT OR REPLACE INTO pulse_posts
                (id, user_id, body, ai_summary, post_type, visibility,
                 moderation_status, status, created_at, updated_at)
            VALUES (?,?,?,?,'text','public','approved','published',?,?)
            """,
            post_id, PULSEDROP_ID, broken, broken,
            "2026-09-01T10:00:00", "2026-09-01T10:00:00",
        )
        # The operator's correction: body only. ai_summary is left exactly as it
        # was, because no correction path has ever touched it.
        _execute("UPDATE pulse_posts SET body=? WHERE id=?", fixed, post_id)
        return post_id

    @pytest.fixture()
    def page(self, app, corrected_post):
        client = app.webhook_app.test_client()
        response = client.get(f"/pulse/post/{corrected_post}")
        assert response.status_code == 200, response.status_code
        return response.get_data(as_text=True)

    def test_the_stale_caption_is_gone_from_the_page(self, page):
        """The whole defect: the page was right and the tags were not."""
        assert BROKEN_TAG not in page

    @pytest.mark.parametrize("tag", ["description", "og:description"])
    def test_the_meta_tags_carry_the_correction(self, page, tag):
        content = _meta(page, tag)
        assert "#sweaters" in content, content
        assert BROKEN_TAG not in content, content


class TestTheDescriptionIsCapped:
    """The slice used to bind to the fallback branch alone.

    ``ai_summary or (body or default)[:155]`` slices only the right-hand side,
    so any post that *had* an ``ai_summary`` shipped a description of up to the
    220 characters that column allowed — the cap was silently skipped for
    exactly the posts that reached it. A short caption cannot show this, so the
    body here is deliberately longer than both limits.
    """

    @pytest.fixture()
    def page(self, app, _author):
        long_body = "Merino Wool Crew Sweater. " + ("soft warm everyday knit. " * 12)
        assert len(long_body) > 220, len(long_body)

        post_id = 2503
        _execute(
            """
            INSERT OR REPLACE INTO pulse_posts
                (id, user_id, body, ai_summary, post_type, visibility,
                 moderation_status, status, created_at, updated_at)
            VALUES (?,?,?,?,'text','public','approved','published',?,?)
            """,
            post_id, PULSEDROP_ID, long_body, long_body[:220],
            "2026-09-01T10:00:00", "2026-09-01T10:00:00",
        )
        client = app.webhook_app.test_client()
        response = client.get(f"/pulse/post/{post_id}")
        assert response.status_code == 200, response.status_code
        return response.get_data(as_text=True)

    def test_the_description_is_capped(self, page):
        assert len(_meta(page, "description")) <= 155


class TestABodylessPostKeepsItsDescription:
    """``ai_summary`` is still read when it is not a duplicate.

    ``live_feed_service`` writes ``"Live now: <title>"`` there, and a live post
    can have no body at all. That value is the column's own, not a copy, so the
    fallback stays — this class is what makes deleting it a test failure rather
    than a silent regression to the generic string.
    """

    @pytest.fixture()
    def page(self, app, _author):
        post_id = 2502
        _execute(
            """
            INSERT OR REPLACE INTO pulse_posts
                (id, user_id, body, title, ai_summary, post_type, visibility,
                 moderation_status, status, created_at, updated_at)
            VALUES (?,?,'','Northlight goes live','Live now: Northlight goes live',
                    'text','public','approved','published',?,?)
            """,
            post_id, PULSEDROP_ID, "2026-09-01T10:00:00", "2026-09-01T10:00:00",
        )
        client = app.webhook_app.test_client()
        response = client.get(f"/pulse/post/{post_id}")
        assert response.status_code == 200, response.status_code
        return response.get_data(as_text=True)

    def test_the_live_description_survives(self, page):
        assert "Live now: Northlight goes live" in _meta(page, "description")


def _meta(page: str, name: str) -> str:
    """The ``content`` of a ``<meta>`` tag, by ``name`` or ``property``.

    Matched with a regex rather than a parser because the page is assembled as a
    string and a parser would happily accept a tag this route never emitted.
    """
    match = re.search(
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]*'
        rf'content=["\'](.*?)["\']',
        page,
        re.I | re.S,
    )
    assert match, f"no <meta {name}> on the page"
    return match.group(1)
