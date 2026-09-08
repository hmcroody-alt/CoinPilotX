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

## Why part of it is written in JavaScript

The client's real failure modes live in the browser and cannot be reached from
Python: drawing "you have nothing" over a failed fetch, drawing "you do not
have this" over a tier resolver that merely fell over, or turning a capability
with no web page into a link that 404s. Those are exercised in node against a
stub DOM by `private_office_web_harness.js`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOT = os.path.join(REPO, "bot.py")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "private_office_web_harness.js")

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


def _client_source() -> str:
    """The browser client, read out of the source rather than imported.

    `import bot` would boot the monolith; this assertion is about a string.
    """
    match = re.search(r'PRIVATE_OFFICE_WEB_JS = r"""(.*?)"""',
                      open(BOT, encoding="utf-8").read(), re.S)
    assert match, "PRIVATE_OFFICE_WEB_JS is no longer a module-level raw string"
    return match.group(1)


def test_client_script_parses():
    """A syntax error here is a permanently blank Office that no Python test
    would notice, because the page still answers 200."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    config = json.dumps({"mode": "hub", "title": "Private Office", "children": []})
    proc = subprocess.run([node, "--check", "-"],
                          input=_client_source().replace("%%CONFIG%%", config),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_client_never_renders_a_failure_as_an_empty_office(tmp_path):
    """The invariant that matters most: error and empty must not co-render.

    A member whose fetch failed and a member with an empty Office must not see
    the same screen, and a member whose tier resolver fell over must never be
    told they do not have the product.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    extracted = tmp_path / "client.js"
    extracted.write_text(_client_source(), encoding="utf-8")
    proc = subprocess.run([node, HARNESS, str(extracted)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
