"""The eight /features/<slug> pages, the hub above them, and the claims they make.

Two different kinds of failure are guarded here, and they pull in opposite
directions.

The first is *sameness*. This site already carries 108 pages built by
substituting a name into one template; measured against each other they are
99.2% and 98.9% textually identical, against 59.6% for two genuinely different
templates, and `services.search_visibility` now marks that whole family
`noindex,follow` because Google calls it scaled content abuse. Eight feature
pages are exactly the shape of thing somebody generates from a skeleton. The
similarity test below is the thing standing between a future edit and rebuilding
the problem we just finished removing -- on the pages that most need to rank.

The second is *overclaiming*. The copy was written against what the running code
does, and several of the most useful sentences on these pages are the ones that
say a thing is missing. Those sentences are load-bearing:

* Messages are not end-to-end encrypted. There is no cipher in this repo.
  Implying otherwise on a page Google indexes is a security claim we cannot
  honour, and it is the single worst thing that could regress here.
* Screen sharing is not implemented on calls or on live video.
* Group calls are gated off in production.
* Marketplace card checkout is hard-paused.
* There are no hashtags, no @-mentions, and no story highlights.

So the tests do not merely ban those phrases -- the pages have to *discuss*
them. They assert that wherever such a phrase appears, it appears negated. A
future edit that drops the "not" is the failure mode, and a plain substring ban
would have made writing the honest sentence impossible in the first place.

Run: python3 -m pytest tests/test_feature_pages.py
"""

import difflib
import itertools
import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="feature_pages_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import content as seo_content, features as seo_features  # noqa: E402
from services import app_promotion, search_visibility as sv  # noqa: E402


FEATURE_PATHS = [seo_features.canonical_path(f) for f in seo_features.FEATURES]
ALL_PATHS = seo_features.all_paths()

# Measured on 2026-09-18, the same way the 108 templated pages were measured:
# visible text only, `difflib.SequenceMatcher` ratio, every pair. The eight came
# out at 24.7% mean and 39.4% worst -- and that worst pair still shares the
# whole page shell, the eight-card sibling grid and the download section, so the
# body copy is further apart than the number suggests.
#
# The threshold is the 59.6% that two genuinely different templates scored,
# rather than a round number chosen to sit just above today's result. Anything
# at or above it is, by this site's own prior measurement, no longer two
# different pages.
MAX_PAIRWISE_SIMILARITY = 0.596


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def bodies(client):
    out = {}
    for path in ALL_PATHS:
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        out[path] = response.get_data(as_text=True)
    return out


def _visible(html):
    """Roughly what a reader sees: no script, no style, no tags."""

    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _graph(html):
    blob = re.search(r'type="application/ld\+json">(.*?)</script>', html, re.S)
    assert blob, "page shipped no JSON-LD"
    return json.loads(blob.group(1))["@graph"]


def _node(graph, type_name):
    found = [n for n in graph if n["@type"] == type_name]
    assert len(found) == 1, f"expected exactly one {type_name}, got {len(found)}"
    return found[0]


# ---------------------------------------------------------------------------
# The pages exist and are reachable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_PATHS)
def test_each_page_answers_an_anonymous_visitor(client, path):
    assert client.get(path).status_code == 200


def test_an_undefined_slug_is_a_404_and_not_a_soft_404(client):
    """`/features/<slug>` must not answer 200 for a slug with no content.

    Flask would otherwise be happy to hand any string to the handler. A page
    that renders an empty shell at 200 is a soft 404, and Google treats a
    section full of them as a quality signal about the whole section -- which
    would mean these eight pages paying for URLs nobody wrote.
    """

    for junk in ("nonexistent", "feed2", "CALLS", "../app"):
        assert client.get(f"/features/{junk}").status_code in (301, 308, 404), junk


def test_the_hub_took_over_the_path_from_the_crypto_landing_page(client, bodies):
    """`/features` used to render `seo_page.html` with copy about a Telegram
    crypto bot. The catch-all that served it still exists, so this asserts the
    static route wins rather than trusting Flask's ordering."""

    body = bodies["/features"]
    assert "Telegram companion" not in body
    assert "AI Crypto Intelligence Platform" not in body
    assert "features" not in seo_content.SEO_PAGES
    assert "<h1>What PulseSoc does</h1>" in body


