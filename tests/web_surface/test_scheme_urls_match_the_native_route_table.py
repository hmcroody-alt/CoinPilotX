"""The last hop of "Open in PulseSoc", pinned against the app's own route table.

The interstitial's whole job is to hand a member a `pulsesoc://` URL that opens
the app on the thing they were already reading. Every other link in that chain
is now tested: the PDP emits an off-domain-navigating CTA, the CTA resolves to a
real page, that page carries the listing id into a scheme URL. Then the chain
leaves Python and nothing checked the join.

That join is a string agreement between two files that share no code. The server
builds `pulsesoc://` + `path_template` from `services/app_links.py`. The app
matches it against the `config.screens` tree in
`mobile-native/src/navigation/linking.ts`. Rename a route on either side, or mark
a destination `native_supported` whose screen exists but was never given a
linking path, and the button still renders, still reviews as correct, still
passes every server-side test -- and opens the app to whatever React Navigation
falls back to. "Opens the app to the wrong place" is the defect this mission
exists to repair, arriving one layer later than it did the first time.

iOS gives no error for an unresolvable scheme URL, nothing logs server-side
because following a `pulsesoc://` href never reaches us, and a simulator will not
show it either. So it has to be caught here.

This checks *real URLs*, not templates: each destination is resolved through the
same `resolve_destination_path` the interstitial calls, then matched against the
declared routes with React Navigation's own parameter semantics. Comparing
normalised templates instead would call `/dashboard/network/friends` a failure,
because the app declares it as `dashboard/:legacyGroup/:legacyModule/...` -- a
concrete path and a parameterised route are only comparable through a matcher.

Run: python3 -m pytest tests/web_surface/test_scheme_urls_match_the_native_route_table.py
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links  # noqa: E402

LINKING_TS = ROOT / "mobile-native" / "src" / "navigation" / "linking.ts"


def _load_aasa_health():
    """Reuse the existing `linking.ts` parser rather than writing a second one.

    `tests/web_parity/test_aasa_claims.py` already depends on this module to
    decide what the app declares, and two parsers for one file is how a test
    ends up agreeing with the bug.
    """
    path = ROOT / "scripts" / "web_rebuild" / "aasa_health.py"
    spec = importlib.util.spec_from_file_location("aasa_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = _load_aasa_health()


#: Destinations the registry calls `native_supported` that the app declares no
#: route for. Pinned rather than skipped, and asserted in *both* directions: a
#: new divergence fails, and fixing one of these also fails, which forces the
#: list down instead of letting it rot.
#:
#: `cart` used to be listed here on the grounds that it was "not reachable from an
#: on-site `app_open_cta` today". That stopped being true when `/pulse/cart`
#: shipped a web cart whose "Open cart in the app" button builds
#: `open_interstitial_url("cart")` -- so the pinned dead end became the live one a
#: member actually followed, and `linking.ts` has now been given the path. The
#: entry is deleted rather than kept, because the assertion below is bidirectional
#: and a fixed destination left in this set fails just as loudly as a new gap.
#:
#: - `notification` -> `pulsesoc://pulse/notifications/<id>`. The app declares
#:   `notifications` for `NotificationCenter` and `pulse/notifications` for the
#:   tab; neither takes an id, so the per-notification spelling resolves to
#:   nothing *in `config.screens`*. Note that this file can only see that tree:
#:   `linking.ts` resolves ids through `nativeObjectDestination` before the config
#:   is consulted, and that resolver does map `/pulse/notifications/<id>`. So this
#:   remaining entry records a gap in the declaration the app generates links
#:   from, not a destination a member cannot reach.
KNOWN_UNROUTED = {"notification"}


def _route_matches(template: str, path: str) -> bool:
    """React Navigation's matching, reduced to what these routes actually use.

    A `:param` segment matches exactly one segment; a trailing `:param?` may be
    absent. Literal segments must be equal. Optional segments appear only at the
    end in this file's route table, which is the only place they are meaningful.
    """
    expected = [s for s in template.strip("/").split("/") if s]
    actual = [s for s in path.strip("/").split("/") if s]
    required = len([s for s in expected if not s.endswith("?")])
    if not required <= len(actual) <= len(expected):
        return False
    for got, want in zip(actual, expected):
        if want.startswith(":"):
            continue
        if want != got:
            return False
    return True


@pytest.fixture(scope="module")
def routes():
    """Every path `linking.ts` declares."""
    families = health.declared_native_paths()
    paths = sorted({p for group in families.values() for p in group})
    # A parser that silently found nothing would make every assertion below
    # pass, which is the one failure mode this file cannot afford.
    assert len(paths) >= 20, f"only parsed {len(paths)} paths from linking.ts"
    return paths


def _resolved_path(spec):
    """The path the interstitial would really build for this destination."""
    if not spec.supports_resource:
        return app_links.resolve_destination_path(spec)
    sample = "7" if spec.id_kind == app_links.ID_KIND_POSITIVE_INT else "sample"
    return app_links.resolve_destination_path(spec, sample)


NATIVE = [d for d in app_links.DESTINATIONS.values() if d.native_supported]


def test_the_route_table_was_actually_read(routes):
    assert LINKING_TS.exists(), LINKING_TS
    assert "pulse/marketplace/:listingId" in routes, routes[:40]
    assert len(NATIVE) >= 15, f"only {len(NATIVE)} native destinations found"


@pytest.mark.parametrize("spec", NATIVE, ids=lambda d: d.key)
def test_every_native_destination_resolves_to_a_route_the_app_declares(spec, routes):
    """The identity check. One case per destination so a failure names it.

    A destination the registry calls `native_supported` is one the interstitial
    will happily build a `pulsesoc://` button for. If the app declares no
    matching route, that button is a dead end that looks live.
    """
    path = _resolved_path(spec)
    matched = [r for r in routes if _route_matches(r, path)]

    if spec.key in KNOWN_UNROUTED:
        assert not matched, (
            f"{spec.key!r} now resolves to {path!r}, matched by {matched}. It is "
            "listed in KNOWN_UNROUTED as a destination the app cannot reach -- "
            "if that has been fixed, remove it from the set so the strict "
            "assertion starts protecting it."
        )
        return

    assert matched, (
        f"{spec.key!r} builds pulsesoc://{path.lstrip('/')} (native_screen="
        f"{spec.native_screen!r}), which linking.ts declares no route for. "
        "Either the app route was renamed, or this destination is marked "
        "native_supported before it is linkable. The button renders either way "
        "and opens the app to whatever React Navigation falls back to."
    )


def test_the_product_scheme_url_is_the_one_the_app_can_resolve(routes):
    """`product` spelled out, because it is the destination the mission is about.

    Built through the same `resolve_destination_path` the interstitial calls, so
    this is the literal string a member's phone receives -- not a reconstruction
    of it.
    """
    path = app_links.resolve_destination_path("product", 4821)
    assert app_links.app_scheme_url(path) == "pulsesoc://pulse/marketplace/4821"
    assert [r for r in routes if _route_matches(r, path)] == [
        "pulse/marketplace/:listingId"
    ]


def test_the_marketplace_root_and_detail_are_different_routes(routes):
    """The "opens Home instead of the product" symptom, as a matching property.

    If the detail route were dropped, `pulse/marketplace/9` would fall back to
    whatever else matched -- and a member would land on the marketplace root
    with their listing forgotten. Asserting the root does *not* match a detail
    URL is what makes the previous test's single match meaningful.
    """
    assert not _route_matches("pulse/marketplace", "/pulse/marketplace/9")
    assert _route_matches("pulse/marketplace", "/pulse/marketplace")


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_a_renamed_app_route_is_caught(routes):
    """Proves the comparison is real and not accidentally always-true.

    Drop the marketplace detail route -- the exact effect of renaming it in
    `linking.ts` -- and the product destination must fail.
    """
    mutated = [r for r in routes if r != "pulse/marketplace/:listingId"]
    assert len(mutated) == len(routes) - 1, "MUTATION TARGET NOT FOUND"
    with pytest.raises(AssertionError):
        test_every_native_destination_resolves_to_a_route_the_app_declares(
            app_links.DESTINATIONS["product"], mutated
        )


def test_mutation_a_matcher_that_matched_everything_is_caught(routes):
    """The other way this file could be vacuous: an over-permissive matcher."""
    assert not _route_matches("pulse/post/:postId", "/pulse/marketplace/7")
    assert not _route_matches("pulse/marketplace/:id", "/pulse/marketplace/7/extra")
    assert not _route_matches("pulse/marketplace/:id", "/pulse/marketplace")
    assert _route_matches("pulse/safety/:section?", "/pulse/safety")
    assert _route_matches("pulse/safety/:section?", "/pulse/safety/reporting")


def test_the_known_gap_list_names_only_real_destinations():
    """A typo in `KNOWN_UNROUTED` would silently exempt nothing -- or worse,
    exempt a destination that no longer exists while the real one goes
    unchecked."""
    for key in KNOWN_UNROUTED:
        assert key in app_links.DESTINATIONS, key
        assert app_links.DESTINATIONS[key].native_supported, key
