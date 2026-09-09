"""Why `/pulse/events/:eventId` is blocked, and what would unblock it.

`linking.ts` publishes `https://pulsesoc.com/pulse/events/:eventId`, so it is a
URL the app can put on a clipboard, and the web 404s on it. The obvious reading
is "the web is behind: build an event detail page." That reading is wrong, and
this file is the standing proof of why — because the obvious reading is the one
someone will act on eighteen months from now, and the deep-link document alone
is a sentence they can disagree with.

## The finding

There is no scheduled-events data anywhere in the product.

- `pulse_live_sessions` has no `scheduled_at` column at all.
- `pulse_live_now_cards` filters to `status IN ('live','publishing','reconnecting')`,
  so a scheduled session is excluded by the query.
- `/api/pulse/live-now` returns `ok`, `items`, `trace_id`, `ranking` — and no
  `scheduled` or `events` key.
- Native's `listLiveNow` computes its scheduled bucket as
  `data.scheduled || data.events || []`, which is therefore always `[]`, so
  `listScheduledLiveEvents` always returns zero items.

So the native Events screen is not a specification the web is failing to match.
It is empty on every launch, for every user.

## Why the web must not mirror it

`EventsScreen` does not show an empty state for a deep link. It calls
`emptyEvent(eventId)` and renders a *fabricated* event — title "PulseSoc Event",
creator "PulseSoc Creator", category "Live" — for whatever id it was handed.
Every `/pulse/events/:eventId` link the app opens shows that placeholder, and
nothing tells the member it is not a real event.

Building a web page to match would fake parity twice: once by inventing a
backend authority for events that the product does not have, which the parity
mission forbids outright, and once by presenting fabricated content as data. On
this row the web hub is the more honest of the two surfaces — it says event
discovery is not enabled yet — and the fix belongs on the native side.

## What this file is for

Not to freeze the gap. To make it self-clearing. Each assertion below names a
specific thing that is missing; if any of them is ever supplied, the test fails
and says that the row is now buildable, and
`scripts/parity/reconcile_urlmap.py` refuses to regenerate the deep-link
document while the `NO_DATA_SOURCE` entry still claims otherwise.

## A note on reading `api/live.ts`

That file is in `config/realtime-audio-protected-paths.json`. This test reads
it and must never edit it: the assertion here is about one line of event
plumbing, and if a legitimate audio change ever moves that line, the correct
response is to update the assertion after reading
`docs/realtime_audio_change_policy.md` — not to reshape a protected file to
keep a parity test green.
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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The keys `/api/pulse/live-now` actually returns. Native reads `scheduled` and
#: `events`; neither is here, which is the whole finding.
EXPECTED_LIVE_NOW_KEYS = {"ok", "items", "trace_id", "ranking"}

#: The buckets native looks for and never finds.
MISSING_BUCKETS = ("scheduled", "events")

#: `listLiveNow` and its cached twin both read the bucket that never arrives.
LIVE_API_SCHEDULED_READ_SITES = 2

#: Fresh load, cached load, and the error branch all fabricate on a deep link.
EVENTS_SCREEN_FALLBACK_SITES = 3

LIVE_API = os.path.join(REPO, "mobile-native", "src", "api", "live.ts")
EVENTS_SCREEN = os.path.join(REPO, "mobile-native", "src", "screens", "EventsScreen.tsx")

_PROBE = r"""
import json, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "events-no-source-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, display_name) "
                "VALUES ('evviewer','ev@example.com','Viewer')")
    viewer_id = cur.lastrowid

    cur.execute("PRAGMA table_info(pulse_live_sessions)")
    columns = [row[1] for row in cur.fetchall()]
    report["live_session_columns"] = columns

    # One scheduled session and one live one. If the API ever starts serving
    # scheduled events, the scheduled row is what will show up.
    fields, values = "user_id, title, category, status", "?,?,?,?"
    scheduled_args = (viewer_id, "Scheduled Event Alpha", "Live", "scheduled")
    live_args = (viewer_id, "Live Right Now", "Live", "live")
    if "scheduled_at" in columns:
        fields += ", scheduled_at"; values += ",?"
        scheduled_args += ("2030-01-01T00:00:00Z",)
        live_args += (None,)
    cur.execute("INSERT INTO pulse_live_sessions (%%s) VALUES (%%s)" %% (fields, values),
                scheduled_args)
    report["scheduled_id"] = cur.lastrowid
    cur.execute("INSERT INTO pulse_live_sessions (%%s) VALUES (%%s)" %% (fields, values),
                live_args)
    report["live_id"] = cur.lastrowid
    conn.commit(); conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = viewer_id

