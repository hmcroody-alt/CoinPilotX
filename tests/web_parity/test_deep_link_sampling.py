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
*cannot* speak for. Two annotations answer it. `PROVEN_ELSEWHERE` points the row
at a test that settles it; `NO_DATA_SOURCE` says there is nothing in the product
to build the row against, so it is blocked rather than pending. Both are escapes
from `unproven` that do not involve loosening the rule above, and are therefore
the obvious places to smuggle a clearance in. Their guards are tested here for
that reason: an entry may silence `unproven` and nothing else, must name a test
file that exists, and must fail the moment its premise stops holding.

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


def test_a_route_literal_may_contain_the_other_quote(reconcile):
    """The scanner's own blind spot, found by the reconciler rather than by a
    test — which is the only reason it was found at all.

    Flask's `any` converter spells its options in quotes, so a double-quoted
    route path can legitimately contain single quotes. The path matcher was
    written as `["\\']...[^"\\']*...["\\']`, which rejects exactly that and
    returns None, and a None literal is *skipped* rather than reported. The
    route then simply is not in the census: no error, no wrong number, just a
    live rule the scan cannot see.

    Only one route in the repo has this shape today, so the cost was one path.
    The reason to pin it is the failure mode, not the count — this is the same
    silent-skip that once hid the whole Private Office blueprint.
    """
    literal = reconcile.census._literal_path(
        '"/dashboard/<any(\'media\', \'safety\'):legacy_group>"', {})
    assert literal == "/dashboard/<any('media', 'safety'):legacy_group>", (
        "a double-quoted route path containing single quotes no longer "
        "resolves; every `any(...)` route has dropped out of the census")
    assert reconcile.census._literal_path("'/single/quoted'", {}) == \
        "/single/quoted", "single-quoted route paths stopped resolving"
    assert reconcile.census._literal_path("PREFIX + '/tail'",
                                          {"PREFIX": "/p"}) == "/p/tail"
    assert reconcile.census._literal_path('some_variable', {}) is None, (
        "an unresolvable first argument must stay None rather than being "
        "guessed at; the widened quote matching must not have widened this")


def test_the_scan_and_the_live_url_map_still_agree_on_the_any_route(reconcile):
    """The check that would have caught the above on its own. `reconcile --check`
    compares both directions, and "live but unseen by scan" is always a scanner
    bug — so the route that exposed the bug is worth naming here, because a
    regression in the matcher would otherwise show up only as a number moving in
    a report nobody diffs."""
    paths = {route.path for route in reconcile.census.collect_web_routes()}
    assert "/dashboard/<any('media', 'safety'):legacy_group>/<path:module_alias>" \
        in paths, (
            "the legacy dashboard alias route is invisible to the static scan "
            "again. It is live in the url_map, so the census now undercounts "
            "the web surface and the reconciler will report it as a scanner bug.")


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


def _write(reconcile, tmp_path, monkeypatch, hub_only, verdicts, broken=(),
           proven=None, sourceless=None):
    """Generate the doc into a temp file and return it. The real
    `PULSESOC_DEEPLINK_PARITY.md` is a committed artifact; a test must not
    rewrite it with fixture data.

    Every annotation dict is replaced here, not just the one under test. These
    tests describe a two-row world; a live entry left in place is validated
    against that world and fails on a path it never named. This helper used to
    neutralize only `BLOCKED_DEEP_LINKS`, and adding `NO_DATA_SOURCE` to the
    script duly broke four tests that had nothing to do with it. So the
    annotations are passed in rather than monkeypatched by each test: a third
    kind cannot repeat the failure, because it will have to be listed here to
    exist at all.
    """
    out = os.path.join(str(tmp_path), "doc.md")
    monkeypatch.setattr(reconcile, "DEEP_LINK_DOC", out)
    monkeypatch.setattr(reconcile, "BLOCKED_DEEP_LINKS", {})
    monkeypatch.setattr(reconcile, "PROVEN_ELSEWHERE", dict(proven or {}))
    monkeypatch.setattr(reconcile, "NO_DATA_SOURCE", dict(sourceless or {}))
    reconcile.write_deep_link_doc([], list(hub_only), list(broken), verdicts)
    return open(out, encoding="utf-8").read()


def test_the_helper_neutralizes_every_annotation_the_script_defines(reconcile):
    """The guard on the paragraph above.

    If a fourth annotation dict is added to the reconciler and `_write` is not
    taught about it, this fails immediately rather than in four unrelated tests
    whose failure message names the wrong subject.
    """
    neutralized = {"BLOCKED_DEEP_LINKS", "PROVEN_ELSEWHERE", "NO_DATA_SOURCE"}
    defined = {name for name in dir(reconcile)
               if name.isupper() and name.endswith(("_DEEP_LINKS", "_ELSEWHERE",
                                                    "_SOURCE"))}
    assert defined == neutralized, (
        "the reconciler's annotation dicts are %s but _write only neutralizes "
        "%s; add the new one to _write before it starts failing other tests"
        % (sorted(defined), sorted(neutralized)))


