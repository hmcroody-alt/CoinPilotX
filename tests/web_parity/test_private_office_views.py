"""`/pulse/private-office/:view` is not a gap, and this is why.

The deep-link census could only mark this row `unproven`. Its evidence comes
from concrete paths spelled out in the app's source, and this parameter's values
are not paths — they are a vocabulary, `RECORD_VIEWS` in
`mobile-native/src/api/privateRecords.ts`, a `as const` array that
`asRecordView()` rejects anything outside of. A path scraper structurally cannot
see it, so the row sat in the bucket that says "a human still has to look".

A human looked, and the answer is good: the web rule is
`/pulse/private-office/<any(...):view>` whose enumeration matches the native
vocabulary exactly, member for member. But "a human looked once" is the kind of
evidence the whole census exists to replace, because it stops being true the
moment either list moves and nothing says so. So the two authorities are read
out of their own sources and compared here instead.

Comparing them is what makes this sound rather than circular. Probing the values
the *web* enumerates would always pass; probing the values the *app* declares is
the question that matters, because those are the URLs it can put on a clipboard.

A live discrepancy this pins, deliberately not "fixed": the backend's
`services/private_office/retrieval.py` RECORD_VIEWS has eight entries — the six
above plus `tasks` and `projects`. `/api/private-office/records/tasks` is served;
`/pulse/private-office/tasks` 404s, and the native app has no such view either.
Web and native agree, so this is not a web↔native parity gap and adding the two
views to the web alone would create exactly the web-only surface the mission
forbids. It is recorded here because the native vocabulary claims in a comment
to mirror the backend's and does not, and because a ninth backend view should
make someone decide rather than pass unnoticed.
"""

import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NATIVE_VIEWS_TS = os.path.join(REPO, "mobile-native", "src", "api",
                               "privateRecords.ts")
BACKEND_RETRIEVAL = os.path.join(REPO, "services", "private_office",
                                 "retrieval.py")

#: The six the two clients share. Named so a change has to be deliberate.
EXPECTED_CLIENT_VIEWS = {"obligations", "events", "decisions", "requests",
                         "risks", "opportunities"}
#: Server-side extras neither client surfaces. See the module docstring.
EXPECTED_BACKEND_ONLY_VIEWS = {"tasks", "projects"}

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot

adapter = bot.webhook_app.url_map.bind("pulsesoc.com")
report = {}

# Which rule, not merely whether it resolves: a value swallowed by some
# unrelated catch-all would look identical to one the view route serves.
landings = {}
for view in %(views)r:
    path = "/pulse/private-office/" + view
    try:
        rule, _args = adapter.match(path, method="GET", return_rule=True)
        landings[view] = rule.rule
    except Exception as exc:
        landings[view] = "404:" + type(exc).__name__
report["landings"] = landings

rules = [r.rule for r in bot.webhook_app.url_map.iter_rules()
         if r.rule.startswith("/pulse/private-office/<")]
report["view_rules"] = sorted(rules)

# The named feature screens are separate registered deep links in the app, so
# they are siblings of `:view`, not values of it. They still have to resolve.
siblings = {}
for path in %(siblings)r:
    try:
        rule, _args = adapter.match(path, method="GET", return_rule=True)
        siblings[path] = rule.rule
    except Exception as exc:
        siblings[path] = "404:" + type(exc).__name__
report["siblings"] = siblings

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


def _read(path: str) -> str:
    assert os.path.exists(path), path + " is gone"
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def native_record_views() -> list[str]:
    """The vocabulary the app validates `:view` against."""
    match = re.search(r"export const RECORD_VIEWS = \[(.*?)\] as const;",
                      _read(NATIVE_VIEWS_TS), re.S)
    assert match, (
        "RECORD_VIEWS is no longer declared as an `as const` array in "
        "privateRecords.ts; this test can no longer read the app's vocabulary "
        "and would silently compare an empty set")
    views = re.findall(r'"([a-z_]+)"', match.group(1))
    assert views, "RECORD_VIEWS parsed but contained no entries"
    return views


def backend_record_views() -> set[str]:
    """The server's vocabulary, read from its `VIEW_X = "x"` constants."""
    source = _read(BACKEND_RETRIEVAL)
    block = re.search(r"RECORD_VIEWS: dict\[str, str\] = \{(.*?)\}", source, re.S)
    assert block, "RECORD_VIEWS is no longer a dict literal in retrieval.py"
    names = re.findall(r"(VIEW_[A-Z]+):", block.group(1))
    assert names, "RECORD_VIEWS parsed but named no VIEW_ constants"
    values = set()
    for name in names:
        assign = re.search(r'^%s = "([a-z_]+)"$' % name, source, re.M)
        assert assign, "%s is used in RECORD_VIEWS but never assigned" % name
        values.add(assign.group(1))
    return values


LINKING_TS = os.path.join(REPO, "mobile-native", "src", "navigation",
                          "linking.ts")


