"""The surfaces have to be *in the pages*, not merely buildable.

`tests/test_app_promotion.py` proves the policy module returns the right markup.
This file proves `bot.py` actually renders it, which is a separate failure mode:
every helper here can be perfect while a shell quietly drops the placeholder, or
a template loses the banner variable, and nothing else in the suite notices.

Run: python3 -m pytest tests/test_app_promotion_shell_wiring.py
"""

import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
from services import app_promotion


UA_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)

CSS = "pulse_app_promotion.css"
JS = "pulse_app_promotion.js"
CONFIG = "PULSE_APP_PROMOTION"
HEADER_CONTROL = 'pulse-topnav-app-promo"'
CARD = 'data-app-promo="desktop_card"'
NOTE = 'data-app-promo="marketplace_note"'
PILL = 'class="pulse-app-pill"'
TRIGGER = "data-app-promo-marketplace"
BANNER = 'name="apple-itunes-app"'


class _Account(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


ACCOUNT = _Account(
    id=1,
    account_id=1,
    username="promoprobe",
    email="promoprobe@example.com",
    display_name="Promo Probe",
    is_admin=False,
    plan="free",
    avatar_url="",
)


@pytest.fixture
def client():
    with bot.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def signed_in():
    with mock.patch.object(bot, "require_account", lambda *a, **k: ACCOUNT), \
            mock.patch.object(bot, "load_account_by_id", lambda *a, **k: ACCOUNT):
        yield


def get(client, path, ua=UA_MAC):
    response = client.get(path, headers={"User-Agent": ua})
    return response, response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Smart App Banner -- the approved scope, through the real routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/search?q=wallet"])
def test_the_public_pages_that_opted_in_carry_exactly_one_banner(client, path):
    response, html = get(client, path)
    assert response.status_code == 200
    assert html.count(BANNER) == 1
    assert f"app-id={app_promotion.app_store_app_id()}" in html


def test_a_public_page_that_did_not_opt_in_carries_none(client):
    response, html = get(client, "/about")
    assert response.status_code == 200
    assert html.count(BANNER) == 0


# ---------------------------------------------------------------------------
# The shells render every surface, and leave no placeholder behind
# ---------------------------------------------------------------------------


def test_the_feed_shell_renders_the_whole_system(client, signed_in):
    response, html = get(client, "/pulse")
    assert response.status_code == 200
    for needle in (CSS, JS, CONFIG, HEADER_CONTROL, CARD, NOTE, PILL, TRIGGER):
        assert html.count(needle) >= 1, needle
    # Both placeholders were substituted. An unreplaced token renders as
    # literal text in the page and is invisible in a 117KB diff.
    assert "__APP_PROMOTION" not in html


def test_the_social_shell_renders_the_whole_system(client, signed_in):
    response, html = get(client, "/pulse/settings")
    assert response.status_code == 200
    for needle in (CSS, JS, CONFIG, HEADER_CONTROL, CARD, NOTE, PILL, TRIGGER):
        assert html.count(needle) >= 1, needle
    assert "__APP_PROMOTION" not in html


def test_the_marketplace_note_ships_hidden_in_a_real_page(client, signed_in):
    _, html = get(client, "/pulse/settings")
    section = html[html.index(NOTE) : html.index(NOTE) + 400]
    assert " hidden>" in section


def test_a_shell_never_shows_two_uninvited_surfaces_at_once(client, signed_in):
    # Both are in the markup; only one may be *visible*. The note ships hidden,
    # so at first paint the card is alone.
    _, html = get(client, "/pulse/settings")
    assert html.count(CARD) == 1
    assert html.count(NOTE) == 1


# ---------------------------------------------------------------------------
# Destination-aware actions
# ---------------------------------------------------------------------------


def test_an_iphone_gets_the_open_in_app_action(client, signed_in):
    _, html = get(client, "/pulse/settings", ua=UA_IPHONE)
    assert html.count("pulsesoc://") == 1


def test_a_desktop_does_not_get_a_dead_scheme_button(client, signed_in):
    # `pulsesoc://` on macOS is a control that either does nothing or raises an
    # OS sheet. It must not be rendered where it cannot work.
    _, html = get(client, "/pulse/settings", ua=UA_MAC)
    assert "pulsesoc://" not in html


# ---------------------------------------------------------------------------
# Regression boundaries the brief drew
# ---------------------------------------------------------------------------


def render_shell(main_html):
    """`pulse_social_shell` directly -- /pulse/live needs tables a test DB lacks."""

    with bot.app.test_request_context("/pulse/live", headers={"User-Agent": UA_MAC}):
        with mock.patch.object(bot, "require_account", lambda *a, **k: ACCOUNT), \
                mock.patch.object(bot, "load_account_by_id", lambda *a, **k: ACCOUNT):
            rendered = bot.pulse_social_shell(
                title="Live",
                description="Live",
                main_html=main_html,
            )
    if hasattr(rendered, "get_data"):
        assert rendered.status_code == 200, rendered.status_code
        return rendered.get_data(as_text=True)
    return rendered


def test_a_live_shell_gets_no_uninvited_card_beside_the_broadcast():
    page = render_shell("<div data-pulse-live-shell><video></video></div>")
    assert page.count(CARD) == 0
    # ...and the way to the app is still discoverable from the header.
    assert page.count(HEADER_CONTROL) == 1


def test_the_same_shell_without_a_broadcast_does_get_the_card():
    # The positive control. Without it the test above passes on any shell that
    # simply stopped rendering the card at all.
    page = render_shell("<div><p>An ordinary page.</p></div>")
    assert page.count(CARD) == 1


def test_the_app_first_marker_lands_on_nav_links_that_hand_off(client, signed_in):
    _, html = get(client, "/pulse/settings")
    # Every pill sits on a link whose href is an /open/ interstitial.
    assert html.count(PILL) == html.count(TRIGGER)
    assert html.count(PILL) >= 1
    assert "/open/" in html


def test_the_promotion_assets_are_served(client):
    for asset in (f"/static/css/{CSS}", f"/static/js/{JS}"):
        response = client.get(asset)
        assert response.status_code == 200, asset
        assert response.get_data()


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_banner_really_comes_from_the_injected_helper(client, monkeypatch):
    monkeypatch.setattr(
        app_promotion, "smart_app_banner_meta", lambda path: "<!--PROMO-SENTINEL-->"
    )
    _, html = get(client, "/")
    assert "<!--PROMO-SENTINEL-->" in html
    assert BANNER not in html


def test_mutation_the_card_really_comes_from_the_policy_module(client, signed_in, monkeypatch):
    monkeypatch.setattr(app_promotion, "promotion_card_html", lambda **kw: "")
    _, html = get(client, "/pulse/settings")
    assert html.count(CARD) == 0
    # The header control's <details> wrapper survives -- it is built in this
    # module -- but the pitch inside it comes from the same builder, so the
    # copy has to vanish from the page entirely.
    assert app_promotion.CARD_TITLE not in html
