"""Locks for the gate that checks every client call against the server's routes.

The failure this guards against is one this repository has already shipped: a
client calls an endpoint the backend never implemented, the client swallows the
404, and the feature is quietly dead. Nothing catches it, because the client
builds and the server starts -- the break lives between them.

`scripts/ops/route_contract_gate.py` closes that gap by deriving the contract
from client source on every run. Which makes the gate itself the thing that has
to be trusted, and a gate is exactly the kind of code that fails silently: every
way it can be wrong -- a regex that stops matching, an extractor that finds
nothing, an allowlist that grows -- makes it *quieter*, not louder. A broken
gate and a clean codebase look identical from the outside.

So these tests assert the gate *fails* when it should, which is the half no
amount of running it on a healthy tree can demonstrate. They drive the real
`main()` with synthesised inputs rather than writing files, so nothing here can
touch the repository.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import contextlib
import io
import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "ops" / "route_contract_gate.py"
ALLOWLIST_PATH = REPO / "config" / "route-contract-allowlist.json"


def _load_gate():
    """Import the gate module without importing `bot`.

    `load_server_rules()` boots a 124k-line Flask monolith, so every test below
    injects a small rule list instead. The rules that matter to these tests are
    the *shapes* -- a converter, an int converter, a path converter, a catch-all
    final segment -- not the 2067 real ones.
    """
    spec = importlib.util.spec_from_file_location("route_contract_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GATE = _load_gate()

RULES = [
    "/api/pulse/feed/home",
    "/api/pulse/posts/<int:post_id>/likes",
    "/api/pages/<int:page_id>/links",
    "/api/business-os/suppliers/cj/connections/<connection_id>/<action>",
    "/api/pulse/live/<int:live_id>/guests/<int:guest_id>/<action>",
    "/api/media/<path:key>",
]


def _site(path, src=""):
    return {"client": "test", "file": "t.ts", "path": path, "src": src}


def _run(sites, rules=None, unresolved=(), allow=None, argv=()):
    """Drive the real main() with injected inputs. Returns (exit_code, output)."""
    rules = RULES if rules is None else rules
    allow = {} if allow is None else allow
    saved = (GATE.extract_call_sites, GATE.load_server_rules,
             GATE.load_allowlist, sys.argv)
    GATE.extract_call_sites = lambda: (list(sites), list(unresolved))
    GATE.load_server_rules = lambda: list(rules)
    GATE.load_allowlist = lambda: dict(allow)
    sys.argv = ["route_contract_gate.py", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = GATE.main()
    finally:
        (GATE.extract_call_sites, GATE.load_server_rules,
         GATE.load_allowlist, sys.argv) = saved
    return code, buf.getvalue()


# --- the failure the gate exists to catch -------------------------------------

def test_a_client_path_with_no_server_route_fails_the_gate():
    code, out = _run([_site("/api/pulse/does-not-exist")])
    assert code == GATE.EXIT_CONTRACT_BREAK, (code, out)
    assert "/api/pulse/does-not-exist" in out


def test_a_healthy_contract_passes():
    code, out = _run([
        _site("/api/pulse/feed/home"),
        _site("/api/pulse/posts/${postId}/likes"),
        _site("/api/media/${encodeURIComponent(key)}"),
    ])
    assert code == GATE.EXIT_OK, (code, out)


# --- the three outcomes stay three --------------------------------------------

def test_an_unresolvable_call_site_is_not_a_pass():
    """The category that makes a gate lie about its own coverage.

    Half the call sites here contain template interpolation. A gate that
    quietly skipped the ones it could not parse would check the other half and
    print a clean result -- the same silent-narrowing defect it was written to
    catch, committed by the gate. `unresolved` gets its own exit code so a
    caller can tell "the contract is broken" from "I could not check it".
    """
    code, out = _run(
        [_site("/api/pulse/feed/home")],
        unresolved=[{"client": "test", "file": "t.ts", "raw": "${BASE}/x",
                     "why": "constant defined in another file"}],
    )
    assert code == GATE.EXIT_NO_DATA, (code, out)
    assert code != GATE.EXIT_OK and code != GATE.EXIT_CONTRACT_BREAK


def test_finding_no_call_sites_at_all_is_not_a_pass():
    """The loudest way for this gate to go silent.

    Rename the wrapper, move `src/api`, or change the call syntax, and the
    extractor returns nothing. Zero call sites, zero mismatches, exit 0 -- a
    gate reporting perfect health precisely because it has stopped looking.
    """
    code, out = _run([])
    assert code == GATE.EXIT_NO_DATA, (code, out)


def test_a_server_with_no_routes_is_not_a_pass():
    code, out = _run([_site("/api/pulse/feed/home")], rules=[])
    assert code == GATE.EXIT_NO_DATA, (code, out)


# --- the allowlist has to be able to fail, in both directions ------------------

def test_an_allowlisted_path_does_not_fail_the_gate():
    allow = {"/api/calls/voip-token": {
        "path": "/api/calls/voip-token", "reason": "r",
        "reference": "ref", "added": "2026-09-13"}}
    code, out = _run([_site("/api/calls/voip-token")], allow=allow)
    assert code == GATE.EXIT_OK, (code, out)
    assert "allowed" in out


def test_an_allowlist_entry_whose_route_now_exists_fails_as_stale():
    """A list that can only ever hide failures rots until it hides a real one.

    The entry is written when the endpoint is deliberately unbuilt. Nothing
    makes anyone revisit it the day the endpoint lands -- so from then on the
    gate holds a standing suppression for a path it would otherwise verify, and
    a later regression on that path is pre-approved. Failing on a stale entry is
    what stops the list growing in one direction only.
    """
    allow = {"/api/pulse/feed/home": {
        "path": "/api/pulse/feed/home", "reason": "r",
        "reference": "ref", "added": "2026-09-13"}}
    code, out = _run([_site("/api/pulse/feed/home")], allow=allow)
    assert code == GATE.EXIT_CONTRACT_BREAK, (code, out)
    assert "STALE" in out


def test_an_allowlist_entry_without_a_reason_is_refused():
    """A suppression with no written-down decision behind it is a forgotten bug."""
    import json
    import tempfile
    saved = GATE.ALLOWLIST_PATH
    try:
        tmp = pathlib.Path(tempfile.mkdtemp()) / "allow.json"
        tmp.write_text(json.dumps(
            {"pending_backend": [{"path": "/api/x", "added": "2026-09-13"}]}))
        GATE.ALLOWLIST_PATH = tmp
        try:
            GATE.load_allowlist()
        except SystemExit as exc:
            assert "reason" in str(exc), exc
        else:
            raise AssertionError("an entry with no reason was accepted")
    finally:
        GATE.ALLOWLIST_PATH = saved


def test_the_real_allowlist_is_well_formed():
    entries = GATE.load_allowlist()
    for path, entry in entries.items():
        assert path.startswith("/"), entry
        assert len(entry["reason"]) > 40, (
            f"{path}: a one-word reason is not a decision anyone can review")
        assert (REPO / entry["reference"].split(" ")[0]).exists(), (
            f"{path}: reference {entry['reference']!r} does not point at a "
            f"file that exists")


# --- matching: the widenings must not swallow a real miss ---------------------

def test_a_converter_where_the_client_has_a_literal_still_matches():
    """`.../<connection_id>/<action>` really does serve `.../health`.

    Comparing the client path to the rule *text* misses this, because `health`
    is not `<action>`. Four real endpoints were reported missing that way. A
    gate with false alarms is a gate somebody switches off, so this is not a
    cosmetic fix.
    """
    ok, how = GATE.match(
        _site("/api/business-os/suppliers/cj/connections/${cid}/health"), RULES)
    assert ok and how == "converter", how
    ok, how = GATE.match(
        _site("/api/pulse/live/${liveId}/guests/${guestId}/leave"), RULES)
    assert ok and how == "converter", how


def test_a_trailing_query_suffix_is_only_stripped_when_the_source_earns_it():
    """The widening is per call site, not per position.

    Every trailing interpolation in this codebase today is a query suffix built
    as ``cond ? `?k=v` : ""``, and it has to come off before matching because a
    query string is not part of a Flask rule. But granting that to every
    trailing placeholder would match `/api/pages/${id}/links/${x}` against
    `/api/pages/<int:id>/links` -- dropping a required segment and calling a
    genuine miss a pass.
    """
    earned = 'const suffix = type ? `?type=${type}` : "";'
    ok, how = GATE.match(
        _site("/api/pages/${pageId}/links${suffix}", src=earned), RULES)
    assert ok and how == "query-suffix", how

    unearned = "const extra = String(x);"
    ok, _ = GATE.match(
        _site("/api/pages/${pageId}/links${extra}", src=unearned), RULES)
    assert not ok, (
        "a trailing placeholder that the source does not show to be a query "
        "suffix was dropped anyway, turning a missing sub-route into a pass")


def test_the_trailing_check_reads_the_last_placeholder_not_the_first():
    """Most paths interpolate an id *and* a suffix.

    Testing the first placeholder for trailing-ness declines to apply the rule
    to every one of them -- which read as four false alarms until it was found.
    """
    earned = 'const suffix = type ? `?type=${type}` : "";'
    ok, _ = GATE.match(
        _site("/api/pages/${pageId}/links${suffix}", src=earned), RULES)
    assert ok, "the suffix rule was skipped because an id came first"


def test_an_int_converter_does_not_accept_a_literal_string():
    """`<int:>` is typed, and the type is the whole point of matching strictly.

    If every converter accepted anything, the probe path below would match
    `/api/pages/<int:page_id>/links` and the gate would confirm a contract the
    server would 404.
    """
    ok, _ = GATE.match(_site("/api/pages/settings/links"), RULES)
    assert not ok, (
        "a literal segment matched an <int:> converter; the gate would pass a "
        "call the server rejects")


def test_a_path_converter_matches_across_slashes_and_others_do_not():
    """`<path:>` is the only converter that may span a slash.

    Both halves are needed, and for a while only one was. Asserting the
    negative against `<int:post_id>` tests the *int* branch, so widening the
    generic branch to `.+` -- the branch that covers `<connection_id>`,
    `<action>`, `<ref>` and most of this app's converters -- went undetected.
    The negative case therefore has to name a generic converter.
    """
    ok, _ = GATE.match(_site("/api/media/${a}/${b}/${c}"), RULES)
    assert ok, "<path:> has to accept embedded slashes"

    ok, _ = GATE.match(_site("/api/pulse/posts/${a}/${b}/likes"), RULES)
    assert not ok, (
        "an <int:> converter accepted two segments; every multi-segment "
        "mismatch would read as a match")

    ok, _ = GATE.match(
        _site("/api/business-os/suppliers/cj/connections/${a}/${b}/health"),
        RULES)
    assert not ok, (
        "a generic converter accepted two segments; a client calling one level "
        "deeper than any route goes would read as a match")


# --- the gate has to be runnable the way CI will run it -----------------------

def test_the_gate_reports_distinct_exit_codes():
    assert GATE.EXIT_OK == 0
    assert GATE.EXIT_CONTRACT_BREAK == 1
    assert GATE.EXIT_NO_DATA == 3
    assert len({GATE.EXIT_OK, GATE.EXIT_CONTRACT_BREAK, GATE.EXIT_NO_DATA}) == 3


def test_the_gate_and_its_allowlist_exist_where_the_docs_say():
    assert GATE_PATH.exists(), GATE_PATH
    assert ALLOWLIST_PATH.exists(), ALLOWLIST_PATH


def test_every_configured_client_directory_exists():
    """A renamed client directory must fail loudly rather than shrink the scope."""
    for name, rel, _glob in GATE.CLIENTS:
        assert (REPO / rel).is_dir(), (
            f"client {name!r} points at {rel!r}, which does not exist; the "
            f"gate would silently stop checking it")


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