# ---------------------------------------------------------------------------
# Sameness: the thing that made 108 other pages noindex
# ---------------------------------------------------------------------------


def test_no_two_feature_pages_are_near_duplicates_of_each_other(bodies):
    """The guard this file exists for. See MAX_PAIRWISE_SIMILARITY above.

    If this goes red, the fix is to write the page rather than to raise the
    threshold. Raising it is how the other 108 got here.
    """

    text = {path: _visible(bodies[path]) for path in FEATURE_PATHS}
    worst = max(
        (difflib.SequenceMatcher(None, text[a], text[b]).ratio(), a, b)
        for a, b in itertools.combinations(FEATURE_PATHS, 2)
    )
    ratio, a, b = worst
    assert ratio < MAX_PAIRWISE_SIMILARITY, (
        f"{a} and {b} are {ratio:.1%} identical, at or above the {MAX_PAIRWISE_SIMILARITY:.1%} "
        "floor two genuinely different templates scored on this site"
    )


@pytest.mark.parametrize("path", FEATURE_PATHS)
def test_each_page_carries_enough_of_its_own_writing_to_be_worth_indexing(bodies, path):
    """A page that is mostly shell is a thin page no matter how distinct it is."""

    assert len(_visible(bodies[path]).split()) >= 300, path


def test_every_page_has_its_own_title_and_description():
    titles = [f["title"] for f in seo_features.FEATURES]
    descriptions = [f["description"] for f in seo_features.FEATURES]
    assert len(set(titles)) == len(titles)
    assert len(set(descriptions)) == len(descriptions)
    for feature in seo_features.FEATURES:
        assert len(feature["description"]) <= 320, feature["slug"]


# ---------------------------------------------------------------------------
# Overclaiming: the sentences that have to keep their "not"
# ---------------------------------------------------------------------------


# A negation may sit on either side of the phrase, because these pages discuss
# each limit twice and in two grammars: a prose sentence puts the "not" first
# ("you cannot share your screen"), and an FAQ puts it after ("Can I share my
# screen on a live stream? No."). Checking only backwards failed every FAQ.
#
# The two windows are deliberately lopsided. Backwards is generous, because the
# negation can be several clauses back in prose. Forwards is short -- just past
# the question mark -- because a wide forward window would let an affirmative
# claim be excused by an unrelated "no" later in the paragraph, which is the
# regression this is supposed to catch.
_NEGATION_LOOKBEHIND = 60
_NEGATION_LOOKAHEAD = 25


def _mentions_are_all_negated(text, phrase, negators):
    """Every occurrence of `phrase` sits next to a negation.

    A flat ban on the phrase would forbid the honest sentence along with the
    dishonest one -- these pages have to be able to say "not end-to-end
    encrypted" -- so the check is on the negation rather than on the noun.
    """

    # Word boundaries rather than substrings. Matching on `"no "` silently
    # failed against "No." at the end of an FAQ answer, which is precisely the
    # sentence these pages are built around.
    pattern = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in negators))
    lowered = text.lower()
    for match in re.finditer(re.escape(phrase.lower()), lowered):
        before = lowered[max(0, match.start() - _NEGATION_LOOKBEHIND):match.start()]
        after = lowered[match.end():match.end() + _NEGATION_LOOKAHEAD]
        if not (pattern.search(before) or pattern.search(after)):
            return False, lowered[max(0, match.start() - 90):match.end() + 40]
    return True, ""


@pytest.mark.parametrize("path", ALL_PATHS + ["/app"])
def test_no_page_claims_end_to_end_encryption(client, path):
    """The one that matters most.

    There is no cipher implementation in this codebase. Messages travel over
    TLS and are stored in a form the operator can read. A page that says
    otherwise is not an SEO mistake, it is a security claim made to people
    deciding what to send.
    """

    text = _visible(client.get(path).get_data(as_text=True))
    ok, context = _mentions_are_all_negated(
        text, "end-to-end encrypted", ("not", "no", "never"),
    )
    assert ok, f"{path} appears to claim end-to-end encryption: ...{context}..."


