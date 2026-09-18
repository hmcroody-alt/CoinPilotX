"""The claims /app makes about the iPhone app, and who can check them.

Structured data is a set of assertions to a search engine. The old
`software_schema()` asserted that PulseSoc was a `FinanceApplication` running on
"Telegram, Web, PWA" and costing $14.99 -- four statements, all false, describing
the Telegram crypto bot this company used to be. None of them was wrong when it
was written; they went stale and nothing noticed, because nothing here compared
them to anything outside the repo.

So the constants in `seo/schema.py` are pinned below against the source they
were read from: Apple's own record of the listing
(`https://itunes.apple.com/lookup?id=6777591572`, read 2026-09-18). A version
bump that leaves them stale fails here rather than quietly asserting the wrong
version to Google. The assertions say *what claim* each value is, because the
next person to see this file red needs to know what to go and check.

The second half is about the page itself. `/app` used to 302 every anonymous
visitor to /signup -- including Googlebot, which is why the one URL on this
domain whose subject is the app was ineligible to rank for the app's own name.
It now branches on authentication. That branch is the thing most worth guarding:
a regression that re-gates it is invisible to anyone who is signed in, which is
everyone who would notice.

Run: python3 -m pytest tests/test_app_schema.py
"""

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_schema_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import schema as seo_schema  # noqa: E402
from services import app_links, app_promotion, search_visibility as sv  # noqa: E402


APP_STORE_ID = "6777591572"


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def landing(client):
    response = client.get("/app")
    assert response.status_code == 200
    return response


@pytest.fixture(scope="module")
def graph(landing):
    body = landing.get_data(as_text=True)
    blob = re.search(r'type="application/ld\+json">(.*?)</script>', body, re.S)
    assert blob, "the page shipped no JSON-LD at all"
    return json.loads(blob.group(1))["@graph"]


def _node(graph, type_name):
    found = [n for n in graph if n["@type"] == type_name]
    assert len(found) == 1, f"expected exactly one {type_name}, got {len(found)}"
    return found[0]


# ---------------------------------------------------------------------------
# The constants, and what each one is a claim about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constant,expected,claim",
    [
        ("APP_VERSION", "1.0.2", "the version currently on the App Store"),
        ("APP_MINIMUM_IOS", "15.1", "Apple's minimumOsVersion for the listing"),
        ("APP_FIRST_RELEASED", "2026-07-01", "Apple's releaseDate for version 1.0"),
        ("APP_CONTENT_RATING", "4+", "Apple's trackContentRating"),
    ],
)
def test_each_app_store_constant_still_matches_what_apple_records(constant, expected, claim):
    """Red here means the listing moved, not that the test is wrong.

    Re-read `https://itunes.apple.com/lookup?id=6777591572` and update the
    constant. Do not update the expectation to match the code -- that inverts
    the direction of the check and is how the previous values survived a whole
    product pivot.
    """

    assert getattr(seo_schema, constant) == expected, claim


def test_the_store_url_is_never_spelled_out_a_second_time():
    """One definition, or the badge and the schema drift apart.

    `services/app_links.py` owns the listing URL. Both the `installUrl` in the
    schema and the download button in the template read from it.
    """

    assert APP_STORE_ID in app_links.app_store_url()
    assert seo_schema.mobile_app_schema()["installUrl"] == app_links.app_store_url()


# ---------------------------------------------------------------------------
# What the schema says about the app
# ---------------------------------------------------------------------------


def test_the_app_is_typed_as_the_thing_it_is(graph):
    app = _node(graph, "MobileApplication")
    assert app["applicationCategory"] == "SocialNetworkingApplication"
    assert "Finance" not in app["applicationCategory"]
    assert app["operatingSystem"].startswith("iOS ")
    assert "Telegram" not in app["operatingSystem"]
    assert "PWA" not in app["operatingSystem"]


