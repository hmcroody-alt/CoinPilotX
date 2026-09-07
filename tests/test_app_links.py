"""Tests for the canonical app-intent link authority.

Two halves. The first asserts the contract. The second is the anti-vacuity half:
each mutation test breaks exactly one guard in `services.app_links` and proves
the behaviour changes -- so a future edit that deletes the guard cannot leave a
green suite behind. A test that would pass with the guard removed proves nothing.
"""

import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links
from services.app_links import (
    APP_INTENT_PARAM,
    APP_SOURCE_PARAM,
    CANONICAL_APP_ORIGIN,
    DESTINATIONS,
    FALLBACK_APP_STORE,
    FALLBACK_IGNORE,
    FALLBACK_WEB,
    AppLinkError,
    app_intent_url,
    app_link_source,
    build_app_link,
    describe_destinations,
    destination_label,
    fallback_decision,
    is_app_intent_query,
    is_web_intent_path,
    match_destination,
    normalize_source,
    resolve_destination_path,
)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_every_link_is_absolute_on_the_one_host_the_app_claims():
    # The entitlement claims applinks:pulsesoc.com and nothing else. A link on
    # any other host cannot open the app however correct its path is.
    for destination, resource in (("home", None), ("post", 42), ("profile", "ada")):
        link = build_app_link(destination, resource)
        assert link.startswith(f"{CANONICAL_APP_ORIGIN}/")


def test_build_app_link_marks_intent_and_source():
    link = build_app_link("post", 1234, source="email")
    assert link == f"{CANONICAL_APP_ORIGIN}/pulse/post/1234?{APP_INTENT_PARAM}=1&{APP_SOURCE_PARAM}=email"


def test_unknown_destination_raises_rather_than_guessing():
    with pytest.raises(AppLinkError):
        build_app_link("teleporter")


def test_required_resource_id_cannot_be_omitted():
    # Silently dropping the id would produce a link labelled "open this post"
    # that lands on Home -- the dishonest-CTA failure this vocabulary exists to
    # prevent.
    with pytest.raises(AppLinkError):
        build_app_link("post")


def test_resource_id_grammar_matches_the_shipped_binary():
    # nativeObjectDestination() matches [1-9]\d* and maps anything else to 0,
    # which every detail screen reads as "no resource".
    assert build_app_link("post", 7).endswith("/pulse/post/7?" + f"{APP_INTENT_PARAM}=1&{APP_SOURCE_PARAM}=system")
    for rejected in ("0", "007", "-3", "abc", "1 2", "9" * 20):
        with pytest.raises(AppLinkError):
            build_app_link("post", rejected)


def test_destination_without_resource_rejects_one():
    with pytest.raises(AppLinkError):
        build_app_link("home", 5)


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "javascript:alert(1)", "a\nb", "a\\b", "a\x00b"],
)
def test_hostile_resource_ids_are_refused(hostile):
    with pytest.raises(AppLinkError):
        build_app_link("profile", hostile)


def test_caller_supplied_params_cannot_spoof_or_clear_the_markers():
    link = build_app_link(
        "home", params={APP_INTENT_PARAM: "0", APP_SOURCE_PARAM: "spoofed", "ref": "x"}
    )
    assert f"{APP_INTENT_PARAM}=1" in link
    assert "spoofed" not in link
    assert "ref=x" in link


def test_unknown_source_falls_back_to_system_rather_than_echoing_input():
    assert normalize_source("<script>") == "system"
    assert normalize_source("email") == "email"
    assert "<script>" not in build_app_link("home", source="<script>")


# ---------------------------------------------------------------------------
# Adapter -- the email/push migration seam
# ---------------------------------------------------------------------------


def test_relative_pulse_paths_become_marked_canonical_links():
    assert app_intent_url("/pulse/post/1234", "email") == (
        f"{CANONICAL_APP_ORIGIN}/pulse/post/1234?{APP_INTENT_PARAM}=1&{APP_SOURCE_PARAM}=email"
    )


def test_existing_query_is_preserved_and_markers_are_not_duplicated():
    once = app_intent_url("/pulse/reels/9?autoplay=1", "share")
    twice = app_intent_url(once, "share")
    assert twice == once
    assert once.count(APP_INTENT_PARAM) == 1
    assert "autoplay=1" in once


@pytest.mark.parametrize(
    "web_link",
    [
        "/privacy",
        "/terms",
        "/legal/dpa",
        "/support",
        "/help/getting-started",
        "/account/settings",
        "/checkout/confirm",
        "/reset-password",
        "/verify-email",
        "/login",
        "/",
    ],
)
def test_website_navigation_is_never_converted_into_an_app_launch(web_link):
    # Section 2: legitimate web destinations keep working as web destinations.
    assert app_intent_url(web_link, "email") == web_link


