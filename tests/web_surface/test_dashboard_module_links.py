"""`/pulse/dashboard/module/:groupKey/:moduleKey` — why it is a router, and what
would make that answer wrong.

The published app URL resolved nowhere on the web, which in the census looks
identical to a missing page. Reading `DashboardModuleDetailScreen` is what
settles it: the screen shows the module's card, names the *production route*,
and then tells the member in as many words that "its more advanced workflows are
available on pulsesoc.com". It is a hand-off surface. It exists because the app
cannot render every dashboard module natively.

On the web there is nothing to hand off to — the visitor is already there — so
the web equivalent of "open the production route" is *being* at the production
route, which is strictly richer than a card describing it. Hence a 302.

That reasoning is load-bearing, so the first half of this file pins the premise
and not just the outcome. If the screen ever grows real native-only content, the
redirect silently becomes the wrong answer and nothing else in the suite would
notice.

The second thing worth pinning is what the router deliberately does *not* do.
`mobile-native/src/data/dashboardModules.ts` looks like the module map's
authority because it is what the screen reads, but it is itself a mirror:
every module in it is in `services.pulse_dashboard_mission_control.WIDGETS` with
the same key, category and route. Transcribing 135 rows into `bot.py` would have
made a third copy, and the first one to drift would send members to the wrong
page. So the route resolves against the registry the server already owns, and
the tests below check both that the mirror still agrees and that no copy crept
into `bot.py`.

Every extractor asserts it matched before it compares. A source-reading test
that silently matches nothing reports success for a file it never read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NATIVE = os.path.join(REPO, "mobile-native", "src")
MODULES_TS = os.path.join(NATIVE, "data", "dashboardModules.ts")
DETAIL_TSX = os.path.join(NATIVE, "screens", "DashboardModuleDetailScreen.tsx")
LINKING_TS = os.path.join(NATIVE, "navigation", "linking.ts")

WEB_PREFIX = "/pulse/dashboard/module/"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --- what the app says -------------------------------------------------------


def native_modules() -> list[tuple[str, str, str]]:
    """Every `(groupKey, moduleKey, route)` the app publishes.

    Group and module entries are told apart by shape rather than by nesting,
    because a brace-matching parser over hand-formatted TypeScript is more
    likely to be wrong than this is. A group line is the one carrying `title:`
    right after `key:` at group indentation; a module is a single-line object
    literal with both a `key` and a `route`.

    The count is reconciled against the file's own `route: "` lines by
    `test_the_extractor_accounts_for_every_route_line`, which is what stops this
    from quietly shrinking to a subset and reporting agreement about it.
    """
    source = _read(MODULES_TS)
    assert "dashboardModuleGroups" in source, (
        "dashboardModules.ts no longer exports dashboardModuleGroups")
    modules: list[tuple[str, str, str]] = []
    group: str | None = None
    for line in source.splitlines():
        header = re.match(r'^\s{4}key: "([^"]+)", title:', line)
        if header:
            group = header.group(1)
            continue
        item = re.match(r'^\s*\{ key: "([^"]+)",.*?\broute: "([^"]+)"', line)
        if item:
            assert group, "a module literal appeared before any group header"
            modules.append((group, item.group(1), item.group(2)))
    assert modules, "found dashboardModuleGroups but no module literals inside it"
    return modules


def native_groups() -> list[str]:
    return sorted({group for group, _, _ in native_modules()})


def native_quick_action_routes() -> list[str]:
    """`dashboardQuickActions` — not modules, but they own `route:` lines too."""
    block = re.search(r"export const dashboardQuickActions[^=]*=\s*\[(.*?)\n\];",
                      _read(MODULES_TS), re.S)
    assert block, "dashboardQuickActions is no longer an array literal"
    routes = re.findall(r'\{ label: "[^"]+", route: "([^"]+)"', block.group(1))
    assert routes, "found dashboardQuickActions but none of its entries"
    return routes


def test_the_extractor_accounts_for_every_route_line():
    """135 modules + 12 quick actions = every `route:` in the file.

    Without this the module extractor could match a subset and every drift check
    below would pass by only ever comparing the part it managed to read. The
    same failure has already happened once in this suite — a `PANELS_BY_MODE`
    extractor that matched array values dropped a whole mode written as a named
    constant, and the test went green about it.
    """
    total = len(re.findall(r'route: "', _read(MODULES_TS)))
    accounted = len(native_modules()) + len(native_quick_action_routes())
    assert accounted == total, (
        f"{total} route lines in dashboardModules.ts but the extractors claim "
        f"{len(native_modules())} modules + {len(native_quick_action_routes())} "
        "quick actions; something in the file is not being read")
    assert len(native_groups()) == 11, (
        f"expected 11 module groups, parsed {len(native_groups())}: "
        f"{native_groups()}")
    assert len(native_modules()) >= 100, (
        "the dashboard has always had well over a hundred modules; parsing "
        f"only {len(native_modules())} means the extractor lost most of the file")


# --- the premise: this screen is a hand-off, not a destination ----------------


def test_the_native_screen_still_defers_its_real_work_to_the_web():
    """The sentence that makes a redirect the honest answer.

    If this screen ever gains workflows that only exist natively, sending a web
    visitor to the production route would drop whatever those are. The redirect
    is correct *because* the screen says the opposite: the deep surface is on
    the web and this card is the summary.
    """
    source = _read(DETAIL_TSX)
    assert "pulsesoc.com" in source, (
        "DashboardModuleDetailScreen no longer points members at pulsesoc.com — "
        "re-read the screen before trusting the redirect at "
        "/pulse/dashboard/module/<group>/<module>, because it may now render "
        "something the web route does not")
    assert re.search(r"more advanced workflows are available on pulsesoc\.com",
                     source), (
        "the hand-off wording changed; confirm the screen is still a summary "
        "that defers to the web rather than a surface in its own right")


def test_the_native_screen_names_the_production_route_as_the_destination():
    """"Production route" on that card is `module.route`, and the router sends
    people to exactly that. If the screen started deriving a different
    destination, the app and the browser would disagree about where one link
    goes."""
    source = _read(DETAIL_TSX)
    assert "Production route" in source, (
        "the 'Module route parity' block no longer labels a production route")
    assert re.search(r"module\.route", source), (
        "DashboardModuleDetailScreen no longer reads module.route, so the web "
        "router has nothing to agree with")


def test_the_native_screen_refuses_an_unknown_module_rather_than_falling_back():
    """The contrast with `/pulse/seller-store`, and the reason the two routers
    behave differently.

    Seller Store falls back to the dashboard for an unknown `mode` because the
    app does — an unrecognised mode still yields a working store. This screen
    does the opposite: it refuses, by name. So the web route 404s instead of
    redirecting, and this is the evidence for that choice rather than a taste
    call."""
    source = _read(DETAIL_TSX)
    assert "could not be matched to the production module map" in source, (
        "DashboardModuleDetailScreen no longer refuses unknown keys; if it now "
        "falls back to the dashboard, the web route should stop 404ing")


def test_the_app_publishes_this_path_the_way_the_web_serves_it():
    """The path in `linking.ts` is the URL the app puts on a clipboard. The web
    rule has to have the same shape or the census's BROKEN row was never really
    closed."""
    found = re.search(
        r'DashboardModuleDetail:\s*(?:"([^"]+)"|\{\s*path:\s*"([^"]+)")',
        _read(LINKING_TS))
    assert found, "DashboardModuleDetail is no longer published in linking.ts"
    path = found.group(1) or found.group(2)
    assert path.startswith("pulse/dashboard/module/") or path.startswith(
        "/pulse/dashboard/module/"), (
        f"the app now publishes {path!r}; the web rule serves {WEB_PREFIX}")


# --- what the server does ----------------------------------------------------

_PROBE = r"""
import inspect, json, sys
sys.path.insert(0, %(repo)r)
import bot
from services import pulse_dashboard_mission_control as mission_control

