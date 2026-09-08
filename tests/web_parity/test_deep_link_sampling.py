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

The other half of the same question is what to do with a row the sampler
*cannot* speak for. `PROVEN_ELSEWHERE` lets a row point at a test that settles
it instead, which is the only escape from `unproven` that does not involve
loosening the rule above — and is therefore the obvious place to smuggle a
clearance in. Its guards are tested here for that reason: an entry may silence
`unproven` and nothing else.

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
    assert len(literals) >= 300, (
        "only %d literal paths found under mobile-native/src; the extractor "
        "has probably stopped matching" % len(literals))
    assert "/scam-shield/scan" in literals, (
        "the known-good sample /scam-shield/scan is no longer extracted")


def test_values_are_scraped_from_the_whole_app_not_just_the_routing_files(
        reconcile):
    """The scan must reach beyond `navigation/`. The camera's five modes are
    declared as `providerRoute` fields on a screen, and scanning only the two
    routing files found none of them — which left the row `unproven` while the
    web served all five. Worse than incomplete: the verdict clears a row when
    every value found resolves, so a value the scan cannot see is a value that
    cannot contribute its 404, and the row would clear on a subset."""
    literals = reconcile.native_literal_paths()
    for path in ("/pulse/camera/post", "/pulse/camera/reel",
                 "/pulse/camera/status"):
        assert path in literals, (
            "%s is declared on CameraStudioScreen and is missing; the scan has "
            "narrowed back to the navigation folder" % path)


def test_a_path_with_a_query_string_is_still_seen(reconcile):
    """`masterNavigation.ts` spells the camera as
    `/pulse/camera/photo?target=feed`. A pattern that requires the closing quote
    to follow the path matches that string *not at all*, silently dropping a
    real value rather than merely mis-reading it."""
    assert "/pulse/camera/photo" in reconcile.native_literal_paths()


def test_paths_that_only_appear_in_comments_are_not_treated_as_real(reconcile):
    """`notificationRouting.ts` illustrates itself with `"/pulse/foo/123?token=
    x"`. An example in prose is not a route the app can produce, and counting
    one would let a comment clear a real gap."""
    literals = reconcile.native_literal_paths()
    assert "/pulse/foo/123" not in literals, (
        "a path from a code comment reached the value set; comment stripping "
        "has stopped working")


def test_a_missing_source_tree_fails_loudly(reconcile, monkeypatch):
    monkeypatch.setattr(reconcile, "NATIVE_LITERAL_ROOT",
                        os.path.join("mobile-native", "nope"))
    with pytest.raises(SystemExit) as excinfo:
        reconcile.native_literal_paths()
    assert "classification" in str(excinfo.value)


def test_a_canary_that_stops_being_extracted_fails_loudly(reconcile,
                                                          monkeypatch):
    """The guard that catches a half-broken extractor — one still returning
    hundreds of paths, just no longer the ones a row was cleared on."""
    monkeypatch.setattr(reconcile, "NATIVE_LITERAL_CANARIES",
                        ("/this/path/is/nowhere",))
    with pytest.raises(SystemExit) as excinfo:
        reconcile.native_literal_paths()
    assert "no longer extracted" in str(excinfo.value)


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


# --- pointing a row at a proof is not the same as clearing it ----------------


def _link(reconcile, path, screen="Screen"):
    return reconcile.census.NativeDeepLink(screen=screen, path=path)


def _write(reconcile, tmp_path, monkeypatch, hub_only, verdicts, broken=()):
    """Generate the doc into a temp file and return it. The real
    `PULSESOC_DEEPLINK_PARITY.md` is a committed artifact; a test must not
    rewrite it with fixture data."""
    out = os.path.join(str(tmp_path), "doc.md")
    monkeypatch.setattr(reconcile, "DEEP_LINK_DOC", out)
    monkeypatch.setattr(reconcile, "BLOCKED_DEEP_LINKS", {})
    reconcile.write_deep_link_doc([], list(hub_only), list(broken), verdicts)
    return open(out, encoding="utf-8").read()


def test_a_proven_row_is_annotated_and_leaves_the_open_bucket(reconcile,
                                                              tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(reconcile, "PROVEN_ELSEWHERE",
                        {"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                   "because reasons")})
    doc = _write(reconcile, tmp_path, monkeypatch,
                 [_link(reconcile, "/a/:b")], {"/a/:b": ("unproven", [])})
    assert "- Hub served, item links 404: **0**" in doc, (
        "a row with a proof is still being counted as an open gap")
    assert "- Hub served, cleared by a test elsewhere: **1**" in doc
    assert "because reasons" in doc, (
        "the rationale is not rendered, so the row reads as cleared by fiat")


def test_a_proof_that_names_a_missing_test_file_fails_loudly(reconcile,
                                                             tmp_path,
                                                             monkeypatch):
    """A dangling pointer is worse than no pointer: the row still leaves the
    open bucket, but nothing is checking the claim any more."""
    monkeypatch.setattr(reconcile, "PROVEN_ELSEWHERE",
                        {"/a/:b": ("tests/web_parity/deleted.py", "why")})
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch,
               [_link(reconcile, "/a/:b")], {"/a/:b": ("unproven", [])})
    assert "does not exist" in str(excinfo.value)


def test_a_proof_may_not_silence_a_row_this_script_proved_broken(reconcile,
                                                                 tmp_path,
                                                                 monkeypatch):
    """The one that matters. If the sampler finds a value the app really
    produces and the web 404s on it, that is a confirmed gap — and an entry
    written back when the row merely looked unresolvable would quietly move it
    into the cleared section. The verdict has to be `unproven`, not just
    hub-only."""
    monkeypatch.setattr(reconcile, "PROVEN_ELSEWHERE",
                        {"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                   "why")})
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [_link(reconcile, "/a/:b")],
               {"/a/:b": ("gap", [("real", False)])})
    assert "hiding a gap" in str(excinfo.value)


def test_a_proof_for_a_row_that_is_no_longer_hub_only_fails_loudly(reconcile,
                                                                   tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(reconcile, "PROVEN_ELSEWHERE",
                        {"/gone/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                      "why")})
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [], {})
    assert "no longer an unproven hub-only link" in str(excinfo.value)


def test_the_private_office_entry_still_names_a_test_that_exists(reconcile):
    """The live entry, checked without generating anything. `--check` skips doc
    generation entirely, so the guards above do not run in the gate."""
    assert "/pulse/private-office/:view" in reconcile.PROVEN_ELSEWHERE
    for path, (test, why) in reconcile.PROVEN_ELSEWHERE.items():
        assert os.path.exists(os.path.join(REPO, test)), (
            "%s points at %s, which is gone" % (path, test))
        assert why.strip(), "%s has an empty rationale" % path