def native_private_office_siblings() -> list[str]:
    """The named private-office screens the app registers as their own links."""
    found = re.findall(r'"(pulse/private-office/[a-z-]+)"', _read(LINKING_TS))
    assert len(found) >= 7, (
        "only %d named private-office deep links found in linking.ts; the "
        "extractor has stopped matching and this check would go vacuous"
        % len(found))
    return sorted("/" + path for path in set(found))


@pytest.fixture(scope="module")
def probe():
    code = _PROBE % {"repo": REPO, "views": native_record_views(),
                     "siblings": native_private_office_siblings()}
    workdir = tempfile.mkdtemp(prefix="private-office-views-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "views.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


# --- the parity claim --------------------------------------------------------


def test_every_view_the_app_can_link_to_is_served_by_the_web():
    """The claim that closes the row. Asserted per view so a failure names the
    one that broke rather than reporting a set difference."""
    assert set(native_record_views()) == EXPECTED_CLIENT_VIEWS, (
        "the app's record views changed; decide whether the web's `any(...)` "
        "enumeration and this expectation should follow before editing them")


def test_each_view_lands_on_the_private_office_view_route(probe):
    for view in native_record_views():
        landing = probe["landings"][view]
        assert not landing.startswith("404:"), (
            "/pulse/private-office/%s 404s, so sharing that view from the app "
            "is broken for anyone without it installed" % view)
        assert landing.startswith("/pulse/private-office/<"), (
            "/pulse/private-office/%s resolves, but to %r — some unrelated "
            "catch-all, not the view route, which would render the wrong page "
            "while looking healthy to a status-code check" % (view, landing))


def test_the_web_enumerates_exactly_the_views_the_app_declares(probe):
    """Set equality, not containment. A web route accepting values the app
    cannot produce is how a web-only surface starts, and the mission forbids
    the web growing product the native app does not have."""
    # Two enumerations share this base: `:section` claims the named feature
    # screens and `:view` claims the record views. Selecting by parameter name
    # rather than by position, because "the only one" was already wrong once.
    rules = [r for r in probe["view_rules"] if ":view>" in r]
    assert len(rules) == 1, (
        "expected exactly one /pulse/private-office/<...:view> rule, found %r "
        "out of %r; two would make it ambiguous which serves a share link"
        % (rules, probe["view_rules"]))
    enumerated = re.search(r"<any\(([^)]*)\)", rules[0])
    assert enumerated, (
        "the view route is no longer an `any(...)` enumeration but %r. If it "
        "became a bare wildcard the web now accepts views the app never "
        "produces, and this comparison can no longer be made" % rules[0])
    web_views = {v.strip() for v in enumerated.group(1).split(",") if v.strip()}
    assert web_views == set(native_record_views()), (
        "the web and the app disagree about which record views exist. "
        "web-only: %s; app-only: %s"
        % (sorted(web_views - set(native_record_views())),
           sorted(set(native_record_views()) - web_views)))


def test_the_named_private_office_screens_resolve_too(probe):
    """`:view`'s siblings. The web splits them across a second `any(...)`
    enumeration that does not list every one of them — `security` is absent
    from it — so each is checked individually rather than trusting that
    enumeration to cover the set."""
    for path, landing in sorted(probe["siblings"].items()):
        assert not landing.startswith("404:"), (
            "%s is a registered deep link in the app and 404s on the web, so "
            "sharing that screen is broken" % path)


# --- the discrepancy that must not drift unnoticed ---------------------------


def test_the_backend_views_neither_client_surfaces_are_still_just_these_two():
    """`tasks` and `projects` are served by the API and shown by nobody. That
    is not a parity gap — web and native agree — but a ninth view appearing
    here should be a decision, not a surprise."""
    backend = backend_record_views()
    clients = set(native_record_views())
    assert clients <= backend, (
        "the app declares record views the server does not: %s"
        % sorted(clients - backend))
    assert backend - clients == EXPECTED_BACKEND_ONLY_VIEWS, (
        "the set of server-side record views no client surfaces changed to %s. "
        "Decide whether the app and the web should gain them together — adding "
        "them to the web alone would be a web-only surface."
        % sorted(backend - clients))


def test_the_native_vocabulary_still_claims_to_mirror_the_backend():
    """The comment says RECORD_VIEWS mirrors retrieval.py's. It does not, and
    that is the reason the two-views discrepancy went unnoticed. If someone
    drops the claim the guard above loses its rationale and should be revisited
    rather than left asserting a coincidence."""
    source = _read(NATIVE_VIEWS_TS)
    assert "retrieval.py" in source, (
        "privateRecords.ts no longer references services/private_office/"
        "retrieval.py. If the mirror claim was withdrawn deliberately, "
        "test_the_backend_views_neither_client_surfaces_are_still_just_these_"
        "two is now pinning a coincidence and should be re-justified.")
