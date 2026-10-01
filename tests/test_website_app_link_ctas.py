"""Website app-link CTAs render, are truthful, and leave web navigation alone.

The shared `_app_link_cta.html` component is the only sanctioned way for a
template to emit an "open this in the app" button. These tests pin three things
that are easy to break silently:

  1. The CTA actually renders. The component depends on the `inject_app_link_helpers`
     context processor, and a Jinja `{% from %}` import is context-LESS by default --
     so a correct-looking `{% from "_app_link_cta.html" import app_cta %}` without
     `with context` raises UndefinedError and 500s the whole page. That is exactly
     how this first landed.
  2. The href is the canonical builder's output, marker and all. A hand-written
     app link in a template is the failure mode the component exists to prevent.
  3. Ordinary web navigation is preserved. The rule is add, not convert: a signed-in
     member clicking "Explore PulseSoc" wants the web feed.

Run: python3 -m pytest tests/test_website_app_link_ctas.py
"""

import collections
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing bot builds the whole Flask app; without this it also tries to create
# ~550 tables. The templates under test need neither.
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"
os.environ.setdefault(
    "DATABASE_URL", "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
)

import bot  # noqa: E402
from services import app_links  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS, which
# would turn every assertion below into a redirect body.
HTTPS = {"X-Forwarded-Proto": "https"}

CTA_RE = re.compile(r"<a[^>]*data-app-link=\"([^\"]+)\"[^>]*>([^<]*)</a>", re.S)
HREF_RE = re.compile(r"href=\"([^\"]*)\"")


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def homepage(client):
    response = client.get("/", headers=HTTPS)
    assert response.status_code == 200, (
        f"homepage did not render (HTTP {response.status_code}); an app-link CTA "
        "raising inside Jinja takes the whole page down"
    )
    return response.get_data(as_text=True)


def ctas(html):
    """{destination: (href, label)} for every component-rendered app link."""
    found = {}
    for match in CTA_RE.finditer(html):
        href = HREF_RE.search(match.group(0))
        found[match.group(1)] = (href.group(1) if href else "", match.group(2).strip())
    return found


# ---------------------------------------------------------------------------
# The CTA renders and points where it says
# ---------------------------------------------------------------------------


def test_homepage_offers_an_app_link_and_a_store_badge(homepage):
    found = ctas(homepage)
    assert "home" in found, "homepage lost its app-opening CTA"
    assert "app-store" in found, "homepage lost its App Store badge"


def test_the_app_cta_href_is_the_canonical_builder_output(homepage):
    href, _ = ctas(homepage)["home"]
    # Jinja autoescapes the query separator; compare on the decoded form.
    assert href.replace("&amp;", "&") == app_links.build_app_link("home", source="web")
    assert href.startswith(f"{app_links.CANONICAL_APP_ORIGIN}/pulse?")
    assert f"{app_links.APP_INTENT_PARAM}=1" in href
    assert f"{app_links.APP_SOURCE_PARAM}=web" in href


def test_the_store_badge_uses_the_configured_listing(homepage):
    href, _ = ctas(homepage)["app-store"]
    assert href == bot.pulsesoc_app_store_url()
    assert href.startswith("https://apps.apple.com/")


def test_the_label_says_the_link_leaves_the_website(homepage):
    _, label = ctas(homepage)["home"]
    # Meaningful out of context: "Open" alone tells a screen reader user nothing.
    assert "PulseSoc app" in label
    assert label != "Open"

    _, badge = ctas(homepage)["app-store"]
    assert badge == "Download on the App Store"


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_ordinary_web_navigation_is_preserved(homepage):
    # Add, do not convert. These are legitimate website links for someone who is
    # reading the site right now; hijacking them into app launches is the
    # behaviour the placement rules forbid.
    web_nav = re.findall(r'<a[^>]*href="/pulse"[^>]*>', homepage)
    assert web_nav, "existing web navigation to /pulse was converted away"
    for anchor in web_nav:
        assert app_links.APP_INTENT_PARAM not in anchor


def test_no_app_intent_marker_leaks_into_a_relative_link(homepage):
    # The marker is only ever valid on a canonical absolute link. A relative
    # "/pulse?pulse_app=1" would mark an in-site click as an app intent and
    # bounce a plain web reader to the App Store.
    assert f'href="/pulse?{app_links.APP_INTENT_PARAM}' not in homepage


def test_the_cta_does_not_emit_ids(homepage):
    # The macro is reusable, so it must stay safe to call more than once a page.
    for match in CTA_RE.finditer(homepage):
        assert " id=" not in match.group(0)


def test_the_page_has_no_duplicate_element_ids(homepage):
    counts = collections.Counter(re.findall(r'\sid="([^"]+)"', homepage))
    assert [key for key, count in counts.items() if count > 1] == []


def test_the_cta_is_a_real_anchor_not_a_scripted_div(homepage):
    # Keyboard reachability and the site focus ring come free from <a href>.
    for match in CTA_RE.finditer(homepage):
        assert match.group(0).startswith("<a ")
        assert "href=" in match.group(0)


def test_the_cta_is_not_absolutely_positioned_or_hover_revealed(homepage):
    # No layout shift on load, no hover-only access.
    for match in CTA_RE.finditer(homepage):
        markup = match.group(0)
        assert "position:absolute" not in markup.replace(" ", "")
        assert "position:fixed" not in markup.replace(" ", "")
        assert ":hover" not in markup


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def cta_module():
    """The component, bound to the same context a real page render would give it.

    `update_template_context` mutates in place and returns None, so the dict has
    to be built first -- passing its return value hands the macro an empty
    namespace and every helper comes back Undefined.
    """
    context = {}
    bot.webhook_app.update_template_context(context)
    return bot.webhook_app.jinja_env.get_template("_app_link_cta.html").make_module(
        vars=context
    )


def test_mutation_the_component_is_what_produces_the_marker(client, monkeypatch):
    # If the CTA's href were hand-written in the template instead of coming from
    # the shared builder, neutering the builder would change nothing and every
    # assertion above would be measuring a hardcoded string.
    before, _ = ctas(client.get("/", headers=HTTPS).get_data(as_text=True))["home"]
    assert app_links.APP_INTENT_PARAM in before

    monkeypatch.setattr(
        bot.app_links,
        "build_app_link",
        lambda destination, resource_id=None, params=None, source=None: "/sentinel",
    )
    after = ctas(client.get("/", headers=HTTPS).get_data(as_text=True))["home"]
    assert after[0] == "/sentinel"


def test_mutation_the_label_comes_from_the_destination_registry(client, monkeypatch):
    monkeypatch.setattr(
        bot.app_links, "destination_label", lambda destination: "Renamed CTA"
    )
    # index.html passes an explicit label, so the registry default must show up
    # on a call that omits one -- render the macro directly against the same
    # context the page uses.
    with bot.webhook_app.test_request_context("/", headers=HTTPS):
        assert "Renamed CTA" in str(cta_module().app_cta("home"))


def test_mutation_a_bad_destination_fails_loudly_rather_than_linking_home(client):
    # Silently degrading an unknown destination to Home is precisely how a button
    # reading "Open this product" ends up opening the feed.
    with bot.webhook_app.test_request_context("/", headers=HTTPS):
        module = cta_module()
        with pytest.raises(app_links.AppLinkError):
            str(module.app_cta("not_a_real_destination"))
        with pytest.raises(app_links.AppLinkError):
            str(module.app_cta("post", "../../etc/passwd"))
