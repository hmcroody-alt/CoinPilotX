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

import inspect
import os
import re
import sys
import textwrap

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
    """Seven posts covering the states the policy must separate.

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
        (9900001, 1, "public", "approved", None, "published", body),
        (9900002, 1, "private", "approved", None, "published", body),
        (9900003, 1, "public", "pending", None, "published", body),
        (9900004, 1, "public", "approved", "2026-09-01", "published", body),
        (9900005, 1, "public", "approved", None, "draft", body),
        (9900006, 1, "public", "approved", None, "published", "Short."),
        # Written by the system account. Passes every other check above -- public,
        # approved, published, long enough -- so it is excluded for authorship or
        # not at all.
        (9900007, 0, "public", "approved", None, "published", body),
    ]
    conn = bot.db()
    cur = conn.cursor()
    for post_id, user_id, visibility, moderation, deleted, status, text in rows:
        cur.execute(
            "INSERT INTO pulse_posts (id, user_id, post_type, body, title, visibility,"
            " moderation_status, deleted_at, status, created_at, updated_at, engagement_score)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (post_id, user_id, "text", text, "", visibility, moderation, deleted, status,
             "2026-09-10T00:00:00", "2026-09-11T00:00:00", 0),
        )
    conn.commit()
    conn.close()
    try:
        yield [r[0] for r in rows]
    finally:
        conn = bot.db()
        conn.cursor().execute("DELETE FROM pulse_posts WHERE id >= 9900001 AND id <= 9900007")
        conn.commit()
        conn.close()


def test_only_the_eligible_post_is_submitted(seeded_posts):
    paths = {path for path, _lastmod in bot.pulse_public_entries(limit=500)}
    assert "/pulse/post/9900001" in paths, "the control post was excluded too"
    for excluded in seeded_posts[1:]:
        assert f"/pulse/post/{excluded}" not in paths, excluded


def test_every_column_the_policy_reads_is_in_the_query(seeded_posts):
    """The failure this exists for is silent in both directions.

    `content_eligibility` reads its record as a mapping and treats an absent key
    as its permissive default, so a column the SELECT omits is not "unknown" --
    it is waived. `status` was in the policy and not in the query, and draft
    posts went to Google for it. Nothing about that looks wrong: the query
    succeeds, the policy runs, the sitemap is well-formed XML.

    The fixture above catches it only for the states someone thought to seed.
    This catches it structurally, by taking every string literal in the two
    policy functions, keeping the ones that are really `pulse_posts` columns,
    and requiring the query to fetch them. Names that are not columns
    (`content`, `hide_from_search`, `author`, `account_type`) drop out on their
    own, so there is no allowlist here to go stale.
    """

    import ast

    columns = set()
    conn = bot.db()
    try:
        for row in conn.cursor().execute("PRAGMA table_info(pulse_posts)").fetchall():
            columns.add(row[1] if not isinstance(row, dict) else row["name"])
    finally:
        conn.close()
    assert {"user_id", "status", "visibility"} <= columns, "read the wrong table"

    consulted = set()
    for func in (sv.content_eligibility, sv.is_automated_author):
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                consulted.add(node.value)
    consulted &= columns
    assert "user_id" in consulted, "the AST walk found nothing; the test is vacuous"

    selected = _selected_columns(bot.pulse_public_entries)
    missing = consulted - selected
    assert not missing, (
        f"pulse_public_entries does not SELECT {sorted(missing)}, which "
        f"content_eligibility reads. A column left out of the SELECT list is a "
        f"permission silently granted."
    )


def _selected_columns(func):
    source = textwrap.dedent(inspect.getsource(func))
    match = re.search(r"SELECT\s+(.*?)\s+FROM\s+pulse_posts", source, re.I | re.S)
    assert match, "could not find the pulse_posts SELECT list"
    return {c.strip().split()[-1].lower() for c in match.group(1).split(",")}


def test_the_submitted_post_carries_its_own_updated_at(seeded_posts):
    entries = dict(bot.pulse_public_entries(limit=500))
    assert entries["/pulse/post/9900001"].startswith("2026-09-11")


def test_the_rendered_post_page_agrees_with_the_sitemap(client, seeded_posts):
    """Dropping a URL from the sitemap is not the same as declining to be
    ranked for it.

    Google indexes what it finds by crawling, and every one of these posts is
    linked from the feed. So the page itself has to carry the directive, and it
    has to be the same directive -- a page that says `index` while the sitemap
    omits it is not a policy, it is a disagreement Google resolves in favour of
    the page.

    Both routes call `content_eligibility`, which is the point. This asserts
    they still do, through two records that differ in one column.
    """

    human = client.get("/pulse/post/9900001").get_data(as_text=True)
    automated = client.get("/pulse/post/9900007").get_data(as_text=True)

    robots = r"""name=['"]robots['"]\s+content=['"]([^'"]+)['"]"""
    control = re.search(robots, human)
    assert control and control.group(1) == sv.INDEX_DIRECTIVE, \
        "the control post is not indexable either; this test proves nothing"
    match = re.search(robots, automated)
    assert match, "the system account's post page declares no robots directive"
    assert match.group(1) == sv.NOINDEX_FOLLOW, match.group(1)


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
