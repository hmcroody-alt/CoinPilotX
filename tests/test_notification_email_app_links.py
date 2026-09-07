"""The notification email CTA must open the app, not the website.

`_branded_html` is the shared body for every notification email, so this one
function is why the "Post published" email landed members in Safari. These tests
pin the fix and, just as importantly, pin the cases that must NOT change: a
password-reset or legal link in a notification email still has to open on the
web, because there is nothing in the app to open.

Run: python3 -m pytest tests/test_notification_email_app_links.py
"""

import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links
from services.notification_service import _branded_html


LINK_RE = re.compile(r"<a href='([^']*)'[^>]*>([^<]*)</a>")


def cta(deep_link):
    """(href, label) of the call to action in a rendered notification email."""
    found = LINK_RE.search(_branded_html("Headline", "Body", deep_link))
    assert found, "notification email lost its call to action"
    return found.group(1), found.group(2)


# ---------------------------------------------------------------------------
# The reported bug
# ---------------------------------------------------------------------------


def test_post_published_email_now_opens_the_app():
    # bot.py notifies "Post published" with deep_link /pulse/post/<id>.
    href, label = cta("/pulse/post/1234")
    assert href.startswith("https://pulsesoc.com/pulse/post/1234?")
    assert f"{app_links.APP_INTENT_PARAM}=1" in href
    assert f"{app_links.APP_SOURCE_PARAM}=email" in href
    assert label == "Open this post in PulseSoc"


@pytest.mark.parametrize(
    "deep_link,expected_label",
    [
        ("/pulse", "Open PulseSoc"),
        ("/pulse/reels/9", "Open this reel in PulseSoc"),
        ("/pulse/status/3", "Open this status in PulseSoc"),
        ("/pulse/messages/12", "Open this conversation in PulseSoc"),
        ("/pulse/notifications", "Open notifications in PulseSoc"),
        ("/pulse/marketplace/7", "Open this listing in PulseSoc"),
        ("/pulse/orders/4", "Open this order in PulseSoc"),
        ("/pulse/groups/founders", "Open this group in PulseSoc"),
        ("/pulse/profile/ada", "Open this profile in PulseSoc"),
    ],
)
def test_the_button_says_what_it_actually_opens(deep_link, expected_label):
    # A button reading "Open this product" that lands on Home is the failure
    # this wording rule exists to prevent.
    href, label = cta(deep_link)
    assert label == expected_label
    assert f"{app_links.APP_INTENT_PARAM}=1" in href


def test_absolute_pulsesoc_links_are_upgraded_too():
    href, label = cta("https://pulsesoc.com/pulse/reels/3")
    assert f"{app_links.APP_INTENT_PARAM}=1" in href
    assert label == "Open this reel in PulseSoc"


def test_missing_deep_link_falls_back_to_notifications():
    href, _ = cta(None)
    assert href.startswith("https://pulsesoc.com/pulse/notifications?")


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "web_link",
    ["/privacy", "/terms", "/support", "/account/settings", "/reset-password?token=abc"],
)
def test_web_intent_notifications_still_open_on_the_web(web_link):
    href, label = cta(web_link)
    assert app_links.APP_INTENT_PARAM not in href
    assert href == f"https://pulsesoc.com{web_link}"
    assert label == "Open PulseSoc"


def test_a_reset_password_token_is_not_leaked_into_an_app_link():
    # Marking this as app-intent would send an iOS member with no app installed
    # to the App Store, stranding the reset. It must stay on the web.
    href, _ = cta("/reset-password?token=secret-value")
    assert "token=secret-value" in href
    assert app_links.APP_INTENT_PARAM not in href


def test_off_host_links_are_left_exactly_as_given():
    href, _ = cta("https://status.example.com/incident/1")
    assert href == "https://status.example.com/incident/1"


def test_paths_the_released_binary_cannot_open_are_not_marked():
    href, label = cta("/pulse/briefings/4")
    assert app_links.APP_INTENT_PARAM not in href
    assert label == "Open PulseSoc"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_a_quote_in_the_deep_link_cannot_break_out_of_the_href():
    html = _branded_html("H", "B", "/pulse/post/1' onmouseover='alert(1)")
    assert "onmouseover='alert(1)'" not in html
    assert "&#x27;" in html


@pytest.mark.parametrize(
    "hostile",
    ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "//evil.example.com/pulse"],
)
def test_hostile_schemes_are_never_turned_into_app_links(hostile):
    href, _ = cta(hostile)
    assert app_links.APP_INTENT_PARAM not in href


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_builder_is_what_produces_the_marker(monkeypatch):
    # If _branded_html stopped calling app_intent_url, the marker would vanish.
    # Neutering the helper must break the assertion above -- otherwise the
    # marker is coming from somewhere else and these tests measure nothing.
    href, label = cta("/pulse/post/1234")
    assert f"{app_links.APP_INTENT_PARAM}=1" in href

    import services.notification_service as notification_service

    monkeypatch.setattr(
        notification_service.app_links, "app_intent_url", lambda link, source: link
    )
    href_after, _ = cta("/pulse/post/1234")
    assert app_links.APP_INTENT_PARAM not in href_after


def test_mutation_the_label_follows_the_registry(monkeypatch):
    from dataclasses import replace

    import services.notification_service as notification_service

    assert cta("/pulse/post/1234")[1] == "Open this post in PulseSoc"
    patched = dict(app_links.DESTINATIONS)
    patched["post"] = replace(app_links.DESTINATIONS["post"], label="Renamed CTA")
    monkeypatch.setattr(
        notification_service.app_links,
        "match_destination",
        lambda path: patched["post"] if "/pulse/post/" in path else None,
    )
    assert cta("/pulse/post/1234")[1] == "Renamed CTA"
