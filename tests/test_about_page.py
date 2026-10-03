"""`/about` -- the page that says what PulseSoc is.

This file exists because of how the old /about went wrong. Nothing broke. No
test failed. The page simply kept describing an educational crypto simulation
platform with Arena battles and a Scam Shield scanner for about a year after the
product became a social network with a marketplace in it, and the only thing
that would have caught it was somebody reading the page. One of its three
buttons pointed at `/scam-shield/scan`, which answers 404 in production.

So the assertions below are not about rendering. They are about three things a
future edit could quietly undo:

1. **The page is about this product.** Not a ban on the word "crypto" -- the
   crypto heritage is real, it is named on the page on purpose, and a legitimate
   secondary mention must stay legal. What is banned is the old *positioning*:
   the page's title, description, h1 and JSON-LD description are the fields that
   tell a search engine what PulseSoc is, and those have to say social commerce.

2. **The honest limits survive.** The page states that messages are not
   end-to-end encrypted, that calls are between two people, that there are no
   hashtags or @-mentions, and that profiles have no story highlights. These are
   the sentences a copy edit "tightens" first, and they are the ones a visitor
   makes a security decision on. As in `tests/test_feature_pages.py`, the test
   is not a substring ban -- the page has to *discuss* the limit, negated.
   Banning the phrase outright would make the honest sentence unwritable.

3. **Every link works for the visitor it is shown to.** Each internal href is
   fetched anonymously and must not 404 and must not redirect to /login. Most of
   this site's social surfaces are behind `pulse_social_shell`, so a reasonable-
   looking link to a reels or profile tab is a dead end for exactly the reader
   this page is for. That is how `/scam-shield/scan` shipped.

Run: python3 -m pytest tests/test_about_page.py

Alone, as CI runs it -- one process per file. Sharing a process with another
file that sets `DATABASE_URL` at import time points this one at that file's
empty database, and the link scan then reports `/pulse/marketplace: 503`, which
is the harness, not a dead CTA.
"""

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="about_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import schema as seo_schema  # noqa: E402
from services import search_visibility as sv  # noqa: E402


PATH = "/about"

#: The old page's own words, in the fields that position the product. Taken from
#: the f-string this page replaced (bot.py, before 2026-10-02) rather than
#: invented, so a revert is what fails -- not a paraphrase nobody would write.
OLD_POSITIONING = (
    "AI command center",
    "crypto learning",
    "AI crypto intelligence",
    "Arena",
    "Scam Shield",
    "virtual portfolio",
    "simulation-first",
    "virtual-dollar",
    "CoinPlotXAI is an educational",
)

#: Limits the page has to keep stating, each as (required sentence, affirmative
#: claims that would contradict it).
#:
#: Two assertions rather than one, because either alone is weak. Requiring the
#: negated sentence stops a copy edit from dropping the limit; banning the
#: affirmatives stops one from being added somewhere else on the page, where a
#: reader would hit it first. A plain substring ban on "end-to-end encrypted"
#: would have made the honest sentence unwritable, which is why it is not that.
HONEST_LIMITS = (
    (
        "messages are not end-to-end encrypted",
        ("is end-to-end encrypted", "are end-to-end encrypted",
         "end-to-end encrypted messaging", "we cannot read"),
    ),
    (
        "there are no hashtags or @-mentions",
        ("use hashtags", "tag people with @", "add hashtags"),
    ),
    (
        "no screen sharing",
        ("share your screen", "screen sharing is available", "you can screen share"),
    ),
    (
        "group calling is not something you can use today",
        ("start a group call", "group calls let you", "call several people"),
    ),
    (
        "no highlights row",
        ("pin highlights", "story highlights let", "add highlights to your profile"),
    ),
)


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def body(client):
    response = client.get(PATH)
    assert response.status_code == 200, f"{PATH} answered {response.status_code}"
    return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def head(body):
    return body.split("</head>", 1)[0]


@pytest.fixture(scope="module")
def visible(body):
    html = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


@pytest.fixture(scope="module")
def graph(body):
    blob = re.search(r'type="application/ld\+json">(.*?)</script>', body, re.S)
    assert blob, "the page shipped no JSON-LD"
    return json.loads(blob.group(1))["@graph"]


def _meta(head, name):
    match = re.search(
        rf'<meta\s+(?:name|property)="{re.escape(name)}"\s+content="([^"]*)"', head
    )
    assert match, f"the page declares no {name}"
    return match.group(1)


def _node(graph, type_name):
    found = [n for n in graph if n.get("@type") == type_name]
    assert len(found) == 1, f"expected exactly one {type_name}, got {len(found)}"
    return found[0]


# ---------------------------------------------------------------------------
# It answers an anonymous visitor, and says so to crawlers
# ---------------------------------------------------------------------------


def test_an_anonymous_visitor_gets_the_page(client):
    assert client.get(PATH).status_code == 200


def test_the_page_is_indexable(head):
    assert sv.is_indexable(PATH), "search_visibility stopped classifying /about as indexable"
    assert "noindex" not in _meta(head, "robots")


def test_there_is_exactly_one_canonical_and_it_is_this_page(body):
    canonicals = re.findall(r'<link rel="canonical" href="([^"]*)"', body)
    assert canonicals == [sv.canonical_url(PATH)], canonicals


# ---------------------------------------------------------------------------
# The old brand does not come back through the fields that position a product
# ---------------------------------------------------------------------------


