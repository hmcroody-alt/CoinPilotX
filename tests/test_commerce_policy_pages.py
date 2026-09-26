"""The four commerce policy pages Merchant Center will not approve us without.

`/returns`, `/refund-policy`, `/shipping`, `/contact`.

These pages have a property the rest of the site does not: they are the only
pages here whose *absence* blocks revenue and whose *content* creates liability.
A 404 stops the Shopping account being approved. A page that promises what the
code does not do invites a chargeback the platform loses, and does it while
looking like a success, because Merchant Center's reviewer cannot tell the
difference. So both directions are guarded:

* Existence and crawlability, because that is the blocker
  (`docs/seo/01_merchant_center_feed.md`, blocker 1).
* Honesty, because the tempting version of each page passes review. There is no
  self-serve return flow in this codebase -- no `returned` order state, no
  returns table, no label generation -- only a buyer-callable dispute endpoint
  and an admin-issued refund. There are no site-wide delivery estimates,
  because `estimated_delivery` is free text most sellers leave blank. The pages
  say so in as many words, and the tests below assert that those sentences
  survive, the same way `tests/test_feature_pages.py` protects "messages are not
  end-to-end encrypted".

The sameness guard is the third thing, and it is here for the reason
`seo/commerce_policies.py` states: this site already carries 108 pages measuring
99.2% identical and `noindex` for it. Four pages off one template is that shape.

Run: python3 -m pytest tests/test_commerce_policy_pages.py
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

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="commerce_policy_pages_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import commerce_policies as cp  # noqa: E402
from seo import content as seo_content  # noqa: E402
from services import search_visibility as sv  # noqa: E402


PATHS = list(cp.all_paths())

# Measured on 2026-09-26. Worst pair is /returns vs /refund-policy at 27.5%,
# mean 24.9%, against the 59.6% two genuinely different templates scored on this
# site -- the same floor `tests/test_feature_pages.py` uses, so the two guards
# are calibrated against one number.
#
# The method is deliberately *not* identical next door, and the difference is
# worth recording because it makes this test stricter rather than looser.
# `difflib.SequenceMatcher` defaults to `autojunk=True`, which treats any
# character appearing in more than 1% of a sequence longer than 200 as junk --
# on prose that is most of the alphabet, and it suppresses the ratio hard.
# Reproduced on the feature pages: they score 40.3% with the default and 55.5%
# with `autojunk=False`. So the 39.4% recorded next door has far less headroom
# under the 59.6% floor than it appears to, and a guard that measures the
# suppressed number is measuring the heuristic as much as the pages. This one
# measures without it.
MAX_PAIRWISE_SIMILARITY = 0.596

# A page that is mostly shell is thin whatever else is true of it. The four run
# 343-423 words of their own copy today.
MIN_OWN_WORDS = 300


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def bodies(client):
    out = {}
    for path in PATHS:
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        out[path] = response.get_data(as_text=True)
    return out


def _visible(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _graph(html):
    blob = re.search(r'type="application/ld\+json">(.*?)</script>', html, re.S)
    assert blob, "page shipped no JSON-LD"
    return json.loads(blob.group(1))["@graph"]


def _types(graph):
    return [node["@type"] for node in graph]


# ---------------------------------------------------------------------------
# The blocker itself: these four URLs have to resolve
# ---------------------------------------------------------------------------


def test_all_four_paths_are_the_ones_merchant_center_asks_for():
    """Named here so a rename cannot quietly satisfy the test and not Google.

    Merchant Center looks for a return policy, a refund policy, shipping
    information and a contact method. These are the URLs submitted for review;
    changing one is a thing to do deliberately and then to re-verify in the
    Merchant Center account, not a refactor.
    """

    assert PATHS == ["/returns", "/refund-policy", "/shipping", "/contact"]


@pytest.mark.parametrize("path", PATHS)
def test_each_page_answers_an_anonymous_visitor(client, path):
    """No login, no redirect. A reviewer and a buyer both arrive signed out."""

    response = client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"


@pytest.mark.parametrize("path", PATHS)
def test_each_page_is_indexable_and_enters_the_sitemap(client, path):
    decision = sv.classify(path)
    assert decision.indexable, f"{path} classified {decision.directive}: {decision.reason}"
    assert path in seo_content.all_public_paths(), f"{path} is not in all_public_paths()"
    assert f"<loc>https://pulsesoc.com{path}</loc>" in client.get(
        "/sitemap-pages.xml").get_data(as_text=True)


@pytest.mark.parametrize("path", PATHS)
def test_nothing_hangs_below_a_policy_page(client, path):
    """`/returns/anything` must 404 rather than render the policy again.

    These are four fixed paths, not a `<slug>` rule, so this is asserting that
    nobody later turns them into one and inherits the soft-404 problem
    `/features/<slug>` had to be guarded against.
    """

    assert client.get(f"{path}/anything").status_code in (301, 308, 404)


def test_every_policy_page_is_linked_from_the_shared_footer(client):
    """A sitemap is a suggestion; a link is a crawl path.

    Merchant Center's review starts from the landing pages, and Google finds
    pages by following links. Four URLs that exist only in the sitemap are the
    shape of thing that stays "Discovered - currently not indexed". The footer
    is in `_public_shell.html`, so this asserts through a page that is not one
    of the four.
    """

    body = client.get("/app").get_data(as_text=True)
    assert "<footer>" in body
    footer = body.split("<footer>", 1)[1]
    for path in PATHS:
        assert f'href="{path}"' in footer, f"{path} is not linked from the shared footer"


# ---------------------------------------------------------------------------
# Sameness
# ---------------------------------------------------------------------------


def test_no_two_policy_pages_are_near_duplicates_of_each_other(bodies):
    """If this goes red, write the page. Raising the threshold is how the other
    108 pages on this domain ended up noindex."""

    text = {path: _visible(bodies[path]) for path in PATHS}
    ratio, a, b = max(
        (difflib.SequenceMatcher(None, text[a], text[b], autojunk=False).ratio(), a, b)
        for a, b in itertools.combinations(PATHS, 2)
    )
    assert ratio < MAX_PAIRWISE_SIMILARITY, (
        f"{a} and {b} are {ratio:.1%} identical, at or above the "
        f"{MAX_PAIRWISE_SIMILARITY:.1%} floor two genuinely different templates "
        "scored on this site"
    )


@pytest.mark.parametrize("path", PATHS)
def test_each_page_carries_enough_of_its_own_writing(bodies, path):
    words = len(_visible(bodies[path]).split())
    assert words >= MIN_OWN_WORDS, f"{path} renders {words} words"


def test_every_page_has_its_own_title_description_and_h1():
    for field in ("title", "description", "h1", "lede"):
        values = [policy[field] for policy in cp.POLICIES]
        assert len(set(values)) == len(values), f"two policies share a {field}"


# ---------------------------------------------------------------------------
# Honesty: the half that passes review either way
# ---------------------------------------------------------------------------


def test_no_page_promises_a_returns_flow_that_does_not_exist(bodies):
    """There is no self-serve return in this codebase, so no page may imply one.

    What exists: `POST /api/business-os/marketplace/orders/<id>/dispute` for a
    buyer, an admin refund route, and `refunded`/`disputed` order states. What
    does not exist: a return request endpoint, a `returned` state, a returns
    table, or any label generation.

    The ban is on the *claim*, not the words -- `/returns` has to be able to say
    that it does not generate labels. So each phrase is required to appear
    negated where it appears at all, which is the pattern
    `tests/test_feature_pages.py` uses for "not end-to-end encrypted".
    """

    forbidden_claims = (
        "prepaid label",
        "prepaid return label",
        "print your label",
        "return label",
        "returns portal",
        "self-serve return",
        "one-click return",
        "instant refund",
        "guaranteed refund",
        "hassle-free",
        "no questions asked",
    )
    for path, html in bodies.items():
        text = _visible(html).lower()
        for claim in forbidden_claims:
            if claim not in text:
                continue
            window = text[max(0, text.find(claim) - 160):text.find(claim) + len(claim)]
            assert re.search(r"\b(no|not|never|cannot|without|deliberately)\b", window), (
                f"{path} uses {claim!r} without negating it; there is no "
                "self-serve returns flow in this codebase for it to describe"
            )


def test_the_returns_page_says_out_loud_what_is_missing(bodies):
    """The load-bearing sentences. A future edit that tidies these away turns an
    honest page into a false one, and nothing else would catch it."""

    text = _visible(bodies["/returns"]).lower()
    assert "no self-serve returns button" in text
    assert "no automatically generated shipping label" in text
    assert "open a dispute" in text, "the mechanism that does exist must be named"
    assert cp.SUPPORT_EMAIL in text


def test_no_page_invents_a_delivery_timeframe(bodies):
    """`merchant_center_feed` omits `g:shipping` rather than invent a number.

    A page that says "ships in 3-5 business days" while the feed declines to
    state a shipping cost is the same contradiction arriving through the other
    door, and Merchant Center's misrepresentation policy is enforced by
    comparing the two. `estimated_delivery` is per listing and mostly empty, so
    there is nothing site-wide to publish.
    """

    invented = re.compile(
        r"(ships?|delivers?|arrives?|delivery)\b[^.]{0,40}?\b\d+\s*(?:-|–|to)\s*\d+\s*"
        r"(business\s+)?days",
        re.I,
    )
    for path, html in bodies.items():
        text = _visible(html)
        for match in invented.finditer(text):
            window = text[max(0, match.start() - 200):match.end()]
            assert re.search(r"\b(no|not|never|would be|invent)\b", window, re.I), (
                f"{path} states a delivery timeframe: {match.group(0)!r}. No column "
                "in this codebase holds a site-wide one."
            )


def test_the_shipping_page_explains_the_absence_rather_than_hiding_it(bodies):
    text = _visible(bodies["/shipping"]).lower()
    assert "no delivery estimates on this page" in text
    assert "shown before you pay" in text or "before you authorise payment" in text


def test_the_return_window_is_stated_once_and_agrees_with_itself(bodies):
    """One number, from `docs/marketplace_returns_refunds.md`.

    Two windows on two pages is the classic way this kind of page rots, and a
    buyer only has to find the longer one for it to be the one that counts.

    The patterns match a *return window* specifically rather than every number
    followed by "days", which is a deliberate narrowing and the first draft of
    this test got it wrong: `/shipping` contains the string "ships within 3-5
    days" inside the sentence explaining that such a claim would be invented, and
    a bare number match reads a quoted counter-example as a promise. Banning the
    number would mean the page could not argue against it.
    """

    assert cp.RETURN_WINDOW_DAYS == 14
    for path, html in bodies.items():
        text = _visible(html)
        windows = set(re.findall(r"(\d+)[-\s]day (?:return|window)", text, re.I))
        windows |= set(re.findall(
            r"within (\d+) days of (?:delivery|receipt|purchase)", text, re.I))
        assert windows <= {str(cp.RETURN_WINDOW_DAYS)}, (
            f"{path} states return windows {sorted(windows)}; the policy is "
            f"{cp.RETURN_WINDOW_DAYS} days"
        )


def test_the_refund_page_does_not_promise_an_outcome(bodies):
    text = _visible(bodies["/refund-policy"]).lower()
    assert "no guaranteed outcome" in text
    assert "original payment method" in text
    assert "exactly once" in text, "idempotence is the substantive claim on this page"
    assert "is not a bank" in text


def test_the_contact_page_publishes_only_channels_that_exist(bodies):
    """Email, because email is what is monitored.

    No phone number: Merchant Center accepts email, and a number nobody answers
    is a worse contact method than none. The regex is deliberately loose -- it is
    looking for anything a buyer would dial.
    """

    html = bodies["/contact"]
    text = _visible(html)
    assert cp.SUPPORT_EMAIL in text
    assert f'href="mailto:{cp.SUPPORT_EMAIL}"' in html
    assert "security@pulsesoc.com" in text
    phones = re.findall(r"(?:\+?\d[\d\-.\s()]{8,}\d)", text)
    assert not phones, f"/contact publishes something dialable: {phones}"


def test_the_company_that_is_liable_is_named_by_its_legal_name(bodies):
    """`name` is PulseSoc, `legalName` is CoinPlotXAI Inc., both are true, and
    `tests/test_site_identity.py` pins the split -- Apple records the App Store
    seller as COINPLOTXAI INC. Where these pages state who is liable and who
    operates the platform, the legal name is the correct one and must not be
    "corrected" to the brand."""

    assert cp.LEGAL_NAME == "CoinPlotXAI Inc."
    assert cp.LEGAL_NAME in _visible(bodies["/refund-policy"])
    assert cp.LEGAL_NAME in _visible(bodies["/contact"])


# ---------------------------------------------------------------------------
# Structured data: only nodes that correspond to something on the page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_the_graph_claims_nothing_the_page_is_not_about(bodies, path):
    """No `MobileApplication`, no `Service`.

    `app_page_graph` attaches an app node to everything it builds and
    `schema_graph` attaches a `Service` with `serviceType` defaulting to "AI
    intelligence". Either one on a returns policy is a claim about a different
    product, and the reason `commerce_policy_graph` exists instead of a reuse.
    """

    types = _types(_graph(bodies[path]))
    assert "MobileApplication" not in types
    assert "Service" not in types
    assert types.count("Organization") == 1
    assert types.count("WebSite") == 1
    assert types.count("BreadcrumbList") == 1


@pytest.mark.parametrize("path", PATHS)
def test_the_webpage_node_describes_this_url(bodies, path):
    graph = _graph(bodies[path])
    canonical = f"https://pulsesoc.com{path}"
    pages = [n for n in graph if str(n.get("@id", "")).endswith("#webpage")]
    assert len(pages) == 1
    assert pages[0]["url"] == canonical
    assert pages[0]["@type"] == ("ContactPage" if path == "/contact" else "WebPage")
    assert f'<link rel="canonical" href="{canonical}">' in bodies[path]


@pytest.mark.parametrize("path", PATHS)
def test_the_response_is_cacheable_and_declares_its_robots_policy(client, path):
    response = client.get(path)
    assert response.headers["Cache-Control"] == "public, max-age=600"
    assert f'content="{sv.robots_meta(path)}"' in response.get_data(as_text=True)


def test_unknown_slugs_are_not_pages(bodies):
    """`commerce_policies.page` returns None rather than raising, so the route
    can 404. Matching `seo.features.detail_page` deliberately."""

    assert cp.page("nope", sv.canonical_url) is None
    assert cp.page("", sv.canonical_url) is None