@pytest.mark.parametrize("path", ALL_PATHS + ["/app"])
def test_no_image_alt_text_claims_encryption(client, path):
    """The test above could not have caught this, and did not.

    `_visible` strips tags, so an `alt` attribute is invisible to it -- and alt
    text is exactly where an image's claims get handed to Google. /app shipped
    "showing an encrypted, connected call timer" through that hole, describing a
    screenshot whose own pixels read "End-to-end encrypted". The image is gone
    now; this is the check that would have found it.

    Bare "encrypted" rather than the full phrase, because the alt text never said
    "end-to-end". Qualified forms stay legal: the honest sentence on
    /features/messages is "encrypted in transit".
    """

    html = client.get(path).get_data(as_text=True)
    alts = " || ".join(re.findall(r'\balt="([^"]*)"', html))
    ok, context = _mentions_are_all_negated(
        alts, "encrypted", ("not", "no", "never", "in transit", "tls"),
    )
    assert ok, f"{path} has alt text claiming encryption: ...{context}..."


def test_the_messages_page_states_the_encryption_position_outright(bodies):
    """Not merely "does not claim E2E" -- it has to say so, because the absence
    of a claim is not something a reader can notice."""

    text = _visible(bodies["/features/messages"])
    assert "not end-to-end encrypted" in text.lower()
    assert "encrypted in transit" in text.lower()


@pytest.mark.parametrize("path", ALL_PATHS)
def test_no_page_claims_screen_sharing(client, path):
    """`not_implemented` on both the call and the live path."""

    text = _visible(client.get(path).get_data(as_text=True))
    ok, context = _mentions_are_all_negated(
        text, "screen shar", ("cannot", "no", "not"),
    )
    assert ok, f"{path} appears to claim screen sharing: ...{context}..."


def test_the_calls_page_does_not_offer_group_calling(bodies):
    """`PULSE_GROUP_CALLS_ENABLED` is unset in production and
    `subflag_enabled` defaults it to false, so group calls are off. The audio
    and video subflags read the same way but every real call site passes
    `default=True`, which is why those are on and this one is not."""

    text = _visible(bodies["/features/calls"]).lower()
    assert "calls are between two people" in text
    ok, context = _mentions_are_all_negated(
        text, "group call", ("not", "no", "switched off", "cannot"),
    )
    assert ok, f"the calls page appears to offer group calling: ...{context}..."


def test_the_marketplace_page_leads_with_the_payment_pause(bodies):
    """`MARKETPLACE_CARD_PAYMENTS_ENABLED` is unset in production, and the flag
    fails closed, so a buyer cannot start a card checkout. A page that promises
    one sends somebody to a dead end with their wallet out."""

    text = _visible(bodies["/features/marketplace"]).lower()
    assert "temporarily" in text and "card" in text
    assert "stripe" not in text, "the marketplace page must not promise a card processor"


def test_the_app_page_no_longer_promises_stripe_marketplace_checkout(client):
    """/app said "with payment handled by Stripe" before the pause was known
    here. The card on /app is now generated from the same source as the feature
    page, so the two cannot disagree again."""

    text = _visible(client.get("/app").get_data(as_text=True)).lower()
    assert "payment handled by stripe" not in text


@pytest.mark.parametrize(
    "path,phrase,negators",
    [
        ("/features/feed", "hashtag", ("no", "not")),
        ("/features/creator-profiles", "story highlight", ("no", "not")),
    ],
)
def test_the_absent_features_are_named_as_absent(bodies, path, phrase, negators):
    """These are the things a visitor would otherwise discover after installing.
    Both pages mention them on purpose; the test is that they keep the "no"."""

    text = _visible(bodies[path])
    assert phrase in text.lower(), f"{path} no longer mentions {phrase}"
    ok, context = _mentions_are_all_negated(text, phrase, negators)
    assert ok, f"{path} appears to claim {phrase}: ...{context}..."


def test_nothing_on_these_pages_asserts_a_rating_or_a_download_count(bodies):
    """Same prohibition as /app, applied to the pages that link to it."""

    for path, body in bodies.items():
        for forbidden in ("aggregateRating", "ratingValue", "reviewCount",
                          "ratingCount", "downloadUrl", "UserDownloads"):
            assert forbidden not in body, f"{path}: {forbidden}"


