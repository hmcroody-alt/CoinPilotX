"""The rule that decides whether a hub-only deep link is a real gap.

`reconcile_urlmap.py` classifies a parameterised deep link by probing it. When
the probe uses an *invented* parameter, a 404 means nothing: the web often
enumerates its valid values, so `/pulse/settings/x` fails while
`/pulse/settings/security` is served. That produced a bucket where "we have no
detail route" and "we tested a value that never existed" looked identical, and
the document resolved it with the sentence "check before building" — prose,
which nobody re-checks when the routing table moves.

The fix probes values the app can really produce, scraped from the literal
paths its own routing sources match on. This file tests the one rule that makes
that safe to trust.

A literal under the same base is NOT automatically a value for the parameter.
`/pulse/marketplace/create` is a literal in `nativeRouteActions.ts` and it
resolves on the web — but it is `MarketplaceCreateGateway`, its own registered
deep link, not a listing id. Counting it would mark `/pulse/marketplace/
:listingId` as "every real value resolves" while sharing an actual listing goes
on 404ing. A false clearance is strictly worse than the vague bucket it
replaced: the vague bucket said "go and look", and a false clearance says "this
one is done".

These are static tests. They import the script's pure helpers and never boot the
app or touch the database.
"""

import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "parity", "reconcile_urlmap.py")


@pytest.fixture(scope="module")
def reconcile():
    """Import the script by path; `scripts/parity` is not a package."""
    assert os.path.exists(SCRIPT), SCRIPT + " is gone"
    sys.path.insert(0, os.path.join(REPO, "scripts", "parity"))
    spec = importlib.util.spec_from_file_location("reconcile_urlmap", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the extractor is not allowed to go quiet --------------------------------


def test_the_app_sources_still_yield_literal_paths(reconcile):
    """If this returns nothing, every hub-only row silently downgrades to
    `unproven` and the census looks *more* uncertain than it is — a failure
    mode that produces no error and no wrong number, just quiet blindness."""
    literals = reconcile.native_literal_paths()
    assert len(literals) >= 50, (
        "only %d literal paths found in the app's routing sources; the "
        "extractor has probably stopped matching" % len(literals))
    assert "/scam-shield/scan" in literals, (
        "the known-good sample /scam-shield/scan is no longer extracted")


def test_a_missing_source_file_fails_loudly(reconcile, monkeypatch):
    monkeypatch.setattr(reconcile, "NATIVE_LITERAL_SOURCES",
                        (os.path.join("mobile-native", "src", "nope.ts"),))
    with pytest.raises(SystemExit) as excinfo:
        reconcile.native_literal_paths()
    assert "classification" in str(excinfo.value)


# --- the rule itself ---------------------------------------------------------


def test_a_sibling_deep_link_is_not_counted_as_a_parameter_value(reconcile):
    """The marketplace case, which is live: `/pulse/marketplace/create` is both
    a literal in the app's sources and a registered deep link of its own."""
    values = reconcile.native_sample_values(
        "/pulse/marketplace/:listingId",
        every_link_path={"/pulse/marketplace", "/pulse/marketplace/create",
                         "/pulse/marketplace/:listingId"},
        literals={"/pulse/marketplace/create"})
    assert values == [], (
        "/pulse/marketplace/create was counted as a listing id. It is "
        "MarketplaceCreateGateway — its own deep link — so this would clear the "
        "marketplace row while sharing a real listing still 404s")


def test_a_genuine_value_is_counted(reconcile):
    values = reconcile.native_sample_values(
        "/scam-shield/:mode?",
        every_link_path={"/scam-shield/:mode?"},
        literals={"/scam-shield/scan"})
    assert values == ["scan"]


def test_deeper_literals_are_not_flattened_into_one_segment(reconcile):
    """`:section` takes one segment. A two-segment literal underneath it is a
    different route, and folding it in would invent a value nothing produces."""
    values = reconcile.native_sample_values(
        "/pulse/settings/:section",
        every_link_path={"/pulse/settings/:section"},
        literals={"/pulse/settings/privacy", "/pulse/settings/deep/nested"})
    assert values == ["privacy"]


def test_a_multi_parameter_pattern_is_left_alone(reconcile):
    """Single-segment sampling cannot speak for `/a/:b/:c`, so it declines
    rather than guessing — the row stays unproven, which is the honest answer."""
    values = reconcile.native_sample_values(
        "/dashboard/:legacyGroup/:legacyModule/:legacySubmodule?",
        every_link_path=set(),
        literals={"/dashboard/media/videos"})
    assert values == []


def test_a_literal_route_with_no_parameter_is_left_alone(reconcile):
    assert reconcile.native_sample_values(
        "/pulse/saved", every_link_path=set(),
        literals={"/pulse/saved/anything"}) == []


def test_the_base_prefix_is_matched_on_a_boundary_not_a_substring(reconcile):
    """`/pulse/eventsomething` must not be read as a value under
    `/pulse/events`."""
    values = reconcile.native_sample_values(
        "/pulse/events/:eventId", every_link_path=set(),
        literals={"/pulse/eventsomething/x", "/pulse/events/live"})
    assert values == ["live"]
