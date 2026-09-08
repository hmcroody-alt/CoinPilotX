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
dashboard groups; the web served nine. `media` and `safety` had no module URLs
at all — twelve and five of them — while every other group answered on a
`<subsystem_key>` route. `/dashboard/media/video-library` is not obscure: it is a
command item on the native Home screen, labelled "Videos".

It was closed as a router, not as a page, following the route immediately above
it in `bot.py`. That one exists because `DashboardModuleDetailScreen` is a native
meta surface that describes a module and hands off to the web — and on the web
there is nothing to hand off to, so being at the production route is the mirror.
The legacy alias shape lands on that same native screen, so it gets the same
answer, resolved through `mission_control.registry_rows()` rather than a second
copy of the module map.

Two things made it a build rather than a decision, and both are still asserted
below because both could stop being true. Every module's real destination
already existed on the web, and the web's own registry already listed both
groups' modules with the same keys and routes as the app. So closing this needed
no new backend authority, which is the thing the mission forbids.

What is *not* part of the gap: a group hub. `/dashboard/media` is not a URL the
app can produce — the universal link needs a module segment — and the browsing
surface it would duplicate already exists, because `templates/dashboard.html`
loops every category in the registry and has always rendered both sections.
Building one would have been a web-only page mirroring nothing.

These tests probe *responses*, not the routing table. The distinction matters
here in a way it did not before the fix: the new route takes a `<path:>`
converter, so a rule-level check would report all seventeen URLs as "resolving"
no matter where they led — including nowhere. What is asserted instead is that
each one redirects to the module's own registry route.
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
DASHBOARD_TEMPLATE = os.path.join(REPO, "templates", "dashboard.html")
BOT_PY = os.path.join(REPO, "bot.py")

#: The two legacy group segments the web serves by redirecting to the module's
#: production route, because they are the two with no pages of their own. Held
#: by equality: a tenth group losing its pages and quietly falling back to a
#: redirect would be a downgrade, and has to be seen rather than absorbed.
ROUTED_GROUPS = {"media", "safety"}

#: The registry categories those segments name. Native spells the short name and
#: the server spells the title, so one side has to map to the other.
ROUTED_CATEGORIES = {"media": "Pulse Radio & Media",
                     "safety": "Moderation / Safety"}


def _read(path: str) -> str:
    assert os.path.exists(path), path + " is gone"
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def slugify(value: str) -> str:
    """`slugify` from dashboardRouting.ts, which mints the alias every module
    answers to."""
    value = re.sub(r"&", "and", value.strip().lower())
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", value))


def group_slug(value: str) -> str:
    """`_pulse_dashboard_group_slug` from bot.py — deliberately not `slugify`.

    Group keys in `dashboardModules.ts` are hand-written and drop the "&": the
    "Pulse Radio & Media" group is keyed `pulse-radio-media`, where `slugify`
    would give `pulse-radio-and-media`. Using the module slugifier to bridge a
    category title to a group key fails on exactly the three groups whose titles
    contain an ampersand, which is how this distinction was found.
    """
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", value.lower()))


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


#: Where the media and safety modules actually send a member. If these ever stop
#: resolving, the router points at 404s.
MODULE_DESTINATIONS = ("/pulse/music", "/pulse/videos", "/pulse/saved",
                       "/support", "/pulse/profile/security",
                       "/pulse/creator/analytics", "/pulse/live")

#: Aliases native accepts that are not `slugify(key)`, so the enumerated set
#: above does not cover them. `videos` is the last segment of `/pulse/videos`;
#: `pulse/videos` is the last two. Serving only the enumerable subset would have
#: left links the app resolves 404ing while the census said the row was closed.
EXTRA_NATIVE_ALIASES = {"/dashboard/media/videos": "/pulse/videos",
                        "/dashboard/media/pulse/videos": "/pulse/videos"}

#: An alias no module answers to. Native tells the member the card could not be
#: matched; a redirect to /dashboard would claim the link worked.
UNMATCHED_ALIAS = "/dashboard/media/not-a-module"

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot
from services import pulse_dashboard_mission_control as mission_control

