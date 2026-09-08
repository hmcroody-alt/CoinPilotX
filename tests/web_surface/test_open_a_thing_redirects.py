"""Three routes the parity matrix could only describe as "route redirects
rather than rendering a web surface", and what they actually are.

`/pulse/compose`, `/pulse/profile` and `/pulse/status/create` all landed in the
matrix as PARTIAL with that identical note, which is the machine saying "I found
a 302 and cannot tell you why". Read together against
`mobile-native/src/navigation/nativeRouteActions.ts` they turn out to be one
pattern, not three problems. Each is a URL the app resolves to *go to screen X
and open thing Y*:

    /pulse/compose        Home,   openComposer: true
    /pulse/status/create  Status, openCreator: true
    /pulse/profile        Profile (the signed-in member's own)

None of them is a page. The composer is a fragment on the feed, the status
creator is a panel on the status page, and "my profile" has no fixed URL at all
— it resolves per member. So a redirect is the honest web answer for all three,
and the thing worth testing is not that they redirect but that they *arrive
open*. A redirect that lands on the right page with the composer shut is
indistinguishable, to the member, from a link that did nothing.

That was not hypothetical. Two of these were broken when this file was written:

  status/create   swallowed by `/pulse/status/<path:status_id>`, so "create"
                  was read as a status id and the member reached the lane with
                  the viewer trying to open a story that does not exist. The
                  app's own Add Status link, dead on the web.

  ?create=1       the web's *own* creator-command panel has linked
                  `/pulse/status?create=1` for ages and nothing ever read the
                  parameter. Not a native-parity gap — a web link that pointed
                  at itself and was ignored.

Every extractor asserts it matched before it compares.
"""

