"""The sitemaps must not contradict the indexability policy.

A sitemap is a recommendation. Every URL in one is us telling Google "this is
worth crawling and ranking", so an entry that 404s, redirects, carries
`noindex`, or names a URL we have declared non-canonical is not a small
inaccuracy -- it is a false statement, and enough of them cost the credibility
that makes the true entries useful.

The pre-change sitemap made four of them at once. On 2026-09-18 `/sitemap.xml`
listed 354 URLs, of which:

* 16 returned HTTP 500,
* `/signup` shipped `noindex,nofollow` and was in the sitemap anyway,
* `/day-signal` 302'd every anonymous request to `/signup`,
* `/support` declared `canonical: /help`,
* and all 354 carried `<lastmod>` equal to today, every day.

WHAT THESE TESTS ARE FOR
------------------------
Not to re-assert the policy -- `test_search_visibility.py` does that against
the rule table directly. These check the *wiring*: that the sitemap routes
actually consult the policy rather than merely importing it. That distinction
is the whole failure mode, because a route that filters nothing still returns
valid XML and a green page.

So the central assertion is a loop over every URL the live routes emit, fed
back through `classify()`. It covers paths that do not exist yet, which is the
point: a route added next year enters the sitemap through these same helpers.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
from services import search_visibility as sv  # noqa: E402


SITEMAP_ROUTES = (
    "/sitemap-pages.xml",
    "/sitemap-posts.xml",
    "/sitemap-live.xml",
    "/sitemap-replays.xml",
)


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


def _locs(client, route):
    response = client.get(route)
    assert response.status_code == 200, route
    return re.findall(r"<loc>([^<]+)</loc>", response.get_data(as_text=True))


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", SITEMAP_ROUTES)
def test_every_submitted_url_is_one_the_policy_would_index(client, route):
    """The single assertion the old sitemap failed."""

    for loc in _locs(client, route):
        path = loc.replace(sv.CANONICAL_ORIGIN, "") or "/"
        decision = sv.classify(path)
        assert decision.sitemap_eligible, f"{route} submits {path}: {decision.reason}"


@pytest.mark.parametrize("route", SITEMAP_ROUTES)
def test_submitted_urls_are_absolute_canonical_and_on_one_host(client, route):
    for loc in _locs(client, route):
        assert loc.startswith(sv.CANONICAL_ORIGIN + "/"), loc
        assert loc == sv.canonical_url(loc.replace(sv.CANONICAL_ORIGIN, "")), loc


@pytest.mark.parametrize("route", SITEMAP_ROUTES)
def test_no_url_is_submitted_twice(client, route):
    locs = _locs(client, route)
    assert len(locs) == len(set(locs))


# ---------------------------------------------------------------------------
# The specific URLs that were wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,why",
    [
        ("/signup", "ships noindex,nofollow"),
        ("/day-signal", "302s anonymous visitors to /signup"),
        ("/support", "declares canonical: /help"),
        ("/markets/", "templated near-duplicate"),
        ("/country-intelligence/", "templated near-duplicate"),
        ("/dashboard", "authenticated"),
    ],
)
def test_the_urls_that_were_wrong_are_gone(client, path, why):
    """Named individually so a regression says which one came back, and why it
    should not be there -- the reason is the part a future reader needs."""

    for route in SITEMAP_ROUTES:
        for loc in _locs(client, route):
            assert path not in loc, f"{route} re-submitted {loc}: {why}"


# ---------------------------------------------------------------------------
# Fabricated metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", SITEMAP_ROUTES)
def test_no_changefreq_or_priority_is_invented(client, route):
    """Google ignores both, and ours were constants rather than measurements."""

    body = client.get(route).get_data(as_text=True)
    assert "<changefreq" not in body
    assert "<priority" not in body


@pytest.mark.parametrize("route", SITEMAP_ROUTES)
def test_lastmod_is_a_real_date_or_absent(client, route):
    """Never today-for-everything.

    The old generator stamped `datetime.now()` on all 354 URLs on every
    request, so the field said "everything changed this morning" every morning.
    An absent `lastmod` is honest; a fabricated one spends credibility we then
    cannot use on the pages that genuinely did change.
    """

    body = client.get(route).get_data(as_text=True)
    stamps = re.findall(r"<lastmod>([^<]+)</lastmod>", body)
    for stamp in stamps:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp), stamp

    from datetime import date

    today = date.today().isoformat()
    if len(stamps) > 3:
        assert not all(s == today for s in stamps), "every lastmod is today"


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def test_sitemap_xml_is_an_index_pointing_at_every_child(client):
    """Split by content type so Search Console reports coverage per section.

    One number over a mixed bag of marketing pages, user posts and replays is
    not actionable; "38 of 41 pages, 12 of 180 posts" is.
    """

    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "<sitemapindex" in body
    assert "<urlset" not in body
    children = re.findall(r"<loc>([^<]+)</loc>", body)
    assert set(children) == {sv.CANONICAL_ORIGIN + route for route in SITEMAP_ROUTES}


def test_every_child_named_by_the_index_actually_serves(client):
    """An index naming a 404 is worse than not splitting at all."""

    for child in re.findall(r"<loc>([^<]+)</loc>", client.get("/sitemap.xml").get_data(as_text=True)):
        route = child.replace(sv.CANONICAL_ORIGIN, "")
        response = client.get(route)
        assert response.status_code == 200, route
        assert "<urlset" in response.get_data(as_text=True), route


# ---------------------------------------------------------------------------
# The other list we hand to a search engine
# ---------------------------------------------------------------------------


def test_indexnow_declares_the_host_that_owns_its_urls(client):
    """The payload could not have succeeded as it stood.

    It declared `host: coinpilotx.app` while every URL in `urlList` and the key
    file were on `pulsesoc.com`. IndexNow requires the host to own the
    submitted URLs, so the endpoint would have rejected the batch outright --
    and a rejected submission looks the same from here as one nobody sent.
    """

    payload = client.get("/api/indexnow").get_json()
    assert payload["host"] == sv.CANONICAL_HOST
    assert payload["keyLocation"].startswith(sv.CANONICAL_ORIGIN + "/")
    for url in payload["urlList"]:
        assert url.startswith(sv.CANONICAL_ORIGIN + "/"), url


def test_indexnow_submits_only_what_the_sitemap_would(client):
    """Asking Bing to hurry and crawl a page we marked `noindex` is the same
    contradiction as the sitemap's, on a faster channel."""

    payload = client.get("/api/indexnow").get_json()
    assert payload["urlList"], "submitting nothing is not a fix"
    for url in payload["urlList"]:
        path = url.replace(sv.CANONICAL_ORIGIN, "") or "/"
        assert sv.sitemap_eligible(path), f"{path}: {sv.classify(path).reason}"