client = bot.webhook_app.test_client()
adapter = bot.webhook_app.url_map.bind("pulsesoc.com")

def hit(path):
    response = client.get(path)
    try:
        rule, _args = adapter.match(path, method="GET", return_rule=True)
        endpoint = rule.endpoint
    except Exception as exc:
        endpoint = "404:" + type(exc).__name__
    return [response.status_code, response.headers.get("Location"), endpoint]

paths = %(urls)r + %(extra)r + %(dests)r + [%(unmatched)r]
report = {"hits": {p: hit(p) for p in paths},
          "registry": [[r.get("category"), r.get("widget_key"),
                        r.get("display_name"), r.get("route")]
                       for r in mission_control.registry_rows()]}
sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def probe():
    code = _PROBE % {"repo": REPO,
                     "urls": sorted(native_alias_urls()),
                     "extra": sorted(EXTRA_NATIVE_ALIASES),
                     "dests": list(MODULE_DESTINATIONS),
                     "unmatched": UNMATCHED_ALIAS}
    workdir = tempfile.mkdtemp(prefix="dashboard-aliases-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "aliases.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


def _status(probe, path):
    return probe["hits"][path][0]


def _target(probe, path):
    return probe["hits"][path][1]


def _bot_function_source(name: str) -> str:
    """The body of one function in bot.py, so a source assertion cannot be
    satisfied by an identical line somewhere else in a 120k-line file.

    Written after exactly that happened: the open-redirect check below was
    asserted against the whole file, and `pulse_dashboard_module_link` carries
    the same line, so deleting the guard from *this* route left the test green.
    """
    source = _read(BOT_PY)
    start = source.find("\ndef " + name + "(")
    assert start >= 0, (
        "bot.py has no function %r any more; the route it belongs to has been "
        "renamed or removed and these guards are pointing at nothing" % name)
    body = source[start + 1:]
    end = re.search(r"\n(?:@|def |class )", body)
    return body[:end.start()] if end else body


def _registry_route(probe, category, widget_key):
    for cat, key, _name, route in probe["registry"]:
        if (cat, key) == (category, widget_key):
            return route
    raise AssertionError(
        "no %r row in the %r category of the web registry; this test's idea of "
        "what the alias should redirect to has gone stale"
        % (widget_key, category))


# --- the gap, now closed and held closed -------------------------------------


def test_every_alias_url_the_app_can_produce_resolves(probe):
    """The whole enumerated surface, in one assertion. Stated over all eleven
    groups rather than the two that were broken, because the failure this guards
    against is a *group* going missing, and a thirteenth media module must not
    read as a new regression when it is really the same one row."""
    missing = sorted(p for p in native_alias_urls() if _status(probe, p) == 404)
    assert not missing, (
        "these dashboard deep links resolve natively and 404 on the web, so "
        "following a shared link lands on nothing: %s" % missing)


def test_the_media_and_safety_aliases_land_on_the_modules_own_route(probe):
    """The assertion the rule-level check could not make. `<path:module_alias>`
    matches anything under those two segments, so "a rule matched" says nothing;
    what has to be true is that each URL arrives at the route the registry gives
    for that module — which is also what proves the router reads the registry
    instead of carrying its own copy of the map."""
    by_group = module_keys_by_group()
    checked = 0
    for segment, category in sorted(ROUTED_CATEGORIES.items()):
        group_key = legacy_groups()[segment]
        for key in by_group[group_key]:
            path = "/dashboard/%s/%s" % (segment, slugify(key))
            expected = _registry_route(probe, category, key)
            assert _target(probe, path) == expected, (
                "%s went to %r, but the registry says the %s module lives at "
                "%r. A redirect to the wrong page is worse than the 404 it "
                "replaced: the member believes they arrived."
                % (path, _target(probe, path), key, expected))
            checked += 1
    assert checked >= 17, (
        "only %d media/safety aliases were checked; the two source lists "
        "stopped lining up and this test is guarding a fraction of the gap it "
        "was written for" % checked)


def test_the_aliases_native_accepts_beyond_the_enumerable_ones_work_too(probe):
    """`legacyModuleAliases` accepts the module's title and its route tail as
    well as `slugify(key)`. The census can only enumerate the last of those, so
    serving only that subset would have closed the row on paper while leaving
    real links broken."""
    for path, expected in sorted(EXTRA_NATIVE_ALIASES.items()):
        assert _target(probe, path) == expected, (
            "%s is an alias native resolves and the web sends it to %r "
            "(expected %r)" % (path, _target(probe, path), expected))


def test_an_alias_no_module_answers_to_still_404s(probe):
    """The router refuses rather than falling back. A redirect to /dashboard
    would turn every typo into a page that looks like it worked, which is the
    specific dishonesty the route above it also declines."""
    assert _status(probe, UNMATCHED_ALIAS) == 404, (
        "%s returned %d instead of 404; the router has grown a fallback and "
        "can no longer tell a member their link was wrong"
        % (UNMATCHED_ALIAS, _status(probe, UNMATCHED_ALIAS)))


def test_the_router_has_not_taken_over_the_groups_that_have_real_pages(probe):
    """Scope, held by equality. The other nine groups answer on their own
    `<subsystem_key>` pages, which are richer than a redirect. If one of them
    ever lost its pages and started falling through to this router, the URL
    would still resolve and every count in the census would still be green —
    while the member got bounced to a production route instead of the dashboard
    page they used to get."""
    routed = {segment for path, segment in native_alias_urls().items()
              if probe["hits"][path][2] == "pulse_dashboard_legacy_alias"}
    assert routed == ROUTED_GROUPS, (
        "the set of dashboard groups served by redirect changed to %s "
        "(expected %s). A group that gained real pages should leave this set "
        "and ROUTED_GROUPS together; a group that appears here unexpectedly "
        "has silently lost its own pages."
        % (sorted(routed), sorted(ROUTED_GROUPS)))


# --- why no group hub was built ----------------------------------------------


def test_both_groups_already_have_a_browsing_surface(probe):
    """`/dashboard/media` is deliberately not served, and this records why so it
    does not get "fixed" later. It is not a URL the app can produce — the
    universal link pattern needs a module segment — and the page it would
    duplicate exists: `dashboard.html` loops every category in the registry, so
    both sections have always rendered on `/dashboard`. A per-group web page
    would mirror nothing in the app."""
    template = _read(DASHBOARD_TEMPLATE)
    assert "{% for category in mc.categories %}" in template, (
        "dashboard.html no longer renders every registry category. If it now "
        "renders a subset, media and safety may have lost their only browsing "
        "surface on the web, and declining to build the group hubs stops being "
        "correct.")
    categories = {row[0] for row in probe["registry"]}
    for segment, category in sorted(ROUTED_CATEGORIES.items()):
        assert category in categories, (
            "%r is not a category in the registry the dashboard page renders, "
            "so /dashboard no longer lists the %s modules and there is nowhere "
            "on the web to browse them" % (category, segment))


# --- why this was a build and not a decision ---------------------------------


def test_the_destinations_those_modules_point_at_already_exist(probe):
    """Every module in both groups sends a member somewhere the web already
    serves. That is what made this closable without inventing anything — and
    now that the router redirects to exactly these, a 404 here is a live break
    rather than a hypothetical one."""
    missing = sorted(p for p in MODULE_DESTINATIONS if _status(probe, p) == 404)
    assert not missing, (
        "these are where the media and safety modules send people, and they no "
        "longer resolve: %s. The router is now pointing members at 404s."
        % missing)


def test_the_web_registry_already_carries_both_groups():
    """`services/pulse_dashboard_mission_control.py` declares these modules with
    the same keys and routes as the app. The router resolves against it, so this
    is not background any more: it is the authority the redirect reads. Adding a
    second one would be exactly the web-only backend authority the parity
    mission forbids."""
    source = _read(WEB_REGISTRY)
    for section in ROUTED_CATEGORIES.values():
        assert section in source, (
            "%r is no longer a section in the web's mission-control registry. "
            "The alias router resolves against it, so its modules would begin "
            "404ing again." % section)
    by_group = module_keys_by_group()
    for segment, section in sorted(ROUTED_CATEGORIES.items()):
        group_key = legacy_groups()[segment]
        # Not every native module has to exist on the web. The overlap is what
        # the router can resolve, so a shrinking overlap has to be visible
        # rather than quietly narrowing back into a gap.
        shared = [k for k in by_group[group_key]
                  if '_widget("%s"' % k in source]
        assert len(shared) == len(by_group[group_key]), (
            "only %d of the %d native %s modules are declared in the web "
            "registry; the ones missing have no route to redirect to and their "
            "alias URLs are 404ing again: %s"
            % (len(shared), len(by_group[group_key]), section,
               sorted(set(by_group[group_key]) - set(shared))))


# --- the copies this route makes, pinned to their sources --------------------


def test_the_group_map_in_bot_py_still_agrees_with_the_app():
    """The router holds two entries of app data — "media" is not derivable from
    "Pulse Radio & Media" — so the copy is pinned here rather than hidden. If
    the app renames a segment, this fails instead of the URL quietly 404ing."""
    block = re.search(r"PULSE_DASHBOARD_LEGACY_ALIAS_GROUPS = \{(.*?)\}",
                      _read(BOT_PY), re.S)
    assert block, (
        "PULSE_DASHBOARD_LEGACY_ALIAS_GROUPS is no longer a literal dict in "
        "bot.py; the copy it holds is no longer pinned to the app's map")
    served = dict(re.findall(r'"([a-z]+)": "([^"]+)"', block.group(1)))
    assert set(served) == ROUTED_GROUPS, (
        "bot.py routes %s but this test expects %s"
        % (sorted(served), sorted(ROUTED_GROUPS)))
    app_groups = legacy_groups()
    for segment, category in sorted(served.items()):
        assert segment in app_groups, (
            "bot.py serves /dashboard/%s/ but the app has no such legacy group "
            "any more, so the route answers URLs nothing produces" % segment)
        assert group_slug(category) == app_groups[segment], (
            "bot.py maps %r to the %r category, whose group slug is %r, but the "
            "app's DASHBOARD_LEGACY_GROUPS says that segment is %r. The "
            "redirect would resolve against the wrong group's modules."
            % (segment, category, group_slug(category), app_groups[segment]))


def test_the_two_slugifiers_are_not_quietly_merged():
    """The trap the test above walked into. bot.py holds both functions and they
    differ by one substitution; a tidy-minded refactor collapsing them would
    break either every group whose title has an "&" or the first module whose
    title gets one, and nothing on the current data would notice."""
    source = _read(BOT_PY)
    assert 'def _pulse_dashboard_module_alias_slug' in source, (
        "the module-alias slugifier is gone from bot.py. If it was folded into "
        "_pulse_dashboard_group_slug, module aliases have stopped turning '&' "
        "into 'and' and no longer match what the app mints.")
    assert '.replace("&", "and")' in source, (
        "the '&' -> 'and' substitution is gone; the web now mints a different "
        "alias from the app for any module whose title contains an ampersand")
    assert slugify("Rights & Licensing") == "rights-and-licensing"
    assert group_slug("Pulse Radio & Media") == "pulse-radio-media"


def test_the_router_refuses_a_registry_route_that_is_not_local(probe):
    """The open-redirect guard, checked against the thing it guards. No registry
    row has an absolute URL today, which is exactly why the refusal is easy to
    delete as dead code — so what is asserted is the precondition: if one ever
    appears, the guard is the only reason a PulseSoc dashboard link cannot be
    made to send members off-site."""
    body = _bot_function_source("pulse_dashboard_legacy_alias")
    assert 'if not route.startswith("/"):' in body, (
        "the local-route check is gone from pulse_dashboard_legacy_alias; a "
        "registry row with an absolute URL would turn a /dashboard/ link into "
        "an open redirect. Note the identical line in the router above it does "
        "not cover this one.")
    offsite = sorted({route for cat, _k, _n, route in probe["registry"]
                      if cat in ROUTED_CATEGORIES.values()
                      and not str(route or "").startswith("/")})
    assert not offsite, (
        "the media/safety registry rows now contain non-local routes %s. The "
        "router skips them, so those modules 404 — but the more important "
        "question is why a dashboard module points off-site at all." % offsite)