def test_the_positioning_fields_describe_social_commerce(head, body, graph):
    title = re.search(r"<title>(.*?)</title>", body, re.S).group(1)
    h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    assert len(h1s) == 1, f"a page needs exactly one h1; found {len(h1s)}"

    positioning = {
        "<title>": title,
        "meta description": _meta(head, "description"),
        "og:description": _meta(head, "og:description"),
        "h1": h1s[0],
        "JSON-LD WebPage description": _node(graph, "AboutPage").get("description", ""),
    }

    offenders = {
        field: phrase
        for field, text in positioning.items()
        for phrase in OLD_POSITIONING
        if phrase.lower() in text.lower()
    }
    assert not offenders, (
        "the old crypto positioning is back in the fields that tell a search "
        f"engine what PulseSoc is: {offenders}\n\n"
        "Secondary mentions of the crypto heritage in the body copy are fine "
        "and deliberate -- these five fields are not body copy."
    )

    assert "social commerce" in " ".join(positioning.values()).lower(), (
        "none of the title, description, h1 or schema description says what "
        "this product is. That is the failure this page was rewritten to fix."
    )


def test_the_schema_claims_nothing_we_cannot_evidence(body, graph):
    """No ratings, no reviews, no founders, no awards, no headcount.

    Every one of these is a field Google will render in a rich result, and this
    repo has no data behind any of them. `aggregateRating` in particular is the
    one most likely to be added by somebody trying to improve the listing.
    """

    blob = json.dumps(graph).lower()
    for field in (
        "aggregaterating", "review", "founder", "award",
        "numberofemployees", "foundingdate", "pricerange",
    ):
        assert field not in blob, f"the /about graph claims {field} with nothing behind it"

    assert {n["@type"] for n in graph} == {
        "Organization", "WebSite", "AboutPage", "BreadcrumbList",
    }
    assert _node(graph, "Organization")["@id"] == f"{seo_schema.SITE_URL}/#organization", (
        "the Organization node forked from the canonical @id, so it now "
        "describes a second entity on the same domain instead of joining the one"
    )


# ---------------------------------------------------------------------------
# The honest limits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("required,contradictions", HONEST_LIMITS)
def test_the_page_still_states_its_limits(visible, required, contradictions):
    haystack = visible.lower()
    assert required in haystack, (
        f"/about no longer says {required!r}. This is not a formatting "
        "preference: a visitor who assumes a capability we do not have has made "
        "a decision on our behalf."
    )
    for claim in contradictions:
        assert claim not in haystack, (
            f"/about says {claim!r}, which contradicts {required!r} elsewhere on "
            "the same page"
        )


def test_the_page_makes_no_safety_guarantee(visible):
    """The old page's worst habit, and the sentence that refuses it.

    Worth noticing that "software keeps you safe" and "we detect every scam"
    are *on* this page -- inside the sentence saying we will not tell you
    either. A flat substring ban would fail against the most honest paragraph
    here, which is why the required refusal is asserted first and the ban list
    below holds only phrases no truthful sentence on this page needs.
    """

    haystack = visible.lower()
    assert "we will not tell you that software keeps you safe" in haystack, (
        "the sentence refusing to promise safety is gone. Everything else in "
        "the safety section reads as a guarantee without it."
    )

    for claim in (
        "protects you from", "keeps your money safe", "guaranteed",
        "100% secure", "completely secure", "fully encrypted", "bank-grade",
        "military-grade", "we verify every", "every seller is verified",
    ):
        assert claim not in haystack, f"/about now promises {claim!r}"


def test_pulsedrop_is_described_as_a_curator_and_not_a_seller(visible):
    """PulseDrop surfaces listings; it does not own stock or take the money.

    Saying otherwise would be a misrepresentation of who a buyer is contracting
    with, which is a consumer-law problem rather than a copy problem.
    """

    assert "PulseDrop" in visible
    window = visible[visible.index("PulseDrop"):][:700].lower()
    assert "not the seller" in window
    assert "does not hold stock" in window


def test_no_internal_signal_language_leaks(visible):
    blob = visible.lower()
    for term in (
        "risk_score", "fraud_score", "moderation_status", "trust score",
        "stripe", "railway", "webhook", "cron", "postgres", "drain_behind",
    ):
        assert term not in blob, f"/about names an internal implementation detail: {term!r}"


# ---------------------------------------------------------------------------
# Every link is reachable by the reader it is shown to
# ---------------------------------------------------------------------------


def test_no_cta_is_dead_or_behind_the_login_wall(client, body):
    """A 302 to /login is a dead CTA on a page written for people with no account."""

    hrefs = sorted({
        h for h in re.findall(r'href="(/[^"#?]*)"', body)
        if not h.startswith("/static/")
    })
    assert len(hrefs) >= 8, f"the link scan found only {hrefs}; it is broken"

    broken = {}
    for href in hrefs:
        response = client.get(href)
        if response.status_code >= 400:
            broken[href] = response.status_code
        elif response.status_code in (301, 302, 303, 307, 308):
            target = response.headers.get("Location", "")
            if "/login" in target or "/signup" in target:
                broken[href] = f"{response.status_code} -> {target}"

    assert not broken, (
        "these links on /about do not work for an anonymous visitor:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in sorted(broken.items()))
        + "\n\nMost social surfaces here redirect a logged-out reader to /login. "
        "Link the public /features/<slug> page instead of the in-product tab."
    )


def test_the_app_store_link_comes_from_the_single_authority(body):
    """One App Store URL for the whole site, built by services.app_links."""

    from services import app_links

    assert app_links.app_store_url() in body, (
        "/about hardcoded an App Store URL instead of using the app-link macros"
    )