# ---------------------------------------------------------------------------
# Discovery: indexability, canonicals, sitemap, structured data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_PATHS)
def test_each_page_asks_to_be_indexed_and_points_at_itself(bodies, path):
    body = bodies[path]
    assert sv.robots_meta(path) == sv.INDEX_DIRECTIVE
    assert f'name="robots" content="{sv.INDEX_DIRECTIVE}"' in body
    assert f'<link rel="canonical" href="{sv.CANONICAL_ORIGIN}{path}">' in body


@pytest.mark.parametrize("path", ALL_PATHS)
def test_each_page_is_submitted_to_the_sitemap(client, path):
    assert sv.sitemap_eligible(path), path
    assert path in seo_content.all_public_paths(), path
    body = client.get("/sitemap-pages.xml").get_data(as_text=True)
    assert f"<loc>{sv.CANONICAL_ORIGIN}{path}</loc>" in body, path


@pytest.mark.parametrize("path", FEATURE_PATHS)
def test_the_graph_says_the_page_sits_under_app_and_features(bodies, path):
    """The breadcrumb the visitor can see and the one Google reads come from
    different code, so they are asserted against each other rather than each
    being asserted alone."""

    graph = _graph(bodies[path])
    assert [n["@type"] for n in graph] == [
        "Organization", "WebSite", "MobileApplication", "WebPage",
        "BreadcrumbList", "FAQPage",
    ]
    crumbs = [(i["name"], i["item"]) for i in _node(graph, "BreadcrumbList")["itemListElement"]]
    assert [c[1] for c in crumbs] == [
        sv.CANONICAL_ORIGIN + "/",
        sv.CANONICAL_ORIGIN + "/app",
        sv.CANONICAL_ORIGIN + "/features",
        sv.CANONICAL_ORIGIN + path,
    ]
    visible = _visible(bodies[path])
    for name, _url in crumbs[1:]:
        assert name in visible or name == "PulseSoc for iPhone", name


@pytest.mark.parametrize("path", FEATURE_PATHS)
def test_every_faq_answer_in_the_schema_is_visible_on_the_page(bodies, path):
    """Structured data the page does not say is a rich-result violation, and
    the way it happens is two copies of the text."""

    body = bodies[path]
    for entry in _node(_graph(body), "FAQPage")["mainEntity"]:
        assert entry["name"] in body, entry["name"]
        assert entry["acceptedAnswer"]["text"] in body, entry["name"]


@pytest.mark.parametrize("path", ALL_PATHS)
def test_the_smart_app_banner_reaches_every_defined_feature_path(bodies, path):
    """`app_promotion` matches these by prefix because `services/` cannot import
    `seo/`. A prefix cannot prove it covers the eight slugs -- this can."""

    assert app_promotion.wants_smart_app_banner(path), path
    assert 'name="apple-itunes-app"' in bodies[path], path


def test_the_banner_prefix_does_not_leak_onto_an_unrelated_path():
    for path in ("/featured", "/features-old", "/feature"):
        assert not app_promotion.wants_smart_app_banner(path), path


# ---------------------------------------------------------------------------
# Internal linking
# ---------------------------------------------------------------------------


def test_the_hub_links_to_all_eight_and_app_links_to_the_hub(client, bodies):
    hub = bodies["/features"]
    for path in FEATURE_PATHS:
        assert f'href="{path}"' in hub, path
    assert 'href="/features"' in client.get("/app").get_data(as_text=True)


@pytest.mark.parametrize("path", FEATURE_PATHS)
def test_each_feature_page_links_to_the_other_seven(bodies, path):
    """Hub-and-spoke with no spoke-to-spoke edges makes the hub the only route
    between two sibling pages, which is a weaker link graph than it needs to be
    and a worse read."""

    body = bodies[path]
    for other in FEATURE_PATHS:
        if other == path:
            continue
        assert f'href="{other}"' in body, f"{path} does not link to {other}"


@pytest.mark.parametrize("path", ALL_PATHS)
def test_no_internal_link_on_these_pages_lands_on_a_login_wall(client, bodies, path):
    """A CTA that 302s an anonymous visitor to /login is the same species of
    false claim as a wrong price -- it just fails after the click."""

    for href in sorted(set(re.findall(r'href="(/[^"#?]*)"', bodies[path]))):
        status = client.get(href).status_code
        assert status == 200, f"{path} links to {href}, which answers {status}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