rows = mission_control.registry_rows()
report = {
    "registry": [[str(r.get("category") or ""), str(r.get("widget_key") or ""),
                  str(r.get("route") or "")] for r in rows],
    # Read back rather than described: the claim "bot.py holds no copy of the
    # module map" is only worth making against the actual bytes of the route.
    "router_source": (inspect.getsource(bot._pulse_dashboard_group_slug)
                      + inspect.getsource(bot.pulse_dashboard_module_link)),
}

app = bot.webhook_app
app.config["SECRET_KEY"] = "dashboard-module-links-test"
# Deliberately signed out. The route resolves an app URL to a web one and each
# destination asks for a sign-in in its own words; a sign-in wall on the router
# itself would mean a shared link died before it named where it was going.
client = app.test_client()

report["gets"] = {}
for path in %(paths)r:
    response = client.get(path)
    report["gets"][path] = [response.status_code,
                            response.headers.get("Location", "")]

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


def _probe_paths() -> list[str]:
    paths = [WEB_PREFIX + group + "/" + key for group, key, _ in native_modules()]
    paths += [
        # Case is not part of the key. A link that came back through an
        # uppercasing mail client must still land.
        WEB_PREFIX + "Account-Command-Center/profile",
        # Three ways to be wrong, all of which must refuse rather than guess.
        WEB_PREFIX + "not-a-group/profile",
        WEB_PREFIX + "account-command-center/not-a-module",
        # The census resolves parameterised links with a dummy value; this is
        # the exact string it uses, and it must not be answered with a redirect
        # or the deep-link report would call the route healthy for the wrong
        # reason.
        WEB_PREFIX + "x/x",
    ]
    return paths


def _run_probe(code: str, prefix: str) -> dict:
    workdir = tempfile.mkdtemp(prefix=prefix)
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "web.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def probe():
    return _run_probe(_PROBE % {"repo": REPO, "paths": _probe_paths()},
                      "web-dashboard-module-")


def _slug(value: str) -> str:
    """The server's own category slug, recomputed here rather than imported.

    Importing `bot._pulse_dashboard_group_slug` would make this test agree with
    the router by construction: a slug function that lost its lowercasing would
    still match itself. Spelling it out independently means the two have to
    agree on purpose.
    """
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