# ---------------------------------------------------------------------------
# Row-level filtering
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_posts():
    """Six posts covering the states the policy must separate.

    Seeded through the real table rather than a stub because the thing under
    test is whether `pulse_public_entries` passes its rows to the policy at
    all. A stubbed query would pass while the route filtered nothing.

    It also catches the subtler half. `content_eligibility` reads its record as
    a mapping and treats a missing key as permission, so a field the SELECT
    list omits is not checked -- it is waived. That is how the draft post below
    reached the sitemap: `status` was in the policy and not in the query. A new
    policy field therefore needs a row here, or it is unenforced and silent.
    """

    # `FORCE_INIT_DB` because importing `bot` already ran `init_db()` once,
    # against the repo database -- `conftest._never_write_to_the_developers_database`
    # redirects the SQLite fallback only after collection, so the temp database
    # this fixture writes to would otherwise have no tables in it at all.
    os.environ["FORCE_INIT_DB"] = "1"
    try:
        bot.init_db()
    finally:
        os.environ.pop("FORCE_INIT_DB", None)
    body = "A real post about something, long enough to be a destination. " * 5
    rows = [
        (9900001, "public", "approved", None, "published", body),
        (9900002, "private", "approved", None, "published", body),
        (9900003, "public", "pending", None, "published", body),
        (9900004, "public", "approved", "2026-09-01", "published", body),
        (9900005, "public", "approved", None, "draft", body),
        (9900006, "public", "approved", None, "published", "Short."),
    ]
    conn = bot.db()
    cur = conn.cursor()
    for post_id, visibility, moderation, deleted, status, text in rows:
        cur.execute(
            "INSERT INTO pulse_posts (id, user_id, post_type, body, title, visibility,"
            " moderation_status, deleted_at, status, created_at, updated_at, engagement_score)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (post_id, 1, "text", text, "", visibility, moderation, deleted, status,
             "2026-09-10T00:00:00", "2026-09-11T00:00:00", 0),
        )
    conn.commit()
    conn.close()
    try:
        yield [r[0] for r in rows]
    finally:
        conn = bot.db()
        conn.cursor().execute("DELETE FROM pulse_posts WHERE id >= 9900001 AND id <= 9900006")
        conn.commit()
        conn.close()


def test_only_the_eligible_post_is_submitted(seeded_posts):
    paths = {path for path, _lastmod in bot.pulse_public_entries(limit=500)}
    assert "/pulse/post/9900001" in paths, "the control post was excluded too"
    for excluded in seeded_posts[1:]:
        assert f"/pulse/post/{excluded}" not in paths, excluded


def test_the_submitted_post_carries_its_own_updated_at(seeded_posts):
    entries = dict(bot.pulse_public_entries(limit=500))
    assert entries["/pulse/post/9900001"].startswith("2026-09-11")


def test_a_failed_query_is_logged_rather_than_passed_off_as_no_content(monkeypatch, caplog):
    """An empty posts sitemap has two causes and one appearance.

    This was not hypothetical: while writing these tests the local run served
    `<urlset></urlset>` for `/sitemap-posts.xml` and I read it as "no posts in
    the dev database". It was a missing table. In production the same silence
    would read as "Search Console discovered 0 URLs" with nothing to point at.
    """

    def boom():
        raise RuntimeError("relation \"pulse_posts\" does not exist")

    monkeypatch.setattr(bot, "db", boom)
    with caplog.at_level("ERROR"):
        assert bot.pulse_public_entries() == []
    assert "SITEMAP_POSTS_QUERY_FAILED" in caplog.text
