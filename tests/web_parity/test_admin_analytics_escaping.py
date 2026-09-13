"""/admin/analytics renders attacker-controlled text. It must escape it.

`analytics_summary()` reads `analytics_events`, `leads`, `email_logs` and
`referral_events`. The first two are written by **unauthenticated** public
endpoints — `POST /api/track` and the lead form — so anyone on the internet can
put a string of their choosing into a row that only an administrator will ever
look at. Rendering those rows unescaped is stored XSS with the best possible
target: the payload waits in the database until an admin opens the page, then
executes with an admin session on the highest-privilege surface in the product.

Four things made the old code look safer than it was, and each has a test here
because each would be re-introduced by someone reasoning the same way:

1. **`clean_html()` at the ingest site looks like sanitisation.** It is
   `re.sub(r"<[^>]+>", " ")` — a tag stripper. It removes complete tags and
   nothing else, so an unterminated `<img src=x onmouseover=...` passes through
   intact and the browser completes it against the next `>` on the page.
2. **`metadata` never goes through it at all.** It is stored as
   `json.dumps(...)`, which escapes `"` and `\` but has no opinion about `<`.
3. **A request-level filter looks like a WAF.** `security_guard.suspicious_text`
   rejects JSON bodies matching `<script|javascript:|onerror=|onload=`. That is
   four tokens, and every payload below clears it — `onmouseover`, `onfocus`,
   `ontoggle`, `<iframe srcdoc>`. It also only inspects
   `request.mimetype == "application/json"`, so a form-encoded POST is not
   examined at all. It raises the cost of the obvious payload and stops none of
   these. Defence in depth is the reason to keep it, not a reason to skip
   escaping.
4. **The reflected `password` parameter looks unreachable behind a 401.** It is
   not: `require_admin_password()` returns True on an `admin_user_id` session
   without reading the query argument, so a link sent to a signed-in admin
   renders whatever that parameter holds.

These tests drive the real route through the real Flask app. Asserting against
a copy of the `table()` helper would pass forever while the page stayed
vulnerable.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process.

Run: python3 -m pytest tests/web_parity/test_admin_analytics_escaping.py
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "admin-analytics-escaping-tests")
os.environ["ADMIN_ANALYTICS_PASSWORD"] = "test-admin-password"

import bot  # noqa: E402

HTTPS = {"X-Forwarded-Proto": "https"}

#: Working payloads, not marker strings — and specifically payloads that are
#: *storable*. Every one of these was verified to pass `security_guard`'s
#: `<script|javascript:|onerror=|onload=` filter and reach the database through
#: an anonymous `POST /api/track`. Using `onerror=` here instead would make the
#: whole module pass for the wrong reason: the request would be rejected at the
#: door, nothing would be stored, and the page would be clean no matter how it
#: rendered.
PAYLOADS = {
    # Complete tag. Survives to the page only via `metadata`, which skips
    # `clean_html` entirely.
    "img_onmouseover": "<img src=x onmouseover=alert(1)>",
    # Unterminated, so the tag stripper does not recognise it and leaves it
    # whole. The browser closes it against the next `>` in the document.
    "unterminated_img": "<img src=x onmouseover=alert(1)",
    "unterminated_svg": "<svg onfocus=alert(1) autofocus tabindex=1",
    # No angle brackets at all: harmless in a text node, lethal if a cell is
    # ever moved into an attribute. Escaping covers both; a tag stripper covers
    # neither.
    "attr_breakout": '" onmouseover=alert(1) x="',
    # Closes the cell and starts new markup of its own.
    "closing_td": "</td></tr><table><tr><td ontoggle=alert(1)>",
    "iframe_srcdoc": '<iframe srcdoc="&lt;img src=x&gt;"',
}

#: Tag openings. Asserting on attribute names instead would be wrong — after
#: escaping, `onmouseover=` legitimately appears inside the inert text
#: `&lt;img src=x onmouseover=alert(1)`. What must never appear is a `<` that
#: starts a tag the attacker chose.
TAG_OPENINGS = ("<img", "<svg", "<iframe", "<details", "<form action")


@pytest.fixture(scope="module")
def seeded():
    """Plant every payload through the routes an attacker would actually use."""
    with bot.webhook_app.app_context():
        bot.init_db()

    client = bot.webhook_app.test_client()
    for name, payload in PAYLOADS.items():
        # The public, unauthenticated ingest endpoint. No session, no token.
        response = client.post(
            "/api/track",
            json={
                "session_id": f"xss-{name}",
                "event_name": "api_failure",
                "page_url": payload,
                "referrer": payload,
                "country": payload,
                "metadata": {"note": payload},
            },
            headers=HTTPS,
        )
        assert response.status_code < 400, (name, response.status_code)

    # `leads` is rendered too, and is written by the public lead form. Insert
    # directly rather than depending on that form's current shape.
    conn = bot.db()
    cur = conn.cursor()
    # A fresh database seeds the owner admin with temporary credentials, and
    # every admin route redirects to /admin/change-password until that flag is
    # cleared. Without this the session-authenticated test below gets a 302 and
    # never reaches the render it exists to check.
    cur.execute("UPDATE admin_users SET must_change_password=0 WHERE id=1")
    cur.execute(
        "INSERT INTO leads (full_name, email, phone, country, email_opt_in, "
        "sms_opt_in, created_at) VALUES (?, ?, ?, ?, 1, 1, ?)",
        (
            PAYLOADS["img_onmouseover"],
            "attacker@example.com",
            PAYLOADS["attr_breakout"],
            PAYLOADS["unterminated_svg"],
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return client


@pytest.fixture(scope="module")
def page(seeded):
    response = seeded.get(
        "/admin/analytics?password=test-admin-password", headers=HTTPS
    )
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def test_the_payloads_actually_reached_the_page(page):
    """Guard against a vacuous pass.

    If ingest were rejecting these, or the page stopped rendering these tables,
    every assertion below would pass against a page that simply has no attacker
    data on it — the protection would be gone with no red test. So require the
    escaped form to be present: the payload got in, and it is inert.
    """
    assert "&lt;img src=x onmouseover=alert(1)&gt;" in page, (
        "the escaped payload is not on the page: either ingest did not store "
        "it or the page no longer renders the rows these tests are guarding"
    )


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_no_payload_survives_as_live_markup(name, page):
    payload = PAYLOADS[name]
    assert payload not in page, (
        f"{name} was rendered verbatim into /admin/analytics — an anonymous "
        f"POST to /api/track becomes script execution in an admin's session"
    )


def test_no_executable_tag_appears_anywhere_in_the_page(page):
    """Substring checks per payload can be defeated by a partial escape.

    A render that escaped `<` but not `>` would leave `&lt;img src=x
    onmouseover=alert(1)>` on the page: the exact payload string is absent, so
    every test above passes, and the result is still a live tag. Assert instead
    that no attacker-chosen tag opening exists in the document at all.
    """
    for fragment in TAG_OPENINGS:
        assert fragment not in page, (
            f"{fragment!r} is present as live markup in the rendered page"
        )


def test_the_reflected_password_parameter_is_escaped(seeded):
    """The session path is what makes this reachable.

    `require_admin_password()` short-circuits on `admin_user_id` and never
    looks at the query argument, so the 401 that appears to gate this render
    does not fire for a signed-in admin following a crafted link.
    """
    # A real admin session, not just the id: `admin_current_user()` treats a
    # session with no `admin_session_issued_at` as legacy and clears it, so the
    # bare id would 401 and this test would pass without rendering anything.
    now = datetime.now().isoformat()
    with seeded.session_transaction() as flask_session:
        flask_session["admin_user_id"] = 1
        flask_session["admin_session_issued_at"] = now
        flask_session["admin_session_last_seen"] = now

    breakout = '"><img src=x onmouseover=alert(1)>'
    response = seeded.get(
        "/admin/analytics", query_string={"password": breakout}, headers=HTTPS
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert breakout not in body
    assert "<img src=x" not in body
    assert 'href="/admin/analytics/export/emails?password=' in body, (
        "the export links should still be rendered, just with a safe value"
    )


def test_clean_html_is_a_stripper_not_an_escaper():
    """Pin the reason the ingest-side call is not a defence.

    If `clean_html` is ever hardened into a real escaper, this test fails and
    whoever did it gets to decide deliberately whether the output-side escaping
    is still required. It is — output encoding is contextual — but that should
    be a decision, not an accident.
    """
    assert bot.clean_html("<img src=x onmouseover=alert(1)") == "<img src=x onmouseover=alert(1)"
    assert bot.clean_html('" onmouseover=alert(1) x="') == '" onmouseover=alert(1) x="'


def test_the_request_filter_is_a_four_token_blocklist():
    """Pin what the WAF does and does not cover, so nobody over-trusts it.

    Someone reading `xss_payload_blocked` in the security log could reasonably
    conclude the input side is handled. It covers four tokens. If that list is
    ever broadened this test fails, which is the moment to re-read the claim in
    this module's docstring rather than to quietly delete it.
    """
    from services import security_guard

    assert security_guard.suspicious_text("<script>alert(1)</script>")
    assert security_guard.suspicious_text("<img src=x onerror=alert(1)>")
    for payload in PAYLOADS.values():
        assert not security_guard.suspicious_text(payload), (
            f"{payload!r} is now blocked at ingest, so it can no longer reach "
            f"the admin page — this test file needs a payload that still can, "
            f"or it is proving nothing"
        )