@pytest.mark.parametrize(
    "foreign",
    [
        "https://evil.example.com/pulse/post/1",
        "//evil.example.com/pulse",
        "http://pulsesoc.com/pulse",
        "javascript:alert(1)",
        "mailto:support@pulsesoc.com",
    ],
)
def test_off_host_and_hostile_links_pass_through_untouched(foreign):
    assert app_intent_url(foreign, "email") == foreign


@pytest.mark.parametrize(
    "reserved",
    [
        "/pulse/profile/security",
        "/pulse/profile/edit",
        "/pulse/groups/create",
        "/pulse/merchant/apply",
        "/pulse/merchant/dashboard",
        "/pulse/marketplace/create",
    ],
)
def test_reserved_sub_pages_are_not_mistaken_for_resources(reserved):
    # `/pulse/profile/security` is the account security page. Treating "security"
    # as a member handle would open unrelated content behind an honest-looking
    # button.
    assert app_intent_url(reserved, "email") == reserved


def test_paths_the_released_binary_cannot_resolve_are_left_alone():
    # Section 15: never emit a marked link the installed app would land nowhere
    # on. `/pulse/briefings` has no entry in the vocabulary.
    assert app_intent_url("/pulse/does-not-exist", "email") == "/pulse/does-not-exist"


def test_empty_input_is_returned_unchanged():
    assert app_intent_url("", "email") == ""
    assert app_intent_url(None, "email") is None or app_intent_url(None, "email") == ""


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_longest_match_wins_so_a_reel_is_not_the_reels_tab():
    assert match_destination("/pulse/reels").key == "reels"
    assert match_destination("/pulse/reels/12").key == "reel"
    assert match_destination("/pulse/messages").key == "messages"
    assert match_destination("/pulse/messages/12").key == "conversation"


def test_trailing_slash_and_duplicate_separators_normalize():
    assert match_destination("/pulse/post/5/").key == "post"
    assert match_destination("//pulse//post//5").key == "post"


def test_unmapped_path_matches_nothing():
    assert match_destination("/pulse/unknown/thing") is None


# ---------------------------------------------------------------------------
# Fallback policy
# ---------------------------------------------------------------------------


def test_ios_app_intent_always_goes_to_the_app_store_never_the_website():
    # The core contract. Reaching the server at all means the app did not claim
    # the link, which means it is not installed.
    for path in ("/pulse", "/pulse/post/5", "/pulse/marketplace/9", "/pulse/orders"):
        action, _ = fallback_decision(path, is_ios=True, is_app_intent=True)
        assert action == FALLBACK_APP_STORE, path


def test_unmarked_requests_are_ignored_entirely():
    # An ordinary visitor browsing pulsesoc.com is never intercepted.
    assert fallback_decision("/pulse/post/5", is_ios=True, is_app_intent=False)[0] == FALLBACK_IGNORE


def test_web_intent_paths_are_ignored_even_if_someone_appends_the_marker():
    assert fallback_decision("/privacy", is_ios=True, is_app_intent=True)[0] == FALLBACK_IGNORE


def test_non_ios_continues_to_the_web_only_where_a_web_page_genuinely_exists():
    assert fallback_decision("/pulse/post/5", is_ios=False, is_app_intent=True)[0] == FALLBACK_WEB
    # No web route exists for a single listing or for orders, so the listing is
    # the only honest destination left.
    assert fallback_decision("/pulse/marketplace/9", is_ios=False, is_app_intent=True)[0] == FALLBACK_APP_STORE
    assert fallback_decision("/pulse/orders", is_ios=False, is_app_intent=True)[0] == FALLBACK_APP_STORE


def test_unknown_destination_fails_safe_to_our_own_web_surface():
    # Section 18: no 500, and no redirect target derived from request input.
    action, detail = fallback_decision("/pulse/nonsense", is_ios=True, is_app_intent=True)
    assert (action, detail) == (FALLBACK_WEB, "unknown_destination")


# ---------------------------------------------------------------------------
# Vocabulary integrity
# ---------------------------------------------------------------------------


def test_every_destination_path_is_claimed_by_the_published_aasa():
    # services/native_app_links.py publishes exactly /pulse/* and /search*.
    # A destination outside those components would never be handed to the app.
    for spec in DESTINATIONS.values():
        assert spec.path_template.startswith("/pulse/") or spec.path_template in (
            "/pulse",
            "/search",
        ), spec.key


def test_unsupported_destinations_do_not_advertise_a_native_promise():
    for spec in DESTINATIONS.values():
        if not spec.native_supported:
            assert spec.notes, f"{spec.key} must document why it is unsupported"
            assert destination_label(spec.key) == "Open in PulseSoc"


