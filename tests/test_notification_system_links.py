"""SMS gets a tappable link, and every notification destination actually exists.

Two separate defects live here.

The SMS channel appended `sanitize_deep_link(...)`, which returns a *relative
path*. Members were receiving the literal text "/pulse/post/123" -- not a link,
just a string. And the whole message was truncated to 480 characters, so a long
preview silently ate the end of the URL.

Separately, three notification builders pointed at paths that are not routes at
all. A security alert -- the one notification a member must be able to act on
immediately -- opened a 404.

Run: python3 -m pytest tests/test_notification_system_links.py
"""

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402


# ---------------------------------------------------------------------------
# SMS links are tappable
# ---------------------------------------------------------------------------


def test_sms_links_are_absolute_not_bare_paths():
    # The reported shape of the bug: a path in a text message is not a link.
    link = notifications._sms_link("/pulse/post/1234")
    assert link.startswith(f"{app_links.CANONICAL_APP_ORIGIN}/pulse/post/1234?")
    assert not link.startswith("/")


def test_sms_links_are_marked_as_app_intent_with_the_sms_source():
    link = notifications._sms_link("/pulse/post/1234")
    assert f"{app_links.APP_INTENT_PARAM}=1" in link
    assert f"{app_links.APP_SOURCE_PARAM}=sms" in link


@pytest.mark.parametrize(
    "deep_link,expected_key",
    [
        ("/pulse/messages/12", "conversation"),
        ("/pulse/reels/9", "reel"),
        ("/pulse/orders/4", "order"),
    ],
)
def test_sms_links_resolve_to_the_destination_they_name(deep_link, expected_key):
    assert app_links.match_destination(deep_link).key == expected_key
    assert deep_link in notifications._sms_link(deep_link)


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "web_link", ["/account/security", "/privacy", "/terms", "/dashboard/creator"]
)
def test_web_intent_sms_links_stay_on_the_web(web_link):
    link = notifications._sms_link(web_link)
    assert link == f"{app_links.CANONICAL_APP_ORIGIN}{web_link}"
    assert app_links.APP_INTENT_PARAM not in link


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//evil.example.com/pulse",
        "https://evil.example.com/pulse/post/1",
        "/api/internal/secret",
        "/admin/users",
    ],
)
def test_hostile_or_internal_links_fall_back_to_notifications(hostile):
    # sanitize_deep_link rejects these; the promotion must not resurrect them.
    link = notifications._sms_link(hostile)
    assert link.startswith(f"{app_links.CANONICAL_APP_ORIGIN}/pulse/notifications")
    assert "evil.example.com" not in link


# ---------------------------------------------------------------------------
# Truncation must never eat the URL
# ---------------------------------------------------------------------------


def test_a_long_preview_is_trimmed_and_the_link_survives_intact():
    link = notifications._sms_link("/pulse/post/1234")
    body = notifications._sms_body("x" * 900, link)
    assert len(body) <= notifications.SMS_MAX_CHARS
    assert body.endswith(link), "truncation clipped the URL and shipped a dead link"


def test_a_short_preview_is_left_alone():
    link = notifications._sms_link("/pulse/post/1234")
    body = notifications._sms_body("Ada replied to your post", link)
    assert body == f"PulseSoc: Ada replied to your post {link}"


def test_an_absurdly_long_link_does_not_crash_the_body():
    # Budget goes negative rather than raising a slice error.
    body = notifications._sms_body("preview", "https://pulsesoc.com/" + "a" * 900)
    assert body.startswith("PulseSoc:  https://")


# ---------------------------------------------------------------------------
# Every notification destination resolves
# ---------------------------------------------------------------------------


def declared_deep_links():
    """Every `deep_link=` destination the module can actually emit.

    Read out of the production source rather than listed here by hand. A hand
    written list only ever re-checks the destinations someone remembered to add
    to it, so the next `/pulse/dashboard/creator` would sail past it; this sees
    whatever the module really says.

    f-strings are reduced to a concrete example -- `/pulse/post/{id}` becomes
    `/pulse/post/1` -- because what is being checked is the route family, and a
    resolver cannot classify a template hole.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(notifications))
    found = set()

    def record(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.add(
                "".join(
                    part.value if isinstance(part, ast.Constant) else "1"
                    for part in node.values
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "deep_link":
            record(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = args.args[len(args.args) - len(args.defaults) :]
            pairs = list(zip(positional, args.defaults))
            pairs += list(zip(args.kwonlyargs, args.kw_defaults))
            for arg, default in pairs:
                if arg.arg == "deep_link" and default is not None:
                    record(default)

    # "" is "no link", not a broken one.
    return sorted(link for link in found if link)


def test_the_extraction_actually_finds_the_destinations():
    # Guards the test above: if the AST walk silently stopped matching, an empty
    # set would make `all destinations resolve` vacuously true.
    links = declared_deep_links()
    assert len(links) >= 10, links
    assert "/account/security" in links
    assert "/pulse/post/1" in links


@pytest.mark.parametrize("deep_link", declared_deep_links())
def test_every_notification_destination_either_resolves_or_is_web_intent(deep_link):
    # A destination that is neither is a link that lands nowhere. /dashboard/security
    # and /pulse/dashboard/creator used to fail exactly this check.
    resolves = app_links.match_destination(deep_link) is not None
    web_intent = app_links.is_web_intent_path(deep_link)
    assert resolves or web_intent, f"{deep_link} lands nowhere"


def test_no_notification_points_at_the_routes_that_never_existed():
    links = declared_deep_links()
    assert "/dashboard/security" not in links
    assert "/pulse/dashboard/creator" not in links


def test_the_security_alert_points_at_a_page_that_exists():
    # The one notification a member must be able to act on immediately.
    assert "/account/security" in declared_deep_links()


def test_the_creator_notification_points_at_a_page_that_exists():
    assert "/dashboard/creator" in declared_deep_links()


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_link_authority_is_what_makes_sms_links_absolute(monkeypatch):
    assert notifications._sms_link("/pulse/post/1").startswith("https://")

    monkeypatch.setattr(
        notifications.app_links, "app_intent_url", lambda link, source: link
    )
    # Still absolute -- the CANONICAL_APP_ORIGIN prefix is the second half of
    # the fix -- but the marker must be gone, proving the marker comes from the
    # authority and not from a hardcoded string.
    promoted = notifications._sms_link("/pulse/post/1")
    assert promoted == f"{app_links.CANONICAL_APP_ORIGIN}/pulse/post/1"
    assert app_links.APP_INTENT_PARAM not in promoted


def test_mutation_the_live_studio_classification_is_load_bearing(monkeypatch):
    assert app_links.is_web_intent_path("/pulse/live/studio")

    monkeypatch.setattr(app_links, "WEB_INTENT_PREFIXES", ())
    monkeypatch.setattr(app_links, "WEB_INTENT_PATHS", frozenset())
    # With the classification gone the studio is no longer protected, which is
    # what makes the assertion above meaningful.
    assert not app_links.is_web_intent_path("/pulse/live/studio")


def test_the_live_viewing_destination_is_still_app_linkable():
    # Guard against over-broad classification: /pulse/live/studio must not
    # swallow /pulse/live/<id>, which the app genuinely opens.
    assert app_links.match_destination("/pulse/live/5").key == "live"
    assert app_links.APP_INTENT_PARAM in app_links.app_intent_url("/pulse/live/5", "push")