def test_the_app_is_advertised_at_the_price_apple_charges_for_it(graph):
    """Zero. The $14.99 belongs to the subscription, and saying so on the
    application node is a price claim Google may surface in a result."""

    offer = _node(graph, "MobileApplication")["offers"]
    assert offer["price"] == "0"
    assert offer["priceCurrency"] == "USD"


def test_the_subscription_price_still_matches_the_one_the_site_quotes():
    """The two live in different modules and must not drift."""

    assert seo_schema.product_schema()["offers"]["price"] == "14.99"
    assert "14.99" in bot.PRO_PRICE_MONTHLY


def test_no_rating_review_or_download_count_is_asserted_anywhere(landing):
    """The prohibition that has no upside to breaking.

    Apple holds two ratings for this listing. Restating them on our own domain
    is sourced and still misleading, and Google's guidance is that a rating in
    structured data comes from reviews the site itself collects. A missing star
    rating costs nothing we currently qualify for; a fabricated one is a manual
    action.
    """

    body = landing.get_data(as_text=True)
    for forbidden in ("aggregateRating", "ratingValue", "reviewCount", "ratingCount",
                      "downloadUrl", "interactionCount", "UserDownloads"):
        assert forbidden not in body, forbidden


def test_the_graph_asserts_only_nodes_that_correspond_to_something(graph):
    """`schema_graph()` attaches a `Service` to every page it renders, with
    `serviceType` defaulting to "AI intelligence". That is a different claim
    than "this is a free social app", so /app composes its own graph."""

    types = [n["@type"] for n in graph]
    assert types == ["Organization", "WebSite", "MobileApplication", "WebPage",
                     "BreadcrumbList", "FAQPage"]


def test_every_faq_answer_in_the_schema_is_also_visible_on_the_page(landing, graph):
    """Structured data that the page does not say is a rich-result violation,
    and the way it happens is two copies of the text."""

    body = landing.get_data(as_text=True)
    for entry in _node(graph, "FAQPage")["mainEntity"]:
        assert entry["name"] in body, entry["name"]
        assert entry["acceptedAnswer"]["text"] in body, entry["name"]


def test_the_android_answer_stays_honest(graph):
    """There is no Android app. A page that implies one converts a visitor into
    a search for an app that does not exist."""

    answers = {e["name"]: e["acceptedAnswer"]["text"] for e in _node(graph, "FAQPage")["mainEntity"]}
    android = next(a for q, a in answers.items() if "Android" in q)
    assert android.lower().startswith("not today")


# ---------------------------------------------------------------------------
# The auth branch
# ---------------------------------------------------------------------------


def test_an_anonymous_visitor_gets_the_page_rather_than_a_redirect(landing):
    """The single regression this whole file exists to catch."""

    assert landing.status_code == 200


def test_the_page_asks_to_be_indexed_and_points_at_itself(landing):
    body = landing.get_data(as_text=True)
    assert f'<link rel="canonical" href="{sv.CANONICAL_ORIGIN}/app">' in body
    assert sv.robots_meta("/app") == sv.INDEX_DIRECTIVE
    assert f'name="robots" content="{sv.INDEX_DIRECTIVE}"' in body


@pytest.mark.parametrize("path", ["/command-center", "/intelligence", "/dashboard/intelligence"])
def test_the_sibling_paths_did_not_become_public_along_with_it(client, path):
    """They shared a handler with /app until this change. Splitting the route
    is what keeps them private; the policy table is what keeps them out of the
    index if someone ever renders a shell to anonymous users there."""

    assert client.get(path).status_code == 302
    assert not sv.is_indexable(path)
    assert not sv.sitemap_eligible(path)


def test_googlebot_is_shown_what_a_logged_out_person_is_shown(client):
    """The branch is on authentication, not on user-agent. Serving a crawler
    something a human cannot get is cloaking, and it is a penalty rather than a
    ranking."""

    crawler = client.get("/app", headers={
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    })
    human = client.get("/app", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})
    assert crawler.status_code == human.status_code == 200
    assert crawler.get_data() == human.get_data()