response = client.get("/api/pulse/live-now?limit=24")
payload = response.get_json() if response.status_code == 200 else None
report["live_now"] = {
    "status": response.status_code,
    "keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
    "item_ids": [item.get("id") for item in (payload or {}).get("items") or []],
    "item_titles": [item.get("title") for item in (payload or {}).get("items") or []],
}

pages = {}
for path in ("/pulse/events", "/pulse/events/%%d" %% report["scheduled_id"],
             "/pulse/events/424242"):
    page = client.get(path)
    body = page.get_data(as_text=True)
    pages[path] = {
        "status": page.status_code,
        # The exact strings native fabricates. The web must not print them.
        "fabricated": ("PulseSoc Event" in body) or ("PulseSoc Creator" in body),
        "says_not_enabled": "not configured" in body or "is enabled" in body,
    }
report["pages"] = pages

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def events_probe():
    """Boot the app once, in a child process, and ask it for events.

    Subprocess for the reason the rest of this directory uses one: importing
    ``bot`` binds ``DATABASE_URL`` process-wide and cannot be undone.
    """
    workdir = tempfile.mkdtemp(prefix="events-no-source-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "events.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", _PROBE % {"repo": REPO}],
                          cwd=REPO, env=env, capture_output=True, text=True,
                          timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_the_probe_created_a_scheduled_session_to_look_for(events_probe):
    """Guard the fixture, so "not returned" is never really "not inserted"."""
    assert events_probe["scheduled_id"], "no scheduled session was created"
    assert events_probe["live_id"], "no live session was created"
    assert events_probe["live_now"]["status"] == 200, (
        "/api/pulse/live-now did not answer, so nothing below is evidence")


def test_the_live_now_api_serves_no_scheduled_bucket(events_probe):
    """The missing half of the contract native reads.

    If this fails because a bucket appeared, that is good news: the events row
    is now buildable. Build the web surface against the new source and delete
    the `/pulse/events/:eventId` entry from `NO_DATA_SOURCE` in
    `scripts/parity/reconcile_urlmap.py`.
    """
    keys = set(events_probe["live_now"]["keys"])
    present = [bucket for bucket in MISSING_BUCKETS if bucket in keys]
    assert not present, (
        "/api/pulse/live-now now returns %s — a scheduled-events source exists, "
        "so /pulse/events/:eventId is no longer blocked. See this test's "
        "docstring." % present)
    assert keys == EXPECTED_LIVE_NOW_KEYS, (
        "the live-now response shape changed to %s; re-check whether an events "
        "source arrived under a different name" % sorted(keys))


def test_a_scheduled_session_is_not_returned_by_live_discovery(events_probe):
    """The query itself excludes it, not just the response shape.

    Asserted with a row rather than by reading the SQL, so widening the status
    filter is caught even if the response keys never change.
    """
    live = events_probe["live_now"]
    assert events_probe["scheduled_id"] not in live["item_ids"], (
        "a scheduled session is now served by live discovery (%s); the events "
        "row may be buildable" % live["item_titles"])
    assert events_probe["live_id"] in live["item_ids"], (
        "the live session is missing too, so this test is measuring a broken "
        "endpoint rather than an absent feature: %s" % live["item_ids"])


def test_live_sessions_have_nowhere_to_record_a_schedule(events_probe):
    """`isScheduledLive` also accepts any row with `scheduled_at`.

    So the column's absence is load-bearing: with it, some rows could become
    events without the status filter changing at all.
    """
    assert "scheduled_at" not in events_probe["live_session_columns"], (
        "pulse_live_sessions grew a scheduled_at column — scheduled events may "
        "now be expressible, which would unblock /pulse/events/:eventId")


def test_native_reads_a_bucket_the_backend_never_sends(events_probe):
    """The other end of the same contract, read from the app's own source.

    This is the line that turns a missing key into an always-empty screen. It
    lives in a real-time-audio protected file; see this module's docstring
    before touching it.
    """
    source = _read(LIVE_API)
    # Counted, not merely searched. There are two read sites (`listLiveNow` and
    # the cached path), and either one being repointed at a real source is the
    # news this test exists to deliver -- a search would report the surviving
    # one and stay green through half a fix.
    sites = re.findall(r"data\.scheduled\s*\|\|\s*data\.events\s*\|\|\s*\[\]", source)
    assert len(sites) == LIVE_API_SCHEDULED_READ_SITES, (
        "mobile-native/src/api/live.ts now derives its scheduled bucket from "
        "data.scheduled || data.events || [] in %d places, not %d. Re-read it: "
        "either the app now has a real source, or this finding needs restating."
        % (len(sites), LIVE_API_SCHEDULED_READ_SITES))


def test_native_fabricates_an_event_rather_than_showing_an_empty_state():
    """The reason the web must not copy this screen.

    Pinned so that if native is ever fixed — the right outcome — whoever fixes
    it is told that the parity document still describes the old behaviour.
    """
    source = _read(EVENTS_SCREEN)
    # Three separate `if (eventId)` paths reach the fallback: the fresh load,
    # the cached load, and the error branch. Counted rather than searched,
    # because a fix that made only one of them honest would leave the other two
    # fabricating and a mere `in` check would never say so. The error branch is
    # the worst of the three -- it sets an error message and *then* fabricates a
    # selected event, so the screen shows both at once.
    fallbacks = source.count("emptyEvent(eventId)")
    assert fallbacks == EVENTS_SCREEN_FALLBACK_SITES, (
        "EventsScreen falls back to emptyEvent(eventId) in %d places, not %d. "
        "If native now shows an honest empty or error state, update "
        "NO_DATA_SOURCE in scripts/parity/reconcile_urlmap.py, which still says "
        "it fabricates." % (fallbacks, EVENTS_SCREEN_FALLBACK_SITES))
    fabricated = re.search(r'function emptyEvent\([^)]*\)[^{]*\{(.*?)\n\}',
                           source, re.S)
    assert fabricated, "emptyEvent is no longer a top-level function; re-read it"
    body = fabricated.group(1)
    assert '"PulseSoc Event"' in body and '"PulseSoc Creator"' in body, (
        "emptyEvent no longer fabricates a titled event and creator; re-check "
        "whether the placeholder is still presented as real content")


def test_the_web_hub_serves_and_does_not_fabricate(events_probe):
    """The web's honest state, and the thing that must not regress.

    A future events page that renders a placeholder would pass a routing check
    and fail here, which is the point.
    """
    hub = events_probe["pages"]["/pulse/events"]
    assert hub["status"] == 200, "the events hub stopped serving"
    assert not hub["fabricated"], (
        "the web events hub now prints native's placeholder copy; a fabricated "
        "event is not parity, it is the bug being copied")


def test_the_item_link_404s_rather_than_inventing_an_event(events_probe):
    """Blocked, and honest about it.

    A 404 here is not the desired end state — it is the correct one while no
    data source exists. The alternative on offer is native's: answer every id
    with a convincing fake.

    Both a real scheduled session's id and an arbitrary one are checked, because
    the failure worth catching is a page that answers *any* id.
    """
    item_paths = [path for path in events_probe["pages"] if path != "/pulse/events"]
    assert len(item_paths) >= 2, (
        "the probe stopped requesting item links, so this proves nothing")
    for path in sorted(item_paths):
        page = events_probe["pages"][path]
        assert page["status"] == 404, (
            "%s now serves (%s). If it was built against a real source, remove "
            "the NO_DATA_SOURCE entry for /pulse/events/:eventId. If it was "
            "built to match native's placeholder, it is fabricating content and "
            "must be removed." % (path, page["status"]))
        assert not page["fabricated"], (
            "%s printed native's placeholder copy" % path)