def test_a_proven_row_is_annotated_and_leaves_the_open_bucket(reconcile,
                                                              tmp_path,
                                                              monkeypatch):
    doc = _write(reconcile, tmp_path, monkeypatch,
                 [_link(reconcile, "/a/:b")], {"/a/:b": ("unproven", [])},
                 proven={"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                   "because reasons")})
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
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch,
               [_link(reconcile, "/a/:b")], {"/a/:b": ("unproven", [])},
               proven={"/a/:b": ("tests/web_parity/deleted.py", "why")})
    assert "does not exist" in str(excinfo.value)


def test_a_proof_may_not_silence_a_row_this_script_proved_broken(reconcile,
                                                                 tmp_path,
                                                                 monkeypatch):
    """The one that matters. If the sampler finds a value the app really
    produces and the web 404s on it, that is a confirmed gap — and an entry
    written back when the row merely looked unresolvable would quietly move it
    into the cleared section. The verdict has to be `unproven`, not just
    hub-only."""
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [_link(reconcile, "/a/:b")],
               {"/a/:b": ("gap", [("real", False)])},
               proven={"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                 "why")})
    assert "hiding a gap" in str(excinfo.value)


def test_a_proof_for_a_row_that_is_no_longer_hub_only_fails_loudly(reconcile,
                                                                   tmp_path,
                                                                   monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [], {},
               proven={"/gone/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                    "why")})
    assert "no longer an unproven hub-only link" in str(excinfo.value)


def test_the_private_office_entry_still_names_a_test_that_exists(reconcile):
    """The live entry, checked without generating anything. `--check` skips doc
    generation entirely, so the guards above do not run in the gate."""
    assert "/pulse/private-office/:view" in reconcile.PROVEN_ELSEWHERE
    for path, (test, why) in reconcile.PROVEN_ELSEWHERE.items():
        assert os.path.exists(os.path.join(REPO, test)), (
            "%s points at %s, which is gone" % (path, test))
        assert why.strip(), "%s has an empty rationale" % path


# --- declaring a row unbuildable is the strongest claim on offer -------------
#
# `PROVEN_ELSEWHERE` says "someone else checked this". `NO_DATA_SOURCE` says
# "there is nothing to build against", which excuses the row from the work list
# rather than pointing at work already done. It gets the same guards, tested the
# same way, because it is the annotation most worth abusing.


def test_a_sourceless_row_is_counted_separately_from_a_proven_one(
        reconcile, tmp_path, monkeypatch):
    """Two rows, one of each kind, in the same document.

    Folding them into one bucket would be the quiet failure: "cleared by a test
    elsewhere" and "blocked, nothing to build against" are opposite states, and
    a reader who saw the wrong one would either go looking for a proof that does
    not exist or skip a row that is genuinely done.
    """
    doc = _write(reconcile, tmp_path, monkeypatch,
                 [_link(reconcile, "/a/:b"), _link(reconcile, "/c/:d")],
                 {"/a/:b": ("unproven", []), "/c/:d": ("unproven", [])},
                 proven={"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                   "checked over there")},
                 sourceless={"/c/:d": ("tests/web_parity/test_deep_link_sampling.py",
                                       "no such table exists")})
    assert "- Hub served, item links 404: **0**" in doc
    assert "- Hub served, cleared by a test elsewhere: **1**" in doc
    assert "- Hub served, item link BLOCKED — no data source exists: **1**" in doc
    assert "no such table exists" in doc, (
        "the rationale is not rendered, so the row reads as blocked by fiat")


def test_a_sourceless_entry_that_names_a_missing_test_fails_loudly(
        reconcile, tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch,
               [_link(reconcile, "/a/:b")], {"/a/:b": ("unproven", [])},
               sourceless={"/a/:b": ("tests/web_parity/deleted.py", "why")})
    assert "does not exist" in str(excinfo.value)


def test_a_sourceless_entry_may_not_silence_a_row_proved_broken(
        reconcile, tmp_path, monkeypatch):
    """The `PROVEN_ELSEWHERE` rule, restated for the stronger claim.

    If the sampler found a real value and the web 404ed on it, the row is a
    confirmed gap and "there is no data source" is refuted by the sample itself.
    """
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [_link(reconcile, "/a/:b")],
               {"/a/:b": ("gap", [("real", False)])},
               sourceless={"/a/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                     "why")})
    assert "no longer an unproven hub-only link" in str(excinfo.value)


def test_a_sourceless_entry_for_a_row_that_now_resolves_fails_loudly(
        reconcile, tmp_path, monkeypatch):
    """The self-clearing half. When the source arrives and the web surface gets
    built, the row leaves the hub-only bucket and this refuses to regenerate —
    so the block cannot outlive the reason for it."""
    with pytest.raises(SystemExit) as excinfo:
        _write(reconcile, tmp_path, monkeypatch, [], {},
               sourceless={"/gone/:b": ("tests/web_parity/test_deep_link_sampling.py",
                                        "why")})
    assert "no longer an unproven hub-only link" in str(excinfo.value)


def test_the_events_entry_still_names_a_test_that_exists(reconcile):
    """The live entry, checked in the gate for the reason given above."""
    assert "/pulse/events/:eventId" in reconcile.NO_DATA_SOURCE
    for path, (test, why) in reconcile.NO_DATA_SOURCE.items():
        assert os.path.exists(os.path.join(REPO, test)), (
            "%s points at %s, which is gone" % (path, test))
        assert why.strip(), "%s has an empty rationale" % path


def test_a_row_may_not_carry_both_annotations(reconcile):
    """"Cleared" and "blocked" cannot both be true, and the document renders
    each row in exactly one section — so an overlap would silently drop the row
    from whichever section ran second, taking its rationale with it."""
    overlap = sorted(set(reconcile.PROVEN_ELSEWHERE) & set(reconcile.NO_DATA_SOURCE))
    assert not overlap, (
        "%s is annotated as both proven elsewhere and blocked for want of a "
        "data source; decide which it is" % overlap)
