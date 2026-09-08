"""`/pulse/intelligence/<subsystem>` — a universal link the app opened and the
web answered with 404.

`mobile-native/src/navigation/linking.ts` registers `pulse/intelligence/
:subsystem?`, so that URL is one the app can put on a member's clipboard. Until
this route existed the web had no rule for it at all.

The interesting part is not the 404, it is *who decides which subsystems exist*.
It is not the app. `notificationRouting.ts` obtains the value by scraping it out
of a `/dashboard/intelligence/<key>` link the **server** sent, and those keys
come from `services/dashboard_intelligence_command_center`. The app is a
consumer of that list, and when it gets a key it renders its hub plus "it does
not have its own screen in the app yet". So:

  known key    the web has a real page for it, and forwards there. Copying the
               app's "no screen for this" note onto a web that *does* have the
               screen would be faking a limitation, not mirroring a product.

  unknown key  the pulse hub, which is what the app shows and what the sibling
               `pulse_signal_stream_page` already does for its own key space.

Sending a `/pulse/` link into `/dashboard/` is the one choice here that looks
like a web invention, so it is pinned to the app's own source:
`notificationRouting.ts` handles both prefixes in a *single* branch and
navigates both to the one IntelligenceCenter screen. The app does not think
these are two places. If that branch is ever split in two, this file fails and
the redirect should be revisited.

Not every hub-only path in the census was a real gap. `/scam-shield/:mode?`
looked identical to this one and is not: the only mode any code anywhere mints
is `scan` (`nativeRouteActions.ts`), and `/scam-shield/scan` has always
resolved. That row was an artifact of probing with a made-up value. The
difference is tested here too, because the next person reading the census will
have to tell those two shapes apart again.

Every extractor asserts it matched before it compares.
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
LINKING_TS = os.path.join(NATIVE, "navigation", "linking.ts")
NOTIFICATION_ROUTING_TS = os.path.join(NATIVE, "navigation", "notificationRouting.ts")
ROUTE_ACTIONS_TS = os.path.join(NATIVE, "navigation", "nativeRouteActions.ts")
INTELLIGENCE_SCREEN_TSX = os.path.join(NATIVE, "screens", "IntelligenceCenterScreen.tsx")
TYPES_TS = os.path.join(NATIVE, "navigation", "types.ts")
ROUTES_PY = os.path.join(REPO, "pulse_communications_v2", "routes.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --- what the app says this URL means ----------------------------------------


def test_the_app_registers_the_url_this_route_exists_to_answer():
    """The whole premise. If the app stops claiming this path, the web route is
    answering a link nobody can send."""
    match = re.search(r'path: "pulse/intelligence/:subsystem\?"', _read(LINKING_TS))
    assert match, (
        "linking.ts no longer registers pulse/intelligence/:subsystem? — the "
        "web route was built because the app opened that URL and the web 404'd")


def test_the_app_reads_the_subsystem_and_shows_its_hub_rather_than_failing():
    """Why an unrecognised key redirects to the hub instead of 404ing: that is
    what a member holding the same link sees in the app."""
    source = _read(INTELLIGENCE_SCREEN_TSX)
    assert re.search(r"route\.params\?\.subsystem", source), (
        "IntelligenceCenterScreen no longer reads route.params.subsystem")
    assert "does not have its own screen in the app yet" in source, (
        "IntelligenceCenterScreen no longer degrades an unknown subsystem to "
        "its hub; re-read it before trusting the web fallback")


def test_the_app_treats_the_dashboard_and_pulse_prefixes_as_one_destination():
    """The load-bearing premise for forwarding a /pulse/ link into /dashboard/.

    This is the assertion that stops the redirect being a web-only opinion. Both
    prefixes are handled by one branch that navigates to one screen.
    """
    source = _read(NOTIFICATION_ROUTING_TS)
    match = re.search(
        r'if \(\(normalized\.startsWith\("/dashboard/intelligence"\)\s*\|\|\s*'
        r'normalized\.startsWith\("/pulse/intelligence"\)\)[^\n]*\)\s*\{'
        r'(.*?)\n  \}', source, re.S)
    assert match, (
        "notificationRouting.ts no longer handles /dashboard/intelligence and "
        "/pulse/intelligence in a single branch. The web forwards the pulse "
        "form into the dashboard form *because* the app considered them one "
        "destination — if they are now two, revisit that redirect")
    body = match.group(1)
    assert body.count("navigationRef.navigate(") == 1, (
        "the shared intelligence branch now navigates to more than one place")
    assert 'navigate("IntelligenceCenter"' in body, (
        "the shared intelligence branch no longer lands on IntelligenceCenter")


def test_the_subsystem_value_is_scraped_from_a_server_sent_dashboard_link():
    """Which registry is authoritative. The app does not invent these keys — it
    reads them out of a /dashboard/intelligence/<key> URL the server produced,
    so the server's registry is the right thing for the route to gate on."""
    source = _read(NOTIFICATION_ROUTING_TS)
    assert re.search(r"normalized\.match\(/\^\\/dashboard\\/intelligence\\/", source), (
        "notificationRouting.ts no longer derives the subsystem from a "
        "/dashboard/intelligence/<key> path, so dashboard_intelligence_"
        "command_center may no longer be the list the route should gate on")