# ---------------------------------------------------------------------------
# The page's other obligations
# ---------------------------------------------------------------------------


def test_the_page_is_submitted_to_the_sitemap(client):
    body = client.get("/sitemap-pages.xml").get_data(as_text=True)
    assert f"<loc>{sv.CANONICAL_ORIGIN}/app</loc>" in body


def test_the_smart_app_banner_opts_this_page_in(landing):
    """iOS shows the banner whether or not the app is installed, so it belongs
    on pages that are about the app rather than on every page."""

    assert "/app" in app_promotion.SMART_BANNER_PATHS
    assert f'name="apple-itunes-app" content="app-id={APP_STORE_ID}' in landing.get_data(as_text=True)


def test_the_screenshots_that_are_referenced_actually_exist(landing):
    """A broken <img> on the one page arguing for the app is worse than no
    screenshots. They are also the largest thing on the page, so the declared
    dimensions matter: without them the images reflow the layout as they land,
    which is a CLS regression measured on real visitors."""

    body = landing.get_data(as_text=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    referenced = re.findall(r'src="(/static/img/app/[^"]+)"', body)
    assert len(referenced) == len(bot.APP_LANDING_SCREENSHOTS)
    for src in referenced:
        assert os.path.exists(os.path.join(root, src.lstrip("/"))), src

    # And the other direction, which matters for a different reason. A screenshot
    # dropped from the list for a copy reason is still served at its own URL and
    # still indexable as an image, so the removal is only real once the file is
    # gone. `pulsesoc-app-video-calls.webp` was dropped precisely because its
    # pixels read "End-to-end encrypted".
    on_disk = set(os.listdir(os.path.join(root, "static/img/app")))
    assert on_disk == {name for name, _alt in bot.APP_LANDING_SCREENSHOTS}
    for tag in re.findall(r"<img [^>]*/static/img/app/[^>]*>", body):
        assert 'width="' in tag and 'height="' in tag, tag
        assert 'alt="' in tag and 'alt=""' not in tag, tag


def test_the_declared_dimensions_match_the_files_on_disk():
    """A width/height pair that is merely *present* still shifts the layout if
    it is wrong -- the browser reserves the wrong box and corrects it on load.
    The first version of this page declared 1039px for images that are 1043."""

    from PIL import Image

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "templates/app_landing.html")) as handle:
        tag = re.search(r'<img [^>]*?width="(\d+)" height="(\d+)"', handle.read())
    declared = (int(tag.group(1)), int(tag.group(2)))
    for filename, _alt in bot.APP_LANDING_SCREENSHOTS:
        with Image.open(os.path.join(root, "static/img/app", filename)) as image:
            assert image.size == declared, f"{filename} is {image.size}, declared {declared}"


def test_every_screenshot_transcribes_the_words_printed_on_it():
    """These are the App Store's marketing compositions, not raw captures, and
    each has a headline rendered into the pixels. That text does not exist for a
    screen reader or for Google, so the alt attribute has to carry it.

    The quote marks are the machine-checkable part. Requiring them is what stops
    the next person from adding a composite image with a paraphrase underneath,
    which reads as a complete description and is not one.
    """

    for filename, alt in bot.APP_LANDING_SCREENSHOTS:
        assert "“" in alt and "”" in alt, f"{filename} does not quote its headline"
        assert alt.index("“") < alt.index("”"), filename
        assert len(alt) > 60, f"{filename} alt is too short to be both quote and description"


def test_the_page_does_not_send_a_visitor_to_a_login_wall_it_called_the_web_app(client, landing):
    """`/pulse` 302s to /login. A CTA reading "use it on the web" that lands on
    a login form is the same species of false claim as a wrong price -- it just
    fails after the click instead of before it."""

    body = landing.get_data(as_text=True)
    for href in set(re.findall(r'href="(/[^"#?]*)"', body)):
        status = client.get(href).status_code
        assert status == 200, f"{href} answers {status} to an anonymous visitor"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