# --- the mirror still agrees -------------------------------------------------


def test_every_native_module_exists_in_the_server_registry(probe):
    """The claim the whole design rests on.

    `dashboardModules.ts` is a mirror of `pulse_dashboard_mission_control`. If it
    ever stops being one, resolving against the registry starts 404ing links the
    app is still handing out, and the failure is invisible from either side
    alone — which is why it is checked from here, where both are in scope.
    """
    registry = {(_slug(category), key) for category, key, _ in probe["registry"]}
    missing = sorted({(group, key) for group, key, _ in native_modules()}
                     - registry)
    assert not missing, (
        f"{len(missing)} modules the app publishes are not in "
        f"services/pulse_dashboard_mission_control.py: {missing[:10]}")


def test_the_two_copies_of_the_module_map_agree_about_every_route(probe):
    """A key that exists in both but points somewhere else is worse than one
    that is missing: the link works, lands on a real page, and it is the wrong
    page. Nothing 404s and nobody reports it."""
    registry = {(_slug(category), key): route
                for category, key, route in probe["registry"]}
    disagreements = [
        (group, key, native_route, registry[(group, key)])
        for group, key, native_route in native_modules()
        if (group, key) in registry and registry[(group, key)] != native_route]
    assert not disagreements, (
        "the app and the server disagree about where these modules live: "
        + json.dumps(disagreements[:10], indent=2))


def test_no_registry_row_can_send_a_visitor_off_site(probe):
    """The router redirects to whatever the registry spells. A row holding an
    absolute URL would turn a PulseSoc link into an open redirect, so the route
    skips anything that is not a local path — and this fails if a row ever
    arrives in that shape, because skipping it silently would look like a
    missing module."""
    offsite = [[category, key, route] for category, key, route in probe["registry"]
               if route and not route.startswith("/")]
    assert not offsite, (
        "registry rows with non-local routes: " + json.dumps(offsite))


# --- the outcome -------------------------------------------------------------


def test_every_module_the_app_publishes_resolves_on_the_web(probe):
    """135 published app URLs, each of which a member can put on a clipboard."""
    failures = []
    for group, key, route in native_modules():
        status, location = probe["gets"][WEB_PREFIX + group + "/" + key]
        if status != 302 or location != route:
            failures.append([group, key, route, status, location])
    assert not failures, (
        f"{len(failures)} dashboard module links do not reach the app's own "
        "route: " + json.dumps(failures[:10], indent=2))


def test_a_module_whose_destination_is_not_derivable_is_routed_anyway(probe):
    """Asserted by hand because it is the case a rule could not have guessed.

    `audience_analytics` lives at `/dashboard/creator/audience-intelligence` —
    neither the group slug nor the module key appears in the destination. Any
    implementation that "derives" a URL from the key instead of reading the
    registry passes every other test in this file and fails this one.
    """
    status, location = probe["gets"][WEB_PREFIX + "creator-studio/audience_analytics"]
    assert status == 302
    assert location == "/dashboard/creator/audience-intelligence"


def test_the_group_key_is_matched_without_regard_to_case(probe):
    status, location = probe["gets"][WEB_PREFIX + "Account-Command-Center/profile"]
    assert status == 302
    assert location == "/dashboard/account/profile"


@pytest.mark.parametrize("suffix", ["not-a-group/profile",
                                    "account-command-center/not-a-module",
                                    "x/x"])
def test_an_unrecognised_module_is_refused_rather_than_redirected(probe, suffix):
    """404, and specifically *not* a redirect to the dashboard.

    The app answers "This dashboard card could not be matched to the production
    module map." Redirecting anyway would claim the link worked when the app
    would have told the member it did not, and it would also hide a real
    regression: if the registry lookup broke entirely, a fallback would make
    every one of the 135 links land on the dashboard while still returning 302.
    """
    status, location = probe["gets"][WEB_PREFIX + suffix]
    assert status == 404, (
        f"{WEB_PREFIX + suffix} answered {status} -> {location!r}; an unknown "
        "key must be refused, not guessed at")


def test_the_router_carries_no_copy_of_the_module_map(probe):
    """The invariant that keeps the two existing copies from becoming three.

    A transcription in `bot.py` would pass every behavioural test above on the
    day it was written and start sending members to stale pages the first time a
    module moved. So the route's own source is required to contain neither
    module keys nor destination routes, and to reach the registry instead.
    """
    source = probe["router_source"]
    assert "registry_rows" in source, (
        "the route no longer resolves against pulse_dashboard_mission_control; "
        "if it now holds its own map, that is a third copy of the module table")
    leaked = sorted({key for _, key, _ in native_modules() if '"%s"' % key in source
                     or "'%s'" % key in source})
    assert not leaked, (
        "module keys are spelled out inside the route: " + ", ".join(leaked[:10]))
    routes = sorted({route for _, _, route in native_modules() if route in source})
    assert not routes, (
        "destination routes are spelled out inside the route: "
        + ", ".join(routes[:10]))