# --- the row next door that looked the same and was not ----------------------


def test_scam_shield_mode_is_not_a_gap_because_only_one_mode_exists():
    """`/scam-shield/:mode?` sat in the same census bucket as this route and is
    not a gap: nothing mints a mode other than `scan`, which the web serves.

    Kept next to the real gap on purpose. Both rows were produced by probing a
    made-up path segment, and only one of them meant anything.
    """
    modes = re.findall(r'path === "/scam-shield/([^"]*)"', _read(ROUTE_ACTIONS_TS))
    assert modes, (
        "nativeRouteActions.ts no longer routes any /scam-shield/<mode> path; "
        "re-derive which modes exist before trusting this row")
    assert set(modes) == {"scan"}, (
        "the app now mints scam-shield modes beyond 'scan' (%s). The web only "
        "serves /scam-shield/scan, so this row may have become a real gap"
        % ", ".join(sorted(modes)))
    params = re.search(r"^  ScamShield: (.+);$", _read(TYPES_TS), re.M)
    assert params, "types.ts no longer declares ScamShield params"
    assert "mode" not in params.group(1), (
        "ScamShield now takes a mode param, so the app can navigate with one "
        "and the web's single /scam-shield/scan route may no longer be enough")


# --- what the server does ----------------------------------------------------

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot
from services import dashboard_intelligence_command_center as registry

app = bot.webhook_app
app.config["SECRET_KEY"] = "intelligence-subsystem-links-test"
report = {}

adapter = app.url_map.bind("pulsesoc.com")
def endpoint(path):
    try:
        return adapter.match(path, method="GET")[0]
    except Exception as exc:
        return "NO MATCH: " + type(exc).__name__

keys = sorted(registry.SUBSYSTEMS_BY_KEY)
report["registry_keys"] = keys

client = app.test_client()
def landing(path):
    response = client.get(path)
    return [response.status_code, response.headers.get("Location", "")]

# Every real key, not one dummy. The census bucket this route came out of was
# created by probing a single invented value, which is exactly how a row that
# was fine (scam-shield) and a row that was broken (this one) came to look the
# same.
report["every_key"] = {k: landing("/pulse/intelligence/" + k) for k in keys}
report["every_key_target_endpoint"] = {
    k: endpoint("/dashboard/intelligence/" + k) for k in keys}

report["odd_shapes"] = {p: landing(p) for p in %(odd)r}
report["hub_endpoint"] = endpoint("/pulse/intelligence")
report["alias_endpoint"] = endpoint("/pulse/intelligence/threat-intelligence")
report["signals_sibling"] = landing("/pulse/signals/nonsense")
report["scam_shield"] = {p: endpoint(p) for p in %(scam)r}

# The second hop of the two-hop story: an unknown key handed to the dashboard
# route still lands on a hub rather than a 404.
report["dashboard_unknown"] = landing("/dashboard/intelligence/definitely-not-real")

import inspect
from pulse_communications_v2 import routes as comm_routes
report["route_source"] = inspect.getsource(
    comm_routes.pulse_intelligence_subsystem_link)

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

ODD_SHAPES = ["/pulse/intelligence/Threat_Intelligence",
              "/pulse/intelligence/threat_intelligence",
              "/pulse/intelligence/THREAT-INTELLIGENCE",
              "/pulse/intelligence/  threat-intelligence  ",
              "/pulse/intelligence/bogus",
              "/pulse/intelligence/scam-shield/extra"]
SCAM_PATHS = ["/scam-shield", "/scam-shield/scan"]


