"""The shared PulseSoc web surface: one client, and the pages built on it.

`bot.PULSE_WEB_SECTION_JS` is a single browser client that backs every page
built with `pulse_web_section_shell` — the Private Office, orders, Pages. It
exists so those pages agree about the things that are easy to get wrong once
per page: that a failed fetch is not an empty list, that a 401 sends you to the
login page instead of drawing a confident zero, and that a 200 which does not
contain the expected records is a fault rather than an empty store.

Because it is one client, a regression in it is a regression everywhere at
once, which is the argument for testing it here rather than inside each
subsystem's own directory.

## Why the routing check runs in a subprocess

Importing `bot` binds ``DATABASE_URL`` for the whole pytest process and cannot
be undone; `tests/private_office/conftest.py` exists because modules doing that
at import time were stealing the variable from each other. So the app is booted
once, in a child process, which reports back nothing but paths and status
codes.

## Why part of it is written in JavaScript

The client's real failure modes live in the browser and cannot be reached from
Python: drawing "you have nothing" over a failed fetch, composing a link out of
a field that came back empty, or dropping a nested object from a record without
a trace. Those are exercised in node against a stub DOM by
`pulse_web_client_harness.js`.
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

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BOT = os.path.join(REPO, "bot.py")
HARNESS = os.path.join(HERE, "pulse_web_client_harness.js")

#: Paths `mobile-native/src/navigation/linking.ts` publishes as universal links
#: that this tranche gave a web surface. Each one 404'd before it existed, so
#: sharing that screen from the app produced a dead link for the recipient and
#: for the sender's own browser.
PAGES = (
    "/pulse/orders",
    "/pulse/orders/17",
    "/dashboard/orders",
    "/pulse/pages",
    "/pulse/pages/create",
    "/pulse/pages/acmeco",
)

#: `/pulse/account-health` is served by the page that already existed at
#: `/dashboard/account/health`, so it renders server-side and ships no client.
SERVER_RENDERED = "/pulse/account-health"

#: The receipt URL `pulse_buyer_order_response` mints into every order it
#: serialises. It has never resolved, which made every receipt link PulseSoc
#: has ever handed a buyer a 404.
RECEIPT = "/dashboard/orders?order_id=5&source=creator_transactions"

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "pulse-web-surface-test"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("pulseweb", "pulseweb@example.com", "Pulse Web"),
    )
    conn.commit()
    user_id = cur.lastrowid

report = {}

anonymous = app.test_client()
signed_out = anonymous.get("/pulse/orders")
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
                   "client": b"GRANT_KEY" in body,
                   "location": response.headers.get("Location", "")}
report["pages"] = pages

health = client.get("/pulse/account-health")
canonical = client.get("/dashboard/account/health")
report["health"] = {"status": health.status_code,
                    "same_as_canonical": health.get_data() == canonical.get_data()}

# The endpoints those pages fetch. A page that renders is worth nothing if the
# API behind it does not answer with the collection the page asks for.
apis = {}
for path, key in (("/api/pulse/orders", "orders"), ("/api/pages", "pages")):
    response = client.get(path)
    payload = response.get_json() or {}
    apis[path] = {"status": response.status_code,
                  "has_collection": isinstance(payload.get(key), list)}

created = client.post("/api/pages", json={"name": "Acme Co", "handle": "acmeco",
                                          "page_type": "BUSINESS",
                                          "confirm_owner": True})
created_body = created.get_json() or {}
apis["POST /api/pages"] = {
    "status": created.status_code,
    "handle": (created_body.get("page") or {}).get("handle", ""),
}
duplicate = client.post("/api/pages", json={"name": "Dup", "handle": "acmeco",
                                            "page_type": "BUSINESS",
                                            "confirm_owner": True})
apis["POST /api/pages (duplicate)"] = {
    "status": duplicate.status_code,
    "message": (duplicate.get_json() or {}).get("message", ""),
}
by_handle = client.get("/api/pages/by-handle/acmeco")
apis["/api/pages/by-handle"] = {
    "status": by_handle.status_code,
    "has_record": isinstance((by_handle.get_json() or {}).get("page"), dict),
}
missing = client.get("/api/pulse/orders/999999")
apis["/api/pulse/orders/<missing>"] = {
    "status": missing.status_code,
    "has_message": bool((missing.get_json() or {}).get("message")),
}
report["apis"] = apis

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def web_probe():
    """Boot the app once, in a child process, and report what it serves."""
    workdir = tempfile.mkdtemp(prefix="pulse-web-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "web.db")
    # Sync so a schema failure surfaces as a failed probe rather than on a
    # daemon thread whose traceback only reaches the log.
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO, "paths": list(PAGES) + [RECEIPT]}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


def test_every_published_path_resolves(web_probe):
    """Werkzeug decides whether these URLs exist, not a string comparison."""
    broken = {path: web_probe["pages"][path]["status"]
              for path in PAGES if web_probe["pages"][path]["status"] != 200}
    assert not broken, f"published app URLs that the site cannot serve: {broken}"


def test_every_page_ships_the_client(web_probe):
    """A 200 that renders no shell is a page that would sit blank forever."""
    for path in PAGES:
        info = web_probe["pages"][path]
        assert info["shell"], f"{path} rendered no client root"
        assert info["client"], f"{path} shipped no client script"


def test_page_create_is_not_read_as_a_handle(web_probe):
    """`/pulse/pages/create` and `/pulse/pages/<handle>` share a prefix.

    Werkzeug prefers the static rule, but that is a property of the routing
    table rather than of the source, so it is asserted against the table. If it
    ever inverted, `create` would resolve as a Page handle and the parity census
    would count the URL as served while it showed "we could not find that Page".
    """
    assert web_probe["pages"]["/pulse/pages/create"]["status"] == 200
    assert web_probe["pages"]["/pulse/pages/create"]["shell"]


def test_receipt_urls_the_api_mints_reach_the_order(web_probe):
    """`receipt_url` is generated by `pulse_buyer_order_response` for every
    order. Redirecting rather than rendering a variant keeps a receipt link and
    a shared order link on the same page — and `source` has to survive the hop,
    because the two transaction tables have independent id sequences."""
    info = web_probe["pages"][RECEIPT]
    assert info["status"] == 302
    assert info["location"].endswith("/pulse/orders/5?source=creator_transactions")


def test_account_health_alias_serves_the_page_that_already_existed(web_probe):
    """Not a second Account Health.

    `/dashboard/account/health` has rendered warnings, strikes and restrictions
    for a long time, and the native screen links to that exact path. Building a
    parallel page for the app's other published URL would have given the same
    product two implementations and two chances to disagree.
    """
    assert web_probe["health"]["status"] == 200
    assert web_probe["health"]["same_as_canonical"]


def test_signed_out_visitors_get_the_login_page(web_probe):
    """Not an API-shaped 401. The page checks only for a session; what a member
    is entitled to see is left entirely to the endpoint it fetches."""
    assert web_probe["signed_out_status"] == 302
    assert "/login" in web_probe["signed_out_location"]


def test_the_endpoints_behind_these_pages_answer_as_the_pages_expect(web_probe):
    """A rendering page in front of an endpoint that answers differently is a
    page that renders a fault. The collection names are the contract."""
    apis = web_probe["apis"]
    for path in ("/api/pulse/orders", "/api/pages"):
        assert apis[path]["status"] == 200, path
        assert apis[path]["has_collection"], f"{path} did not return its collection"
    assert apis["/api/pages/by-handle"]["status"] == 200
    assert apis["/api/pages/by-handle"]["has_record"]


def test_creating_a_page_returns_the_handle_the_form_redirects_to(web_probe):
    """The create form sends the browser to `/pulse/pages/<handle>` using the
    handle in the response. If the endpoint stopped returning one, the form
    would quietly land back on the hub after a successful create."""
    created = web_probe["apis"]["POST /api/pages"]
    assert created["status"] == 200
    assert created["handle"] == "acmeco"


def test_a_rejected_create_carries_words_the_form_can_show(web_probe):
    """The form prints the endpoint's own message. A refusal with no message
    would degrade to a generic "that could not be created", which tells a member
    nothing about the handle they need to change."""
    duplicate = web_probe["apis"]["POST /api/pages (duplicate)"]
    assert duplicate["status"] == 409
    assert duplicate["message"].strip()


def test_a_missing_order_is_a_404_carrying_a_message(web_probe):
    """The client renders 404 as "we could not find that record" rather than as
    a fault, and prefers the server's wording. Both halves depend on this."""
    missing = web_probe["apis"]["/api/pulse/orders/<missing>"]
    assert missing["status"] == 404
    assert missing["has_message"]


def _client_source() -> str:
    """The browser client, read out of the source rather than imported.

    `import bot` would boot the monolith; this assertion is about a string.
    """
    match = re.search(r'PULSE_WEB_SECTION_JS = r"""(.*?)"""',
                      open(BOT, encoding="utf-8").read(), re.S)
    assert match, "PULSE_WEB_SECTION_JS is no longer a module-level raw string"
    return match.group(1)


def test_client_script_parses():
    """A syntax error here is a permanently blank page that no Python test
    would notice, because the page still answers 200."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    config = json.dumps({"mode": "hub", "title": "Private Office", "children": []})
    proc = subprocess.run([node, "--check", "-"],
                          input=_client_source().replace("%%CONFIG%%", config),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_client_never_renders_a_failure_as_an_empty_page(tmp_path):
    """The invariant that matters most: error and empty must not co-render.

    A member whose fetch failed and a member with an empty list must not see the
    same screen, and a member whose tier resolver fell over must never be told
    they do not have the product.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    extracted = tmp_path / "client.js"
    extracted.write_text(_client_source(), encoding="utf-8")
    proc = subprocess.run([node, HARNESS, str(extracted)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
