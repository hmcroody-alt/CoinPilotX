"""The dashboard row the deep-link census structurally cannot decide.

`/dashboard/:legacyGroup/:legacyModule/:legacySubmodule?` is a registered
universal link, so every URL matching it is one the app can put on a clipboard.
The census classifies such a row by sampling one segment, declines multi-parameter
patterns rather than guessing, and leaves it `unproven` — the honest answer from
a sampler, and a permanent one.

The values are derivable, just not by scraping paths. Native resolves one of
these URLs through `findLegacyDashboardAlias`: the first segment is looked up in
`DASHBOARD_LEGACY_GROUPS`, and the rest is matched against aliases of the modules
in that group, one of which is always `slugify(module.key)`. So the set of URLs
the app will resolve is the product of two lists in its own source, and that is
what is enumerated and probed here.

Doing it found a gap the path scraper had only glimpsed. Native has eleven
dashboard groups; the web serves nine. `media` and `safety` have no web surface
at all — not the twelve and five module URLs, and not the group hubs either —
while every other group answers on a `<subsystem_key>` route. `/dashboard/media/
video-library` is not obscure: it is a command item on the native Home screen,
labelled "Videos".

Two things make this a build rather than a decision. Every module's real
destination already exists on the web — `/pulse/music`, `/pulse/videos`,
`/pulse/saved`, `/support`, `/pulse/profile/security` all resolve — and the web's
own `services/pulse_dashboard_mission_control.py` already lists both groups'
modules with the same keys, titles and routes as the app. So closing this needs
no new backend authority, which is the thing the mission forbids.

The expected-broken set below is a ratchet in the same spirit as
`BROKEN_DEEP_LINK_BUDGET`: it is asserted by equality, so the suite fails both
when a new alias breaks and when one is fixed without being removed from the
list.
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
ROUTING_TS = os.path.join(REPO, "mobile-native", "src", "navigation",
                          "dashboardRouting.ts")
MODULES_TS = os.path.join(REPO, "mobile-native", "src", "data",
                          "dashboardModules.ts")
WEB_REGISTRY = os.path.join(REPO, "services", "pulse_dashboard_mission_control.py")

#: The two groups the web has no surface for, and the alias URLs that 404
#: because of it. Delete entries as they are built; the test fails if one is
#: closed and left here, so the list cannot rot into a description of the past.
EXPECTED_BROKEN_GROUPS = {"media", "safety"}


def _read(path: str) -> str:
    assert os.path.exists(path), path + " is gone"
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def slugify(value: str) -> str:
    """`slugify` from dashboardRouting.ts, which mints the alias every module
    answers to."""
    value = re.sub(r"&", "and", value.strip().lower())
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", value))


def legacy_groups() -> dict:
    """URL segment -> module-group key, from the app's own map."""
    block = re.search(
        r"DASHBOARD_LEGACY_GROUPS: Record<string, string> = \{(.*?)\};",
        _read(ROUTING_TS), re.S)
    assert block, (
        "DASHBOARD_LEGACY_GROUPS is no longer a literal object in "
        "dashboardRouting.ts; this test can no longer read which URL segments "
        "the app resolves and would compare an empty set")
    groups = dict(re.findall(r'(\w+):\s*"([a-z0-9-]+)"', block.group(1)))
    assert len(groups) >= 10, (
        "only %d legacy dashboard groups parsed; the extractor has stopped "
        "matching" % len(groups))
    return groups


def module_keys_by_group() -> dict:
    """Group key -> module keys, from the registry the app renders from."""
    groups, current = {}, None
    for line in _read(MODULES_TS).splitlines():
        header = re.match(r'\s{4}key: "([a-z0-9-]+)", title:', line)
        if header:
            current = header.group(1)
            groups[current] = []
            continue
        item = re.match(r'\s*\{ key: "([a-z0-9_]+)", title: "', line)
        if item and current:
            groups[current].append(item.group(1))
    assert len(groups) >= 10, (
        "only %d module groups parsed out of dashboardModules.ts" % len(groups))
    empty = sorted(k for k, v in groups.items() if not v)
    assert not empty, (
        "these module groups parsed with no modules, so every URL they own "
        "would silently drop out of this check: %s" % empty)
    return groups


def native_alias_urls() -> dict:
    """Every `/dashboard/<group>/<module>` URL the native router resolves."""
    groups = legacy_groups()
    by_group = module_keys_by_group()
    urls = {}
    for segment, group_key in sorted(groups.items()):
        for key in by_group.get(group_key, []):
            urls["/dashboard/%s/%s" % (segment, slugify(key))] = segment
    assert len(urls) >= 120, (
        "only %d alias URLs derived; the two sources stopped lining up and "
        "this check would pass on a fraction of the surface" % len(urls))
    return urls


_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot

adapter = bot.webhook_app.url_map.bind("pulsesoc.com")

def landing(path):
    try:
        rule, _args = adapter.match(path, method="GET", return_rule=True)
        return rule.rule
    except Exception as exc:
        return "404:" + type(exc).__name__

