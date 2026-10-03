"""What we would hand a search engine, and the gates that keep the rest out.

A submission channel fails quietly. IndexNow answers `200 OK` for a URL that is
noindex, for a URL that was deleted a minute ago, and for a URL nobody will ever
crawl -- the `200` is a receipt for the request, not for anything a user could
find. So a broken payload here does not produce a red test or a 500; it produces
outbound traffic that looks like progress.

These tests therefore check two separate things and it is worth being explicit
about which is which:

**The gates.** That ineligible things are excluded, with the exclusion coming
from the shared authority rather than from a local opinion. These are pure
functions over injected entries, so they run without a database and without
seeding a catalogue.

**The wiring.** That `/api/indexnow` actually consults those gates and actually
reads the same sources as the sitemaps. This is the failure mode the existing
sitemap suite was written for and it applies identically here: a route that
filters nothing still returns valid JSON and a green page.

One measured surprise is pinned below rather than trusted. `sitemap_eligible`
returns **true** for `/sitemap.xml`, so the sitemaps are kept out of the payload
by an explicit check and not by the policy table.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import marketplace_listing_lifecycle  # noqa: E402
from services import open_web_distribution as owd  # noqa: E402
from services import search_visibility as sv  # noqa: E402


PAGES = owd.CLASS_PAGES
POSTS = owd.CLASS_POSTS
PRODUCTS = owd.CLASS_PRODUCTS
CATEGORIES = owd.CLASS_CATEGORIES


def universe(**sources):
    return owd.candidate_universe(sources)


# ---------------------------------------------------------------------------
# What gets in
# ---------------------------------------------------------------------------


def test_every_distributable_class_reaches_the_payload():
    """The point of the rewire: products, departments and posts are carried.

    Before this, the payload came from a hand-maintained marketing page list, so
    the one channel whose purpose is speed covered only the URLs that never
    change. A regression here would not fail anything else -- the payload would
    still be valid, still be on-host, still be fully eligible, and still be
    useless.
    """

    result = universe(
        pages=["/about"],
        posts=[("/pulse/post/5", "2026-10-01")],
        products=[("/pulse/marketplace/35", "2026-10-02")],
        categories=[("/pulse/marketplace?category=mens-clothing", "2026-10-02")],
    )

    assert result.included == {PAGES: 1, POSTS: 1, CATEGORIES: 1, PRODUCTS: 1}
    assert result.urls == [
        "https://pulsesoc.com/about",
        "https://pulsesoc.com/pulse/post/5",
        "https://pulsesoc.com/pulse/marketplace?category=mens-clothing",
        "https://pulsesoc.com/pulse/marketplace/35",
    ]


def test_a_department_keeps_its_allowlisted_query_string():
    """Departments are the only class whose canonical URL carries a query.

    `canonical_url` allowlists exactly one parameter on exactly one path. Strip
    it and all four departments canonicalise to the bare hub, which is one
    duplicate URL submitted four times -- and the dedupe below would then hide
    it as three duplicates rather than report four wrong URLs.
    """

    result = universe(categories=[
        ("/pulse/marketplace?category=mens-clothing", ""),
        ("/pulse/marketplace?category=womens-clothing", ""),
    ])

    assert result.total == 2
    assert result.duplicates == 0


# ---------------------------------------------------------------------------
# What stays out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,label", [
    ("/signup", "registration workflow state"),
    ("/checkout", "commerce workflow"),
    ("/admin", "administrative surface"),
    ("/api/pulse/posts", "JSON API, not a page"),
    ("/search?q=shoes", "internal search results"),
    ("/pulse/merchant/dashboard", None),
    ("/pulse/marketplace/create", None),
])
def test_non_content_surfaces_are_excluded(path, label):
    result = universe(pages=[path])

    assert result.total == 0, f"{path} reached the payload"
    assert result.included[PAGES] == 0
    if label:
        assert label in result.excluded


@pytest.mark.parametrize("path", [
    "/pulse/marketplace?utm_source=indexnow",
    "/pulse/marketplace?page=2",
])
def test_tracking_and_pagination_parameters_are_excluded(path):
    """A URL that is not the canonical form of itself must not be submitted.

    `classify` says these are eligible -- it is query-blind on purpose, because
    `?page=2` is the same page *shape* as the hub. Only the full
    `sitemap_eligible` gate catches them, which is why this module calls that
    and not `classify`. Dropping to `classify` passes every other test in this
    file.
    """

    result = universe(pages=[path])

    assert sv.classify(path).sitemap_eligible, "premise: classify alone allows this"
    assert result.total == 0
    assert "non-canonical query string" in result.excluded


@pytest.mark.parametrize("path", [
    "/sitemap.xml",
    "/sitemap-pages.xml",
    "/sitemap-products.xml",
    "/feeds/merchant-center.xml",
])
def test_machine_readable_surfaces_are_excluded(path):
    """And the sitemaps are excluded by *our* check, not by the policy table.

    Pinned because it is counter-intuitive and because the implicit version of
    this protection is invisible: supply a sitemap URL and nothing complains.
    """

    assert universe(pages=[path]).total == 0


@pytest.mark.parametrize("supplied", [
    "https://evil.example.com/pulse/marketplace/1",
    "//evil.example.com/pulse/marketplace/1",
    "http://pulsesoc.com/about",
    "pulse/marketplace/35",
    "",
])
def test_an_absolute_or_unrooted_url_cannot_enter_a_batch(supplied):
    """Found by test, not by reading: the host assertion alone does not catch this.

    `canonical_url` normalises by prepending the origin, so an absolute URL
    handed in where a path was expected comes back as
    `https://pulsesoc.com/https://evil.example.com/...`. That is *on-host*, so
    it passes the host check, and it is a guaranteed 404 -- a submission asking
    an engine to hurry up and fetch nothing.

    The hardcoded origin holds, so this is not a host leak. It is a
    malformed-input hole, and the input that would reach it is a seller- or
    importer-supplied URL stored where a path was expected, which is the shape
    of data the marketplace actually carries.
    """

    result = universe(pages=[supplied])

    assert result.total == 0, result.urls
    for url in result.urls:
        assert "evil.example.com" not in url


def test_a_foreign_url_is_refused_at_the_batch_boundary_too():
    """Two independent checks, because the cost of a `422` is the whole batch."""

    assert not owd.batch_is_submittable(["https://evil.example.com/x"])
    assert not owd.batch_is_submittable(["https://pulsesoc.com.evil.example.com/x"])
    assert not owd.batch_is_submittable([])
    assert not owd.batch_is_submittable(["https://pulsesoc.com/a"] * (owd.MAX_URLS_PER_SUBMISSION + 1))


def test_every_submitted_url_is_on_the_declared_host():
    result = universe(
        pages=["/about", "/signup"],
        products=[("/pulse/marketplace/35", "")],
    )

    assert owd.batch_is_submittable(result.urls)
    for url in result.urls:
        assert url.startswith(owd.ORIGIN + "/"), url
    assert owd.HOST == sv.CANONICAL_HOST


def test_an_unknown_entity_class_is_ignored_rather_than_guessed_at():
    """Adding a class is a contract change; it should require editing the module."""

    assert universe(replays=[("/pulse/replay/1", "")], live=[("/pulse/live/1", "")]).total == 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_duplicates_are_dropped_and_counted():
    """The live payload shipped `/sports-edge` twice at `5bdf4e431`.

    `seo.content.all_public_paths()` returns it twice; `/sitemap-pages.xml` never
    showed it because that caller wraps the list in `set()`, and the IndexNow
    payload did because a list comprehension does not dedupe. Counting rather
    than silently absorbing matters: a count that starts climbing is the signal
    that an upstream list has grown a repeat.
    """

    result = universe(pages=["/about", "/sports-edge", "/sports-edge", "/about"])

    assert result.urls == ["https://pulsesoc.com/about", "https://pulsesoc.com/sports-edge"]
    assert result.duplicates == 2
    assert result.included[PAGES] == 2


def test_two_paths_that_canonicalise_together_count_as_one():
    """Dedupe is on the submitted URL, not on the input path.

    `sitemap_xml` dedupes on the input path and so cannot see a collision it
    emits. Keying on the path here would reproduce that blind spot in a channel
    where a duplicate also corrupts per-URL diagnostics.
    """

    result = universe(pages=["/pulse/marketplace"], products=[("/pulse/marketplace", "")])

    assert result.total == 1
    assert result.duplicates == 1


def test_the_payload_is_byte_stable_across_calls():
    """Same state in, same bytes out, or the next agent cannot diff our work."""

    sources = {
        PAGES: sorted({"/about", "/sports-edge", "/help"}),
        PRODUCTS: [("/pulse/marketplace/35", ""), ("/pulse/marketplace/36", "")],
    }

    first = owd.payload(sources, "local")
    second = owd.payload(sources, "local")

    assert first == second
    assert first["urlList"] == second["urlList"]


# ---------------------------------------------------------------------------
# The gates this channel depends on but does not own
# ---------------------------------------------------------------------------


def test_held_listings_are_excluded_by_the_lifecycle_predicate_not_by_content_eligibility():
    """The one refactor that would silently publish held inventory to search.

    `content_eligibility` treats `{draft, pending, scheduled, archived}` as
    not-published. That set does not contain `held`, so a held record **passes**
    it -- asserted below so the claim is measured rather than remembered.

    Marketplace listings are safe today only because they never reach that
    function: they go through `public_sql`, which restricts status to
    published/live/active. The protection is real but it is incidental to which
    function you call, so a reasonable-looking consolidation onto "one
    eligibility function" would open it.
    """

    held = {"visibility": "public", "moderation_status": "approved", "status": "held", "body": "x" * 300}
    assert sv.content_eligibility(held).sitemap_eligible, "premise: held passes the content gate"

    predicate = marketplace_listing_lifecycle.public_sql("l", "ms").lower()
    assert "'published','live','active'" in predicate.replace(" ", "")
    assert "held" not in predicate
    assert "approval_status" in predicate and "'approved'" in predicate


@pytest.mark.parametrize("visibility", ["followers", "private", "unlisted", ""])
def test_a_post_that_is_not_public_fails_the_row_level_gate(visibility):
    """The path check is not enough; posts need the per-row verdict.

    `/pulse/post/5` is eligible as a *path* for every post, public or not. Only
    the record form of the gate can tell them apart, which is why the entry
    builder passes the row and why dropping to a bare path check is a mutation
    worth catching.
    """

    row = {"visibility": visibility, "moderation_status": "approved", "body": "x" * 300}

    assert sv.sitemap_eligible("/pulse/post/5"), "premise: the path alone is eligible"
    assert not sv.sitemap_eligible("/pulse/post/5", row)


# ---------------------------------------------------------------------------
# The outbound gate, which is off
# ---------------------------------------------------------------------------


def test_nothing_in_this_module_can_make_a_network_call():
    """The strongest available statement that this is read-only: no client exists.

    Asserted against the source rather than by mocking a transport, because a
    mock proves the call we thought to mock did not happen and says nothing
    about the one we did not.
    """

    import inspect

    source = inspect.getsource(owd)

    for forbidden in ("requests", "urllib", "httpx", "http.client", "socket", "urlopen"):
        assert forbidden not in source, f"{forbidden} appeared in a read-only module"


@pytest.mark.parametrize("env_value,mode,expected", [
    (None, "production", False),
    ("", "production", False),
    ("false", "production", False),
    ("0", "production", False),
    ("maybe", "production", False),
    ("true", "local", False),
    ("true", "staging", False),
    ("true", None, False),
    ("true", "production", True),
])
def test_the_outbound_gate_is_fail_closed(monkeypatch, env_value, mode, expected):
    """Absent, empty or unparseable means off. Only an explicit yes in production is yes.

    The row that matters most is `(None, "production", False)`. The gate cannot
    be built out of host correctness -- `canonical_url` hardcodes the production
    origin, so a staging container boots already emitting production URLs under a
    valid production key, and staging is indistinguishable from production by
    payload inspection. The usual tell does not exist here, so an unset
    environment has to mean no.
    """

    monkeypatch.delenv(owd.SUBMIT_ENABLED_VAR, raising=False)
    if env_value is not None:
        monkeypatch.setenv(owd.SUBMIT_ENABLED_VAR, env_value)

    assert owd.outbound_enabled(mode) is expected


def test_the_payload_reports_that_no_submitter_exists():
    diagnostics = owd.payload({PAGES: ["/about"]}, "production")["diagnostics"]

    assert diagnostics["outboundImplemented"] is False
    assert diagnostics["outboundEnabled"] is False
    assert diagnostics["bingCoverage"] == "unknown"


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


def test_the_payload_declares_itself_a_universe_and_not_a_change_batch():
    """The distinction that keeps this from becoming outbound noise.

    What the endpoint serves is every eligible URL at this instant. Submitting
    that on a schedule as though it were the set that *changed* would send the
    same unchanged URLs repeatedly and make the submission count a function of
    the cron interval. The label is in the payload so the distinction survives
    contact with whoever reads it next.
    """

    assert owd.payload({PAGES: ["/about"]}, "local")["payloadKind"] == "eligible-universe"


def test_the_protocol_fields_are_present_and_self_consistent():
    payload = owd.payload({PAGES: ["/about"]}, "local")

    assert payload["host"] == sv.CANONICAL_HOST
    assert payload["keyLocation"] == f"{sv.CANONICAL_ORIGIN}{owd.KEY_PATH}"
    assert payload["submitEndpoint"] == "https://api.indexnow.org/indexnow"
    assert payload["urlList"]


def test_the_key_is_protocol_valid():
    """8-128 characters of `a-z A-Z 0-9 -`, and it must match the served file.

    This key is public protocol verification material, not a secret: the
    protocol requires it be fetchable by anyone at `/<key>.txt`, because that
    file is how the provider proves we control the host. A key that were secret
    could not work. Recorded here because a scanner will file it as a leak.
    """

    assert 8 <= len(owd.KEY) <= 128
    assert all(character.isalnum() or character == "-" for character in owd.KEY)

    served = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "indexnow-key.txt")
    with open(served, encoding="utf-8") as handle:
        assert handle.read().strip() == owd.KEY


def test_diagnostics_carry_counts_and_reasons_but_never_an_entity():
    """The interesting exclusions are the private ones; naming them publishes them.

    `/api/indexnow` is anonymous and unauthenticated. "4 excluded: not
    published" is useful and safe; the ids or titles of those four are neither.
    """

    diagnostics = owd.payload(
        {PAGES: ["/about", "/signup", "/checkout"], POSTS: [("/pulse/post/99", "")]},
        "local",
    )["diagnostics"]

    assert diagnostics["candidateCount"] == 2
    assert diagnostics["includedByClass"] == {PAGES: 1, POSTS: 1, CATEGORIES: 0, PRODUCTS: 0}
    assert sum(diagnostics["excludedByReason"].values()) == 2

    for reason in diagnostics["excludedByReason"]:
        assert "99" not in reason and "/" not in reason


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------
#
# Everything above runs on injected entries and proves the gates. None of it
# proves the route is connected to anything, and a route that reads the wrong
# source still returns valid JSON, a 200, and a fully eligible payload. These
# import bot, which is slow, and they are the tests that would have caught the
# original defect.


@pytest.mark.parametrize("entity_class,builder_name", [
    (POSTS, "pulse_public_entries"),
    (PRODUCTS, "marketplace_public_entries"),
    (CATEGORIES, "marketplace_category_entries"),
])
def test_each_class_reads_the_same_builder_the_sitemap_reads(monkeypatch, entity_class, builder_name):
    """Not an equivalent query -- the same call.

    A second query here would be free to disagree with the sitemap about what is
    public, and for departments it would be free to disagree about *spelling*:
    `marketplace_seo` picks each department's slug by majority vote across the
    catalogue, so a differently-filtered read can flip the slug and submit a URL
    the live page calls an unknown category and serves `noindex`.

    Asserted by substituting the builder for a sentinel rather than by comparing
    its output against itself. The comparison form looks stronger and is
    vacuous: on a database with no posts and no catalogue both sides are `[]`,
    so it passes against a source list that was deleted outright. That is how
    this test read until a mutation run showed it surviving the removal of all
    three classes.
    """

    import bot

    sentinel = [(f"/sentinel/{entity_class}", "2026-01-01")]
    monkeypatch.setattr(bot, builder_name, lambda *args, **kwargs: list(sentinel))

    assert bot.open_web_candidate_sources()[entity_class] == sentinel


def test_the_pages_class_unions_exactly_what_the_pages_sitemap_unions():
    """The drift this closed: `/api/indexnow` read one of the three lists.

    `/sitemap-pages.xml` unions `all_public_paths()` with the learn pages and the
    ads landing pages. The payload read only the first, so it missed seven
    `/learn/` pages and four ads landing pages -- two lists, drifting, with
    neither one wrong on purpose.
    """

    import bot
    from services import seo_engine

    expected = sorted(
        set(bot.all_public_paths())
        | set(seo_engine.PUBLIC_LEARN_PATHS)
        | set(seo_engine.ADS_LANDING_PATHS)
    )

    assert bot.open_web_candidate_sources()[PAGES] == expected


@pytest.mark.parametrize("sitemap", [
    "/sitemap-pages.xml",
    "/sitemap-posts.xml",
    "/sitemap-categories.xml",
    "/sitemap-products.xml",
])
def test_every_url_in_a_sitemap_is_also_a_submission_candidate(sitemap):
    """The coverage invariant, and it holds whether or not anything is seeded.

    A sitemap and this payload answer the same question on different schedules:
    one is what we publish for a crawler to come and find, the other is what we
    push when it changes. A URL eligible for one and not the other is a
    contradiction rather than a configuration, and the contradiction is the
    normal state of two lists maintained separately.

    Asserted per child rather than over the union so that a failure names which
    class fell out.
    """

    import re

    import bot

    client = bot.webhook_app.test_client()

    listed = re.findall(r"<loc>([^<]+)</loc>", client.get(sitemap).get_data(as_text=True))
    submitted = set(client.get("/api/indexnow").get_json()["urlList"])

    missing = [url for url in listed if url not in submitted]
    assert not missing, f"{sitemap} publishes URLs the fast channel never submits: {missing}"


def test_the_route_makes_no_outbound_request_and_says_so():
    import bot

    payload = bot.webhook_app.test_client().get("/api/indexnow").get_json()

    assert payload["diagnostics"]["outboundImplemented"] is False
    assert payload["diagnostics"]["outboundEnabled"] is False
    assert payload["payloadKind"] == "eligible-universe"
    assert len(payload["urlList"]) == len(set(payload["urlList"])), "duplicate in the live payload"


def test_the_withheld_classes_say_why_rather_than_being_absent():
    """A class missing from a list is indistinguishable from a class forgotten."""

    withheld = owd.payload({PAGES: ["/about"]}, "local")["diagnostics"]["withheldClasses"]

    assert set(withheld) == {"live", "replays", "sitemaps", "feeds"}
    for reason in withheld.values():
        assert len(reason) > 20, "a withholding needs a reason, not a placeholder"