def test_message_granularity_is_documented_as_unsupported():
    # The released binary's deepest messaging route is the conversation.
    assert DESTINATIONS["message"].native_supported is False
    assert app_links.DEGRADE_TO["message"] == "conversation"


def test_describe_destinations_covers_the_whole_registry():
    described = describe_destinations()
    assert {row["key"] for row in described} == set(DESTINATIONS)


def test_query_helpers_read_the_marker():
    assert is_app_intent_query({APP_INTENT_PARAM: "1"}) is True
    assert is_app_intent_query({APP_INTENT_PARAM: "0"}) is False
    assert is_app_intent_query({}) is False
    assert app_link_source({APP_SOURCE_PARAM: "push"}) == "push"
    assert app_link_source({APP_SOURCE_PARAM: "nonsense"}) == "system"


# ---------------------------------------------------------------------------
# Anti-vacuity: each guard is proven load-bearing by breaking it
# ---------------------------------------------------------------------------


def test_mutation_web_intent_allowlist_is_load_bearing(monkeypatch):
    # `/privacy` is not in the destination vocabulary, so its passing through
    # untouched proves nothing on its own. `/pulse` IS a destination, so putting
    # it under web-intent protection and then removing that protection isolates
    # the allowlist as the single cause of the difference.
    monkeypatch.setattr(app_links, "WEB_INTENT_PATHS", frozenset({"/pulse"}))
    assert app_intent_url("/pulse", "email") == "/pulse"
    monkeypatch.setattr(app_links, "WEB_INTENT_PATHS", frozenset())
    assert app_intent_url("/pulse", "email").startswith(CANONICAL_APP_ORIGIN)


def test_mutation_host_allowlist_is_load_bearing(monkeypatch):
    foreign = "https://evil.example.com/pulse/post/1"
    assert app_intent_url(foreign, "email") == foreign
    monkeypatch.setattr(
        app_links, "ALLOWED_APP_LINK_HOSTS", frozenset({"evil.example.com"})
    )
    mutated = app_intent_url(foreign, "email")
    assert mutated != foreign, "host allowlist is not actually consulted"
    assert mutated.startswith(CANONICAL_APP_ORIGIN)


def test_mutation_reserved_ids_are_load_bearing(monkeypatch):
    from dataclasses import replace

    assert app_intent_url("/pulse/profile/security", "email") == "/pulse/profile/security"
    unguarded = replace(DESTINATIONS["profile"], reserved_ids=frozenset())
    assert unguarded.accepts_id("security") is True, (
        "reserved_ids is not what blocks the reserved sub-page"
    )


def test_mutation_id_validation_is_load_bearing(monkeypatch):
    with pytest.raises(AppLinkError):
        build_app_link("post", "0")
    monkeypatch.setattr(
        app_links, "POSITIVE_INT_RE", re.compile(r"^[0-9]+$")
    )
    monkeypatch.setattr(
        app_links,
        "_ID_VALIDATORS",
        {
            app_links.ID_KIND_NONE: lambda _v: False,
            app_links.ID_KIND_POSITIVE_INT: lambda v: bool(re.match(r"^[0-9]+$", v)),
            app_links.ID_KIND_SLUG: lambda v: bool(app_links.SLUG_RE.match(v)),
        },
    )
    # With the grammar loosened, "0" is accepted -- proving the strict pattern is
    # the thing rejecting it, not some incidental failure elsewhere.
    assert build_app_link("post", "0").endswith(
        f"/pulse/post/0?{APP_INTENT_PARAM}=1&{APP_SOURCE_PARAM}=system"
    )


def test_mutation_marker_is_what_triggers_the_fallback(monkeypatch):
    assert fallback_decision("/pulse/post/5", is_ios=True, is_app_intent=True)[0] == FALLBACK_APP_STORE
    assert fallback_decision("/pulse/post/5", is_ios=True, is_app_intent=False)[0] == FALLBACK_IGNORE


def test_mutation_web_equivalence_flag_is_load_bearing(monkeypatch):
    from dataclasses import replace

    assert fallback_decision("/pulse/post/5", is_ios=False, is_app_intent=True)[0] == FALLBACK_WEB
    patched = dict(DESTINATIONS)
    patched["post"] = replace(DESTINATIONS["post"], web_equivalent=False)
    monkeypatch.setattr(app_links, "DESTINATIONS", patched)
    monkeypatch.setattr(
        app_links,
        "_MATCHERS",
        tuple(
            (pattern, patched.get(spec.key, spec))
            for pattern, spec in app_links._MATCHERS
        ),
    )
    assert fallback_decision("/pulse/post/5", is_ios=False, is_app_intent=True)[0] == FALLBACK_APP_STORE