report = {"aliases": {p: landing(p) for p in %(urls)r},
          "hubs": {p: landing(p) for p in %(hubs)r},
          "destinations": {p: landing(p) for p in %(dests)r}}
sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

#: Where the media and safety modules actually send a member. If these ever stop
#: resolving, building the group pages would point at 404s.
MODULE_DESTINATIONS = ("/pulse/music", "/pulse/videos", "/pulse/saved",
                       "/support", "/pulse/profile/security",
                       "/pulse/creator/analytics", "/pulse/live")


@pytest.fixture(scope="module")
def probe():
    urls = sorted(native_alias_urls())
    hubs = sorted("/dashboard/" + segment for segment in legacy_groups())
    code = _PROBE % {"repo": REPO, "urls": urls, "hubs": hubs,
                     "dests": list(MODULE_DESTINATIONS)}
    workdir = tempfile.mkdtemp(prefix="dashboard-aliases-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "aliases.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


def _broken(probe) -> dict:
    return {p: v for p, v in probe["aliases"].items() if v.startswith("404:")}


# --- the gap, stated as a set that may only shrink ---------------------------


def test_the_broken_aliases_are_confined_to_the_groups_with_no_web_surface(probe):
    """Asserted by group rather than by listing seventeen URLs, because the
    failure is not seventeen independent bugs — it is two groups that were never
    built, and a module added to either would otherwise look like a new
    regression."""
    groups = {probe_group for path, probe_group
              in native_alias_urls().items() if path in _broken(probe)}
    assert groups == EXPECTED_BROKEN_GROUPS, (
        "the set of dashboard groups with no web surface changed to %s "
        "(expected %s). If a group was closed, remove it from "
        "EXPECTED_BROKEN_GROUPS; if one broke, a whole native dashboard group "
        "now 404s for anyone following a shared link."
        % (sorted(groups), sorted(EXPECTED_BROKEN_GROUPS)))


def test_every_alias_outside_those_groups_resolves(probe):
    """The nine that work, held in place. These are the reason the two are
    identifiable as a gap rather than as a design choice."""
    broken = _broken(probe)
    unexpected = sorted(
        path for path, segment in native_alias_urls().items()
        if path in broken and segment not in EXPECTED_BROKEN_GROUPS)
    assert not unexpected, (
        "these dashboard deep links resolve natively and 404 on the web, in "
        "groups that are otherwise served: %s" % unexpected)


def test_the_hubs_for_those_groups_are_missing_too(probe):
    """Scope. The gap is not "some module URLs lack detail pages" — the group
    landing pages are absent as well, so there is nowhere on the web to send
    someone even for browsing."""
    for segment in sorted(EXPECTED_BROKEN_GROUPS):
        assert probe["hubs"]["/dashboard/" + segment].startswith("404:"), (
            "/dashboard/%s now resolves while its module URLs still 404. That "
            "is a worse shape than the gap: a hub that cannot link anywhere. "
            "Close the module URLs in the same change." % segment)


def test_the_other_hubs_all_resolve(probe):
    served = sorted(set(legacy_groups()) - EXPECTED_BROKEN_GROUPS)
    missing = [s for s in served if probe["hubs"]["/dashboard/" + s].startswith("404:")]
    assert not missing, (
        "these dashboard group hubs stopped resolving: %s" % missing)


# --- why this is a build and not a decision ----------------------------------


def test_the_destinations_those_modules_point_at_already_exist(probe):
    """Every module in both groups sends a member somewhere the web already
    serves. The missing piece is the dashboard surface, not the product behind
    it — which is what makes this closable without inventing anything."""
    missing = sorted(p for p, v in probe["destinations"].items()
                     if v.startswith("404:"))
    assert not missing, (
        "these are where the media and safety modules send people, and they no "
        "longer resolve: %s. Building the group pages would point at 404s."
        % missing)


def test_the_web_registry_already_carries_both_groups():
    """`services/pulse_dashboard_mission_control.py` already declares these
    modules with the same keys and routes as the app. Closing the gap therefore
    reads from an authority the web already has; adding a second one would be
    exactly the web-only backend authority the parity mission forbids."""
    source = _read(WEB_REGISTRY)
    for section in ("Pulse Radio & Media", "Moderation / Safety"):
        assert section in source, (
            "%r is no longer a section in the web's mission-control registry. "
            "If it was removed, the media/safety gap can no longer be closed "
            "by reading an existing authority, and the plan needs revisiting "
            "rather than the registry being re-added to suit it." % section)
    by_group = module_keys_by_group()
    for group_key, section in (("pulse-radio-media", "Pulse Radio & Media"),
                               ("moderation-safety", "Moderation / Safety")):
        # Not every native module has to exist on the web. The overlap is what
        # the page would render, so a shrinking overlap has to be visible rather
        # than quietly narrowing the page it feeds.
        shared = [k for k in by_group[group_key]
                  if '_widget("%s"' % k in source]
        assert len(shared) >= 5, (
            "only %d of the %d native %s modules are declared in the web "
            "registry; the shared vocabulary these pages would render has "
            "shrunk to almost nothing"
            % (len(shared), len(by_group[group_key]), section))