from __future__ import annotations

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
ROUTE_ACTIONS_TS = os.path.join(NATIVE, "navigation", "nativeRouteActions.ts")
HOME_CORE_JS = os.path.join(REPO, "static", "js", "pulse_home_core.js")
BOT_PY = os.path.join(REPO, "bot.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --- what the app says these URLs mean ---------------------------------------


def native_route_action(path: str) -> str:
    """The body of the `nativeRouteActions` branch for one path.

    This file is the app's own answer to "a member opened this URL, now what",
    so it is the right authority for whether a URL means *view a thing* or
    *open a composer*. The web has to agree with the verb, not just the page.
    """
    source = _read(ROUTE_ACTIONS_TS)
    match = re.search(
        r'if \(path === "%s"\)\s*(navigation\.navigate\([^;]+);' % re.escape(path),
        source)
    assert match, (
        f"nativeRouteActions.ts no longer has a branch for {path!r}; the web "
        "route was built to match what that branch does, so re-read it before "
        "trusting the redirect")
    return match.group(1)


def test_the_three_urls_all_mean_open_something_not_just_go_somewhere():
    """The shared premise. If any of these stops carrying an "open" parameter,
    its web redirect should stop opening the thing too, and this is where that
    shows up."""
    assert "openComposer: true" in native_route_action("/pulse/compose"), (
        "/pulse/compose no longer asks the app to open the composer")
    assert "openCreator: true" in native_route_action("/pulse/status/create"), (
        "/pulse/status/create no longer asks the app to open the status creator")
    profile = native_route_action("/pulse/profile")
    assert 'screen: "Profile"' in profile, (
        "/pulse/profile no longer resolves to the member's own Profile screen")
    assert "openComposer" not in profile and "openCreator" not in profile, (
        "/pulse/profile now opens something; the web redirect only navigates")


def test_the_web_feed_opens_its_composer_on_the_fragment_compose_points_at():
    """`/pulse/compose` redirects to `/pulse#create`, which is only the right
    answer while the feed still acts on that fragment. If this handler goes, the
    redirect quietly becomes "send them to the feed and do nothing"."""
    source = _read(HOME_CORE_JS)
    match = re.search(r"location\.hash === ['\"]#create['\"]", source)
    assert match, (
        "pulse_home_core.js no longer opens the composer for #create, so "
        "/pulse/compose now lands on the feed with nothing open")
    tail = source[match.end():match.end() + 200]
    assert "openPulseComposer" in tail, (
        "the #create branch no longer calls openPulseComposer")


# --- what the server does ----------------------------------------------------

_PROBE = r"""
import json, re, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "open-a-thing-redirects-test"
report = {}

adapter = app.url_map.bind("pulsesoc.com")
def endpoint(path):
    try:
        return adapter.match(path, method="GET")[0]
    except Exception as exc:
        return "NO MATCH: " + type(exc).__name__
report["endpoints"] = {p: endpoint(p) for p in %(match_paths)r}

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                ("statusowner", "statusowner@example.com", "Status Owner"))
    conn.commit()
    cur.execute("SELECT user_id FROM users WHERE username='statusowner'")
    owner = int(cur.fetchone()[0])
    canonical = bot.pulse_profile_canonical_path(owner)
report["canonical_profile"] = canonical

anonymous = app.test_client()
report["signed_out"] = {p: [anonymous.get(p).status_code,
                            anonymous.get(p).headers.get("Location", "")]
                        for p in %(auth_paths)r}

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = owner

report["redirects"] = {}
for path in %(get_paths)r:
    response = client.get(path)
    report["redirects"][path] = [response.status_code,
                                 response.headers.get("Location", "")]

page = client.get("/pulse/status")
body = page.get_data().decode("utf-8", "replace")
report["status_page_status"] = page.status_code
# The bootstrap block is read back off the served page rather than out of the
# source file: a branch that exists in bot.py but never reaches the browser
# would pass a source grep and still leave the composer shut.
report["reads_create_param"] = bool(
    re.search(r"statusParams\.get\('create'\)", body))
report["calls_open_creator"] = bool(re.search(r"openStatusCreator\(", body))
report["has_desktop_studio_branch"] = bool(
    re.search(r"function openStatusCreator[^}]*statusMobileMode", body))
# The viewer branch has to `return` before the create branch, or a link to a
# story would also pop the composer over the top of it.
report["viewer_returns_before_create"] = bool(
    re.search(r"openStatusViewer\(linkedStatusId\);\s*return", body))
report["panel_links_create_param"] = "/pulse/status?create=1" in _read_bot()

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

_PROBE_HELPERS = r"""
def _read_bot():
    with open(%(bot)r, encoding="utf-8") as handle:
        return handle.read()
"""

MATCH_PATHS = ["/pulse/status/create", "/pulse/status/123", "/pulse/status/a/b",
               "/pulse/status", "/pulse/compose", "/pulse/profile"]
AUTH_PATHS = ["/pulse/status/create", "/pulse/profile"]
GET_PATHS = ["/pulse/compose", "/pulse/profile", "/pulse/status/create",
             "/pulse/status/123"]


def _run_probe() -> dict:
    code = (_PROBE_HELPERS % {"bot": BOT_PY}) + (_PROBE % {
        "repo": REPO, "match_paths": MATCH_PATHS, "auth_paths": AUTH_PATHS,
        "get_paths": GET_PATHS})
    workdir = tempfile.mkdtemp(prefix="web-open-a-thing-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "web.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def probe():
    return _run_probe()


# --- status/create: the shadowed one -----------------------------------------


def test_the_create_link_is_not_read_as_a_status_id(probe):
    """The bug this route was added for.

    `/pulse/status/<path:status_id>` matched `create` and sent members to
    `?status_id=create`, so the app's Add Status link reached the lane with the
    viewer opening a story that does not exist. Werkzeug prefers the literal
    rule, so this holds regardless of declaration order — but it is asserted
    because nothing else would notice if the literal were deleted.
    """
    assert probe["endpoints"]["/pulse/status/create"] == "pulse_status_create_page", (
        "/pulse/status/create is served by "
        f"{probe['endpoints']['/pulse/status/create']}; if that is the "
        "parameterised rule again, 'create' is being read as a status id")


def test_adding_the_literal_route_did_not_shadow_real_status_links(probe):
    """The risk of fixing it. Status ids reach that rule from notifications and
    e-mail, and a literal that stole them would break every shared story link
    while looking like a tidy fix."""
    assert probe["endpoints"]["/pulse/status/123"] == "pulse_status_notification_redirect"
    assert probe["endpoints"]["/pulse/status/a/b"] == "pulse_status_notification_redirect"
    assert probe["redirects"]["/pulse/status/123"] == [302, "/pulse/status?status_id=123"]


def test_the_create_link_lands_on_the_page_asking_for_the_composer(probe):
    """It redirects to the web's existing spelling rather than a new parameter.
    `?create=1` is what the creator command panel already linked, so this adds a
    reader for a link the site was already publishing instead of inventing a
    second way to say the same thing."""
    assert probe["redirects"]["/pulse/status/create"] == [302, "/pulse/status?create=1"]


def test_the_status_page_actually_acts_on_the_create_parameter(probe):
    """The half that makes the redirect worth anything.

    Checked against the *served* page, not the source, because a branch that
    exists in bot.py but never reaches the browser would satisfy a grep and
    still leave the member looking at a page that ignored them.
    """
    assert probe["status_page_status"] == 200
    assert probe["reads_create_param"], (
        "the status page never reads ?create=1, so /pulse/status/create and the "
        "creator command panel both land with the composer shut")
    assert probe["calls_open_creator"]


def test_opening_the_composer_is_not_assumed_to_mean_opening_the_sheet(probe):
    """Above 900px a media query hides the create sheet and the studio card
    carries the form. Opening the sheet there would toggle a class nobody can
    see, which is the same outcome as the bug being fixed — a link that appears
    to do nothing."""
    assert probe["has_desktop_studio_branch"], (
        "openStatusCreator no longer branches on statusMobileMode, so on a "
        "desktop it opens a sheet the stylesheet keeps hidden")


def test_a_link_to_a_story_still_shows_that_story(probe):
    """Both parameters can be present. Viewing has to win: a link to a specific
    status is a request to see it, and popping the composer over the top would
    hide the thing the member followed the link for."""
    assert probe["viewer_returns_before_create"], (
        "the create branch is no longer guarded by the viewer's return, so a "
        "?status=... link may open the composer on top of the story")


def test_the_creator_panel_link_the_web_already_published_now_works(probe):
    """This one was never a native gap. The web linked `?create=1` from its own
    creator command panel and nothing read it — a dead link inside the product,
    found only because the native URL happened to point at the same place."""
    assert probe["panel_links_create_param"], (
        "nothing links /pulse/status?create=1 any more; if the panel moved to a "
        "different spelling, the redirect above should follow it")


# --- compose and profile: routers that were always correct -------------------


def test_compose_lands_on_the_feed_with_the_composer_opening(probe):
    """Not a missing page. The web composer is a fragment on the feed, so there
    is no `/pulse/compose` page to build — building one would be a second
    composer with its own idea of what a draft is."""
    assert probe["redirects"]["/pulse/compose"] == [302, "/pulse#create"]


def test_my_profile_resolves_to_the_signed_in_member(probe):
    """"My profile" is the one URL that cannot be a static page: it means a
    different page per visitor. The redirect is the answer, and it has to be the
    member's own canonical path rather than a generic profile shell."""
    status, location = probe["redirects"]["/pulse/profile"]
    assert status == 302
    assert location == probe["canonical_profile"], (
        f"/pulse/profile sent the member to {location!r} instead of their "
        f"canonical path {probe['canonical_profile']!r}")
    assert location not in ("/pulse/profile", "/pulse", ""), (
        "the profile redirect is not resolving to a member at all")


@pytest.mark.parametrize("path", AUTH_PATHS)
def test_a_signed_out_visitor_is_asked_to_sign_in_rather_than_bounced(probe, path):
    """These need a member to resolve — one needs to know whose profile, the
    other needs somewhere to post. Sending a signed-out visitor onwards would
    hand them a page that cannot answer, so they get the sign-in with a `next`
    that returns them here."""
    status, location = probe["signed_out"][path]
    assert status == 302
    assert "/login" in location, (
        f"{path} sent a signed-out visitor to {location!r} instead of sign-in")
    assert "next=" in location, (
        f"{path} drops the visitor's destination, so signing in strands them")
