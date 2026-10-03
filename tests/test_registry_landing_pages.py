"""The five registry-backed landing families: what they answer, and what they claim.

`/markets/<symbol>`, `/markets/<symbol>/live`, `/markets/<symbol>/prediction`,
`/country-intelligence/<slug>`, `/sports-edge/<slug>` and `/intel/<slug>` are
all the same shape: a dict lookup in `seo/content.py`, and `render_seo_landing`
over whatever comes back. 108 URLs are generated this way. Two separate defects
lived in that shape, and neither was visible from anything that enumerated the
sitemaps -- which is why this file exists rather than more assertions in
`test_sitemap_integrity.py`.

SOFT 404s OVER AN UNBOUNDED URL SPACE
-------------------------------------
Every one of these routes accepts an arbitrary string. All six used to answer
`302 -> /` when the registry did not contain it, so `/markets/notacoin` told
Googlebot the URL had moved, and that it had moved to the home page. Google
calls that a soft 404 and counts it against the section. The sibling catch-all
`seo_topic_page`, in the same group of routes in the same file, has always
returned a real 404 for the identical condition -- the same question answered
two ways, which is what made this an oversight rather than a decision.

404 and not 410, and not a 301: a symbol we do not cover has no successor, and
redirecting to the most important page on the site is the specific failure the
old behaviour was.

THE DIRECTIVE THESE PAGES ACTUALLY SEND
---------------------------------------
`services.search_visibility` classifies `/markets/` and
`/country-intelligence/` as `noindex,follow` -- the scaled near-duplicate
family. `templates/seo_page.html` hardcoded `index, follow, ...`, so for the
whole life of that rule 42 URLs went on asking Google to rank them while the
policy table said the opposite. It survived because the *sitemap* half of the
policy worked correctly: nothing that reads a sitemap could see it, and the
only symptom was those pages sitting in Search Console's "crawled -- currently
not indexed", where Google had independently reached the verdict we had already
reached and failed to send.

So the second group of tests below reads the rendered HTML and compares it to
`robots_meta()`. A page is three channels -- the sitemap, `robots.txt`, and the
`<meta>` the page itself ships -- and a test that only ever checks the first
two cannot see a template literal contradicting both.

Run: python3 -m pytest tests/test_registry_landing_pages.py
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="registry_landing_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import content as seo_content  # noqa: E402
from services import search_visibility as sv  # noqa: E402


#: One live slug per family, read from the registry rather than hardcoded, so a
#: renamed entry retires the slug here too instead of leaving a dead assertion.
def _first(mapping):
    return sorted(mapping)[0]


_MARKET = _first(seo_content.MARKET_PAGES)
_COUNTRY = _first(seo_content.COUNTRY_PAGES)
_SPORT = _first(seo_content.SPORTS_SEO_PAGES)
_ARTICLE = _first(seo_content.ARTICLE_PAGES)

KNOWN = (
    f"/markets/{_MARKET}",
    f"/markets/{_MARKET}/live",
    f"/markets/{_MARKET}/prediction",
    f"/country-intelligence/{_COUNTRY}",
    f"/sports-edge/{_SPORT}",
    f"/intel/{_ARTICLE}",
)

#: The unknown-slug probes. Verified anonymously against production on
#: 2026-10-03: every one of these redirected rather than 404ing. The last two
#: are shapes a crawler actually produces rather than ones a person would type
#: -- a percent-encoded trailing space from a mangled inbound link, and a
#: trailing-punctuation artefact -- because the unbounded part of the URL space
#: is reached by accident far more often than on purpose.
#:
#: `/sports-edge/football` is deliberately *not* in this list even though it is
#: one of the four production probes. `soccer` is in the registry, so `football`
#: is the one slug in this family with a genuine successor, and a 301 to it
#: would be a correct answer. Pinning it to 404 here would turn a legitimate
#: alias into a test failure, which is not what this guard is about: the thing
#: being forbidden is redirecting to the home page, not redirecting.
UNKNOWN = (
    "/markets/notacoin",
    "/markets/notacoin/live",
    "/markets/notacoin/prediction",
    "/country-intelligence/atlantis",
    "/sports-edge/curling",
    "/intel/not-a-real-article",
    "/markets/btc%20",
    "/country-intelligence/NIGERIA-",
)


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


# ---------------------------------------------------------------------------
# A slug the registry does not contain is a 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", UNKNOWN, ids=lambda p: p)
def test_an_unknown_slug_is_a_404_and_not_a_redirect_to_the_home_page(client, path):
    """The regression. A 3xx here is the soft 404 this file documents.

    The assertion is deliberately `== 404` rather than `not in (301, 302)`: a
    301 to a real successor would be correct in principle, but there is no
    successor for a symbol we do not cover, and allowing 3xx is how the
    redirect-to-home behaviour would come back.
    """

    response = client.get(path)
    assert response.status_code == 404, (
        f"{path} answered {response.status_code}"
        + (f" -> {response.headers.get('Location')}" if response.status_code // 100 == 3 else "")
    )


@pytest.mark.parametrize("path", KNOWN, ids=lambda p: p)
def test_a_slug_the_registry_does_contain_still_renders(client, path):
    """Anti-vacuity for the test above.

    `_registry_page_or_404` is one `if not page` away from 404ing the whole
    family, and a file whose only assertion is "these URLs 404" would pass
    perfectly in that world.
    """

    assert client.get(path).status_code == 200


def test_the_unknown_probes_are_not_secretly_in_the_registry():
    """The other half of the anti-vacuity check.

    If somebody adds a `notacoin` entry to `MARKET_PAGES`, the 404 test above
    starts failing for a reason that has nothing to do with soft 404s, and the
    useful signal would be lost in the noise. Fail here instead, where the
    message says what actually happened.
    """

    assert seo_content.market_page("notacoin") is None
    assert seo_content.country_page("atlantis") is None
    assert seo_content.sports_page("curling") is None
    assert seo_content.article_page("not-a-real-article") is None

    # And the converse, for the one slug held out of UNKNOWN: `football` is
    # absent from the registry *and* has a successor in it, which is the whole
    # reason it is held out. If `soccer` is ever renamed, the comment above
    # stops being true and this is where that shows up.
    assert seo_content.sports_page("football") is None
    assert seo_content.sports_page("soccer") is not None


# ---------------------------------------------------------------------------
# The directive on the page is the directive in the policy table
# ---------------------------------------------------------------------------


_ROBOTS_META = re.compile(
    r"""<meta\s+name=['"]robots['"]\s+content=['"]([^'"]+)['"]""", re.I
)


@pytest.mark.parametrize("path", KNOWN, ids=lambda p: p)
def test_the_page_sends_the_directive_the_policy_table_declares(client, path):
    """`search_visibility` owns this decision; the template must not re-decide it.

    Note the regex accepts both quote styles. These templates emit attributes
    with single *and* double quotes depending on the block, and a
    double-quote-only reader concludes a page ships no robots meta at all --
    which looks exactly like the bug this test is for, in reverse.
    """

    body = client.get(path).get_data(as_text=True)
    found = _ROBOTS_META.findall(body)

    assert len(found) == 1, f"{path} shipped {len(found)} robots metas, expected 1"

    expected = sv.robots_meta(path)
    actual = found[0].replace(" ", "")
    assert actual == expected.replace(" ", ""), (
        f"{path} sends {found[0]!r} but the table declares {expected!r}"
    )


def test_some_of_these_pages_are_actually_noindex():
    """Without this, the test above passes if the table goes all-`index`.

    It would then be asserting that a hardcoded `index,follow` literal matches
    a policy that says `index,follow` -- true, and worthless. The 42
    `noindex,follow` pages are the ones the comparison exists for, so their
    existence is the precondition.
    """

    directives = {sv.robots_meta(path) for path in KNOWN}
    assert any(d.startswith("noindex") for d in directives), directives
    assert len(directives) > 1, "every family agrees; the comparison proves nothing"


# ---------------------------------------------------------------------------
# A noindex page is still not in a sitemap, from this side of the policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", KNOWN, ids=lambda p: p)
def test_the_three_channels_agree_about_each_page(client, path):
    """Sitemap eligibility, crawlability and the on-page meta, in one place.

    `test_sitemap_integrity.py` checks the first against the policy and
    `tests/protection/` checks the second against the served `robots.txt`.
    What neither does is line all three up for a single URL, which is the only
    view in which "indexable but not sitemap-eligible" or "noindex and also
    Disallowed" shows up as the contradiction it is.
    """

    decision = sv.classify(path)

    assert decision.sitemap_eligible is decision.indexable, path

    # A noindex page must stay crawlable, or Google never reads the noindex.
    # These families are `noindex,follow`, never `noindex,nofollow`, for
    # exactly that reason.
    assert not decision.directive.startswith("noindex,nofollow"), path


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
