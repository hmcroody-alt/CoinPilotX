"""The website hands off to the app — or it quietly stops, and nobody notices.

The rebuilt site is required to promote the iOS app throughout. The load-bearing
form of that promotion is the universal link: tap `pulsesoc.com/pulse/post/812`
in Messages, the installed app opens on that post. One file decides whether that
happens, and every way it breaks is silent:

- The app declares a path that the association does not claim. The link opens
  the website. No error, no log line, nothing to alert on — and the failure is
  invisible to anyone testing on a simulator, where associated domains do not
  work at all.
- The association claims a path the web needs to keep. Support, the trust
  centre, the scam checker: the pages a person reaches *because* the app is the
  problem. Claiming those closes the last door.
- The association claims `/`. The web rebuild ceases to exist for anyone with
  the app installed.

These tests check the built payload against `mobile-native/src/navigation/
linking.ts` — the app's own route table — so the two cannot drift apart
silently. They import the checker rather than reimplementing it, because two
copies of a path-matching rule is how the test ends up agreeing with the bug.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_checker():
    path = ROOT / "scripts" / "web_rebuild" / "aasa_health.py"
    spec = importlib.util.spec_from_file_location("aasa_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = _load_checker()


@pytest.fixture(scope="module")
def payload(monkeypatch_session=None):
    import os

    os.environ.setdefault("PULSESOC_APPLE_TEAM_ID", "A1B2C3D4E5")
    from services.native_app_links import apple_app_site_association

    built, error = apple_app_site_association()
    assert built is not None, error
    return built


@pytest.fixture(scope="module")
def patterns(payload):
    return health.claimed_patterns(payload)


@pytest.fixture(scope="module")
def components(payload):
    return health.ordered_components(payload)


@pytest.fixture(scope="module")
def families():
    return health.declared_native_paths()


def test_the_native_route_table_was_actually_found(families):
    """A parser that silently finds nothing would make every test below pass."""
    assert len(families) >= 10, f"only found {sorted(families)}"
    assert len(families["pulse"]) > 50


@pytest.mark.parametrize("family", sorted(health.DECISIONS))
def test_every_declared_family_matches_its_decision(family, families, components):
    paths = families.get(family)
    if paths is None:
        pytest.skip(f"{family} is no longer declared in linking.ts")
    decision, reason = health.DECISIONS[family]
    missed = health.unclaimed_urls(paths, components)
    total = sum(len(health.concrete_urls(p)) for p in paths)
    if decision == "CLAIMED":
        assert not missed, (
            f"{len(missed)} of {family}'s {total} URLs are not claimed by any "
            f"AASA component, so they open the website instead of the app: {missed[:6]}"
        )
    else:
        assert len(missed) == total, (
            f"{family} is WEB_ONLY ({reason}) but the AASA claims "
            f"{total - len(missed)} of its {total} URLs"
        )


def test_no_family_is_undecided(families):
    """The gate on the rebuild inventing a URL family.

    A new `/explore` or `/watch` section is fine — as long as someone decided
    whether the app should receive it. Silence is how a deep link family goes
    missing for a release.
    """
    undecided = sorted(set(families) - set(health.DECISIONS))
    assert not undecided, (
        f"{undecided} are declared in linking.ts with no entry in "
        f"aasa_health.DECISIONS. Add them as CLAIMED or WEB_ONLY with a reason."
    )


def test_the_bare_home_path_is_claimed(components):
    """`/pulse/*` does not match `/pulse`.

    Apple's `*` matches a run of characters but the literal `/` in front of it
    still has to be present, so the pattern that claims every object in the
    product misses its own front door. `/search*` has no slash and so has never
    had this problem, which is exactly why the asymmetry survived unnoticed.
    """
    assert health.opens_in_app(components, "/pulse")
    assert health.opens_in_app(components, "/pulse/post/812")


def test_the_association_never_claims_the_whole_site(payload):
    assert not health.check_payload_shape(payload)


def test_support_surfaces_stay_on_the_web(components):
    """Named individually, because the reasoning is specific and easy to lose.

    Each of these is reached by someone for whom the app is not a working
    destination: it will not open, their account is locked, they are checking
    whether a link is a scam, or they have not installed it and are deciding.
    """
    for url in ("/help", "/trust-center", "/security", "/privacy-center", "/scam-shield"):
        assert not health.opens_in_app(components, url), (
            f"{url} must stay reachable in a browser"
        )


def test_the_matcher_follows_apples_wildcard_not_fnmatch():
    """`fnmatch`'s `*` stops at `/`; Apple's does not.

    If this ever regresses to `fnmatch`, every test above keeps passing while
    reporting `/pulse/*` as failing to claim `/pulse/post/812` — the checker
    would be wrong in the direction that looks like extra safety.
    """
    assert health.component_matches("/pulse/*", "/pulse/post/812")
    assert health.component_matches("/search*", "/search")
    assert not health.component_matches("/pulse/*", "/pulse")
    assert not health.component_matches("/saved", "/saved/x")


def test_optional_segments_expand_to_both_urls():
    """`pulse/safety/:section?` is two real URLs; testing one hides half the gap."""
    assert health.concrete_urls("pulse/safety/:section?") == [
        "/pulse/safety",
        "/pulse/safety/sample",
    ]


def test_the_web_client_shell_stays_in_the_browser(components):
    """`/pulse/app` is the browser client, sitting inside the app's own prefix.

    Everything else under `/pulse/` is a native object, so `/pulse/*` claims the
    lot -- correctly. The web client shell is the exception: `linking.ts`
    declares no route for it, and an unresolvable universal link is not a
    graceful fallback. iOS has already decided to open the app by then; React
    Navigation simply fails to resolve the URL and the user is left on whatever
    screen was showing.
    """
    for url in ("/pulse/app", "/pulse/app/", "/pulse/app/feed", "/pulse/app/profile/812"):
        assert not health.opens_in_app(components, url), (
            f"{url} is the web client and must open in a browser"
        )


def test_carving_out_the_web_client_did_not_carve_out_the_app(components):
    """The exclusion must be surgical.

    `/pulse/app*` without the slash would also swallow `/pulse/apple-pay` or any
    future `/pulse/app...` object. This is the check that the carve-out cost
    nothing, and it is why the components are `/pulse/app` and `/pulse/app/*`
    rather than one `/pulse/app*`.
    """
    for url in ("/pulse/post/812", "/pulse/apparel", "/pulse/applications", "/pulse/apple-pay"):
        assert health.opens_in_app(components, url), f"{url} must still open the app"


def test_the_exclusion_sits_above_the_pattern_it_carves_out(components):
    """Position, not presence. This is the half a flat pattern list cannot see.

    iOS stops at the first matching component, so `/pulse/app (exclude)` below
    `/pulse/*` is unreachable configuration: the file still contains the
    exclusion, still reviews as correct, and does nothing at all. Nothing about
    the *set* of components changes when they are reordered, which is exactly
    why this has to be asserted on the order.
    """
    patterns_in_order = [pattern for pattern, _ in components]
    broad = patterns_in_order.index("/pulse/*")
    for carved in ("/pulse/app", "/pulse/app/*"):
        assert patterns_in_order.index(carved) < broad, (
            f"{carved} is listed after /pulse/*, so iOS never reaches it"
        )


def test_reordering_the_exclusion_below_the_broad_pattern_breaks_the_carve_out():
    """Anti-vacuity for the test above, and for `opens_in_app` itself.

    If the resolver ignored order -- as `claimed_patterns` does by construction
    -- every assertion about the exclusion would pass no matter where it sat.
    Ordering must be *observable*, so here it is observed: the same components
    in the wrong order give the opposite answer.
    """
    correct = [("/pulse/app", True), ("/pulse/*", False)]
    reversed_order = [("/pulse/*", False), ("/pulse/app", True)]
    assert not health.opens_in_app(correct, "/pulse/app")
    assert health.opens_in_app(reversed_order, "/pulse/app")


def test_the_flat_pattern_view_is_the_one_that_gets_this_wrong():
    """Documents why `claimed_patterns` must not be used for URL questions.

    Kept as a test rather than a comment because it is a live trap: the function
    is still exported, still used by the shape check, and reads like it answers
    "is this URL claimed?". It does not, and this pins the exact disagreement.
    """
    payload = {
        "applinks": {
            "details": [
                {
                    "components": [
                        {"/": "/pulse/app", "exclude": True},
                        {"/": "/pulse/*"},
                    ]
                }
            ]
        }
    }
    flat = health.claimed_patterns(payload)
    ordered = health.ordered_components(payload)
    assert any(health.component_matches(p, "/pulse/app") for p in flat), (
        "the flat view claims /pulse/app ..."
    )
    assert not health.opens_in_app(ordered, "/pulse/app"), "... and iOS does not"


def test_both_bundle_ids_get_the_same_component_list(payload):
    """Production ships the release and dev bundle IDs the same list.

    `ordered_components` speaks for "the app" in the singular and is only
    entitled to when there is one answer. If the two ever diverge it raises
    rather than silently answering for whichever appID happens to be first.
    """
    lists = health.component_lists(payload)
    assert len(lists) == 2, f"expected release + dev bundle IDs, got {len(lists)}"
    assert lists[0] == lists[1]
    assert health.ordered_components(payload) == lists[0]


def test_divergent_component_lists_raise_instead_of_guessing():
    payload = {
        "applinks": {
            "details": [
                {"appID": "A.one", "components": [{"/": "/pulse/*"}]},
                {"appID": "A.two", "components": [{"/": "/saved"}]},
            ]
        }
    }
    with pytest.raises(ValueError):
        health.ordered_components(payload)
    # The per-app question still has an answer, and it is per-app.
    assert health.opens_in_app_anywhere(payload, "/pulse/post/1")
    assert health.opens_in_app_anywhere(payload, "/saved")
