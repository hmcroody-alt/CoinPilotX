"""The Private Office web surface: its URLs, and the honesty of its client.

`mobile-native/src/navigation/linking.ts` publishes eleven
`https://pulsesoc.com/pulse/private-office/...` universal links. Every one of
them 404'd until this surface existed, which meant sharing anything from inside
the Office produced a dead link for the recipient *and* for the sender's own
browser. These tests exist to keep that from silently coming back: a route
renamed, or a converter narrowed, would restore the 404s without failing
anything else in the suite.

## Why the routing check runs in a subprocess

Everything else in this directory is built to keep the 118k-line monolith *out*
of the pytest process — `conftest.py` exists because modules that rebind
``DATABASE_URL`` at import time were stealing it from each other. Importing
`bot` here would be the same mistake with a much bigger blast radius: it binds
the database for the whole process and cannot be undone. So the app is booted
once, in a child process, which reports back nothing but paths and status
codes.

## Where the client is tested

The browser client these pages ship is not theirs alone — `PULSE_WEB_SECTION_JS`
also backs orders and Pages — so its behaviour is exercised in
`tests/web_surface/`, in node against a stub DOM. What stays here is what is
specific to the Office: its URLs, and the fact that every one of them serves
the client at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Every path `linking.ts` publishes for the Office, minus meetings.
#:
#: Meetings are excluded deliberately rather than forgotten. `private_meetings`
#: resolves to TEMPORARILY_DISABLED at every tier, so the server already refuses
#: it, and a web meeting room would be a second real-time audio publication
#: path, which `docs/realtime_audio_change_policy.md` forbids outright.
DEEP_LINKS = (
    "/pulse/private-office",
    "/pulse/private-office/facts",
    "/pulse/private-office/security",
    "/pulse/private-office/documents",
    "/pulse/private-office/people",
    "/pulse/private-office/briefings",
    "/pulse/private-office/shield",
    "/pulse/private-office/concierge",
    "/pulse/private-office/capital-graph",
    "/pulse/private-office/capital-graph/17",
    "/pulse/private-office/obligations",
)

#: Probed alongside the real links. A typo in a record view must 404 rather
#: than render a confident empty Office.
UNKNOWN_VIEW = "/pulse/private-office/nonsense"

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "office-web-surface-test"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("officeweb", "officeweb@example.com", "Office Web"),
    )
    conn.commit()
    user_id = cur.lastrowid

report = {"rules": sorted({r.rule for r in app.url_map.iter_rules()
                           if r.rule.startswith("/pulse/private-office")})}

anonymous = app.test_client()
signed_out = anonymous.get("/pulse/private-office")
report["signed_out_status"] = signed_out.status_code
report["signed_out_location"] = signed_out.headers.get("Location", "")

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = user_id

pages = {}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data()
    pages[path] = {"status": response.status_code,
                   "shell": b"office-root" in body,
                   "client": b"GRANT_KEY" in body}
report["pages"] = pages

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def office_probe():
    """Boot the app once, in a child process, and report what it serves."""
    workdir = tempfile.mkdtemp(prefix="office-web-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "office.db")
    # Sync so a schema failure surfaces as a failed probe rather than on a
    # daemon thread whose traceback only reaches the log.
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO, "paths": list(DEEP_LINKS) + [UNKNOWN_VIEW]}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    if "<<<REPORT>>>" not in proc.stdout:
        pytest.fail("the app did not boot:\n" + proc.stdout[-4000:] + proc.stderr[-4000:])
    return json.loads(proc.stdout.split("<<<REPORT>>>", 1)[1])


def test_every_office_deep_link_resolves(office_probe):
    """Werkzeug decides whether these URLs exist, not a string comparison."""
    broken = {path: info["status"]
              for path, info in office_probe["pages"].items()
              if path != UNKNOWN_VIEW and info["status"] != 200}
    assert not broken, f"published app URLs that the site cannot serve: {broken}"


def test_every_office_page_ships_the_client(office_probe):
    """A 200 that renders no shell is a page that would sit blank forever."""
    for path in DEEP_LINKS:
        info = office_probe["pages"][path]
        assert info["shell"], f"{path} rendered no Office shell"
        assert info["client"], f"{path} shipped no client script"


def test_unknown_record_view_is_a_404_not_an_empty_page(office_probe):
    """`/api/private-office/records/<view>` 400s on anything outside the six
    views, so the page space is closed with an `any(...)` converter. A bare
    catch-all would answer 200 with an empty Office for a typo."""
    assert office_probe["pages"][UNKNOWN_VIEW]["status"] == 404


def test_signed_out_visitors_get_the_login_page(office_probe):
    """Not an Office-shaped 401.

    The page checks only for a session. Entitlement is left entirely to
    `/api/private-office/overview`, because a second server-side tier check
    here is exactly the drift this subsystem is built to prevent.
    """
    assert office_probe["signed_out_status"] == 302
    assert "/login" in office_probe["signed_out_location"]