def _run_probe() -> dict:
    code = _PROBE % {"repo": REPO, "odd": ODD_SHAPES, "scam": SCAM_PATHS}
    workdir = tempfile.mkdtemp(prefix="web-intelligence-subsystem-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "web.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def probe() -> dict:
    return _run_probe()


def test_the_registry_is_not_empty_so_the_key_tests_cannot_go_vacuous(probe):
    keys = probe["registry_keys"]
    assert len(keys) >= 10, (
        "dashboard_intelligence_command_center now exposes only %d subsystems; "
        "the per-key assertions below would be nearly vacuous" % len(keys))
    assert "ai-advisor" in keys, (
        "ai-advisor left the registry. It is the one key the dashboard route "
        "special-cases *before* the registry lookup, so if it is served but "
        "unlisted this route would send it to the hub instead of its page")


def test_every_known_subsystem_reaches_the_page_that_key_names(probe):
    wrong = {k: v for k, v in probe["every_key"].items()
             if v != [302, "/dashboard/intelligence/" + k]}
    assert not wrong, (
        "these subsystem links did not forward to their own page: "
        + json.dumps(wrong, indent=1))


def test_no_subsystem_link_forwards_to_a_page_that_does_not_exist(probe):
    """A redirect to a 404 is worse than the 404 it replaced: it looks handled.
    Resolved against the live url_map, so a rule that quietly changed shape
    shows up here rather than in production."""
    dead = {k: v for k, v in probe["every_key_target_endpoint"].items()
            if v != "dashboard_intelligence_subsystem_page"}
    assert not dead, (
        "these forward to a path no rule serves: " + json.dumps(dead, indent=1))


def test_an_unknown_subsystem_lands_on_the_hub_rather_than_404(probe):
    assert probe["odd_shapes"]["/pulse/intelligence/bogus"] == [302, "/pulse/intelligence"], (
        "an unrecognised subsystem no longer degrades to the pulse hub, which "
        "is what the app does with the same link")


def test_the_unknown_key_fallback_still_holds_on_the_far_side(probe):
    """The known-key redirect is only safe while the dashboard route keeps its
    own hub fallback. If it ever starts 404ing, a key that leaves the registry
    between the two hops would dead-end."""
    assert probe["dashboard_unknown"][0] in (301, 302), (
        "the dashboard subsystem route no longer redirects unknown keys")


def test_the_shapes_a_shared_link_actually_arrives_in(probe):
    """Case and underscores, because these keys travel through notification
    payloads and hand-edited URLs, and the dashboard route normalises them."""
    landings = probe["odd_shapes"]
    for path in ["/pulse/intelligence/Threat_Intelligence",
                 "/pulse/intelligence/threat_intelligence",
                 "/pulse/intelligence/THREAT-INTELLIGENCE",
                 "/pulse/intelligence/  threat-intelligence  "]:
        assert landings[path] == [302, "/dashboard/intelligence/threat-intelligence"], (
            # Asserting the exact target, not merely a 302: the unknown-key
            # fallback is also a 302, so a normalisation that silently stopped
            # working would still "pass" a status-code-only check while sending
            # the member to a hub instead of the page they asked for.
            "%s no longer normalises to the same subsystem page (got %r)"
            % (path, landings[path]))


def test_the_alias_did_not_shadow_the_hub_it_hangs_off(probe):
    """The obvious way this change could have broken something: swallowing
    `/pulse/intelligence` itself, or the deeper signals routes."""
    assert probe["hub_endpoint"] == "pulse_communications_v2.pulse_alerts_page", (
        "/pulse/intelligence no longer reaches its own hub; the subsystem rule "
        "has shadowed it (got %r)" % probe["hub_endpoint"])
    assert probe["alias_endpoint"] == (
        "pulse_communications_v2.pulse_intelligence_subsystem_link"), (
        "the subsystem alias is no longer the rule that serves this path")
    assert probe["odd_shapes"]["/pulse/intelligence/scam-shield/extra"][0] == 404, (
        "a two-segment path under /pulse/intelligence now resolves; the alias "
        "was meant to take exactly one segment")


def test_the_sibling_route_still_behaves_the_same_way(probe):
    """`pulse_signal_stream_page` is the in-repo precedent this route copies.
    Two sibling URLs disagreeing about unknown keys is the confusion worth
    catching."""
    assert probe["signals_sibling"] == [302, "/pulse/intelligence"], (
        "/pulse/signals/<unknown> no longer falls back to the pulse hub, so "
        "the two sibling routes now disagree about unknown keys")


def test_scam_shield_still_serves_the_only_mode_that_exists(probe):
    """The counterpart to the source-side check above: the mode the app mints
    resolves on the web. Together they are the reason that census row is not a
    gap, and neither half is enough alone."""
    assert probe["scam_shield"]["/scam-shield/scan"] == "scam_shield_scan_page", (
        "/scam-shield/scan no longer resolves — this *is* now a real gap, "
        "because it is the only scam-shield mode the app can produce")
    assert not probe["scam_shield"]["/scam-shield"].startswith("NO MATCH"), (
        "/scam-shield no longer resolves")


def test_the_route_carries_no_copy_of_the_subsystem_list(probe):
    """The registry is the authority. An implementation that spells the keys out
    passes every behavioural test above and silently rots the moment a subsystem
    is added or renamed."""
    source = probe["route_source"]
    assert "SUBSYSTEMS_BY_KEY" in source, (
        "the route no longer consults the registry; it must be deciding which "
        "subsystems exist some other way")
    leaked = sorted(k for k in probe["registry_keys"]
                    if '"%s"' % k in source or "'%s'" % k in source)
    assert not leaked, (
        "subsystem keys are spelled out inside the route: " + ", ".join(leaked))
