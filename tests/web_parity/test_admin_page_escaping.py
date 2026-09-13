"""Admin pages must escape database values, not tag-strip them.

`tests/web_parity/test_admin_analytics_escaping.py` covers one page. This
module covers the pattern behind it, which was repo-wide: ~1,800 f-string
placeholders across ~99 admin page functions interpolated database columns into
HTML, most of them wrapped in `clean_html()`.

`clean_html` is `re.sub(r"<[^>]+>", " ")`. It is a tag stripper, and it is the
reason this went unnoticed for so long -- it reads like sanitisation at every
call site. It is not:

* It removes only *syntactically complete* tags. `<img src=x onmouseover=alert(1)`
  has no closing `>`, so the pattern never matches and the value passes through
  byte for byte. The browser then closes the tag against the next `>` in the
  document, which on a table row is the `>` of `</td>`.
* It does not touch `"`, `'` or `&`. A value landing in `src='...'` or
  `value='...'` could close the attribute and open one of its own without ever
  needing a `<`.

So the fix is at the render site and it is `html_escape`, per the `table()`
helper in `admin_analytics_page`. That is also why escaping these sites is
safe rather than destructive: a value that has been through `clean_html` can
never *be* markup, so no page was relying on it being markup.

Every test below drives the real Flask route through the real app, seeding its
payload through the real writer endpoint. Asserting against a copy of a render
helper would keep passing while the page stayed vulnerable, and seeding by
direct INSERT would keep passing if the writer were later locked down -- the
point is to prove the whole path.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process.

Run: python3 -m pytest tests/web_parity/test_admin_page_escaping.py
"""

import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "admin-page-escaping-tests")

import bot  # noqa: E402

HTTPS = {"X-Forwarded-Proto": "https"}

#: Unterminated on purpose. A complete `<img ...>` would be removed by the
#: `clean_html` most of these writers apply, and the test would then pass
#: against unfixed code for a reason that has nothing to do with the render
#: site. This payload survives `clean_html` untouched, which is the whole
#: point. `onmouseover` rather than `onerror` so `security_guard`'s four-token
#: blocklist does not reject the request before anything is stored.
PAYLOAD = "<img src=x onmouseover=alert(1)"

#: Needs no `<` at all: it closes a single-quoted attribute and opens an event
#: handler inside the same tag. `clean_html` has no opinion about quotes, so
#: this is what makes `src='{avatar_url}'` exploitable.
ATTR_PAYLOAD = "' onmouseover=alert(1) x='"

#: A tag opening the attacker chose. Asserting on `onmouseover=` instead would
#: be wrong: after escaping, that text legitimately appears inside the inert
#: string `&lt;img src=x onmouseover=alert(1)`. What must never appear is a `<`
#: that starts a tag.
LIVE_TAG = "<img src=x"

#: Any value works: verify_csrf() only compares the form field to the session.
CSRF = "escaping-test-csrf-token"


def _admin_client():
    client = bot.webhook_app.test_client()
    now = datetime.now().isoformat()
    with client.session_transaction() as flask_session:
        # A bare `admin_user_id` is treated as a legacy session and cleared, so
        # the page would 302 and the assertions below would pass against an
        # empty string.
        flask_session["admin_user_id"] = 1
        flask_session["admin_session_issued_at"] = now
        flask_session["admin_session_last_seen"] = now
    return client


@pytest.fixture(scope="module")
def seeded():
    with bot.webhook_app.app_context():
        bot.init_db()

    conn = bot.db()
    cur = conn.cursor()
    # A fresh database seeds the owner admin with temporary credentials, and
    # every admin route redirects to /admin/change-password until this clears.
    cur.execute("UPDATE admin_users SET must_change_password=0 WHERE id=1")
    conn.commit()
    conn.close()

    def anon():
        """A client that has never been logged in, carrying a CSRF token.

        One per writer: a successful signup rotates the session, which drops the
        token seeded here, and the next POST would come back as the form again.
        These forms are CSRF-protected, which is not a mitigation for any of
        this -- the attacker submits their own form in their own browser and
        gets a valid token for free -- but the test client has to carry one or
        the pages stay empty and every assertion below passes vacuously.
        """
        client = bot.webhook_app.test_client()
        with client.session_transaction() as anon_session:
            anon_session["csrf_token"] = CSRF
        return client

    # 1. Unauthenticated signup. `full_name` is written to BOTH users.full_name
    #    and users.display_name, which between them are rendered on /admin/users,
    #    /admin/pulse-users, /admin/account-command, /admin/privileges and more.
    #    One anonymous request poisons all of them.
    anon().post(
        "/signup",
        data={
            "full_name": PAYLOAD,
            "username": "xssprobe",
            "email": f"xss-{uuid.uuid4().hex[:8]}@example.com",
            "password": "Str0ng-Passw0rd!x",
            "country": "Testland",
            "age_confirmed": "on",
            "terms_accepted": "on",
            "csrf_token": CSRF,
        },
        headers=HTTPS,
    )

    # 2. Unauthenticated failed login against an address that does not exist.
    #    The User-Agent header is stored raw in auth_events.user_agent -- no
    #    clean_html even at write time -- and rendered on /admin/security. This
    #    is the strongest provenance in the file: no account, no session, no
    #    form field, just a header on a request that was *rejected*.
    #    `terms_accepted` is required or the route 400s before logging anything.
    anon().post(
        "/login",
        data={
            "email": "nobody@example.com",
            "password": "wrong-password",
            "terms_accepted": "on",
            "csrf_token": CSRF,
        },
        headers={**HTTPS, "User-Agent": PAYLOAD},
    )

    # 3. Anonymous support form. Writes support_tickets.name/.subject/.message,
    #    rendered on /admin/support.
    anon().post(
        "/support",
        data={
            "name": PAYLOAD,
            "email": "attacker@example.com",
            "issue_type": "general support",
            "subject": PAYLOAD,
            "message": PAYLOAD,
            "csrf_token": CSRF,
        },
        headers=HTTPS,
    )

    # 4. users.avatar_url lands in `src='...'`, so it needs the quote payload
    #    rather than the tag one. Written by POST /api/pulse/profile/avatar;
    #    set directly here so the test does not depend on a multipart upload.
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET avatar_url = ? WHERE username = ?",
        (ATTR_PAYLOAD, "xssprobe"),
    )
    (user_id,) = cur.execute(
        "SELECT user_id FROM users WHERE username = ?", ("xssprobe",)
    ).fetchone()

    # 5. Join keys, not payloads.
    #
    #    /admin/privileges, /admin/account-command and /admin/intelligence-graph
    #    read the display name out of `users` -- the column the anonymous signup
    #    above already poisoned -- but only for users that appear in a second
    #    table. Without a row there the LEFT JOIN has nothing to drive it, the
    #    page renders empty, and every assertion passes against vulnerable code.
    #
    #    So these rows carry no attacker text at all; they exist to make the
    #    signup payload reachable. Each is written in production by an ordinary
    #    non-admin action: viewing Creator Status materialises a privilege
    #    profile, editing a profile appends a profile audit log, and trust
    #    scoring materialises a trust profile.
    for statement, params in (
        (
            "INSERT INTO user_privilege_profiles (user_id, current_level, trust_score) VALUES (?, 'creator', 10)",
            (user_id,),
        ),
        (
            "INSERT INTO profile_audit_logs (user_id, action, created_at) VALUES (?, 'profile_updated', ?)",
            (user_id, datetime.now().isoformat()),
        ),
        (
            "INSERT INTO user_trust_profiles (user_id, trust_score) VALUES (?, 10)",
            (user_id,),
        ),
    ):
        cur.execute(statement, params)
    conn.commit()
    conn.close()

    return _admin_client()


def _page(client, path):
    response = client.get(path, headers=HTTPS)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    return response.get_data(as_text=True)


def test_the_signup_payload_actually_reached_the_database(seeded):
    """Guard against every assertion below passing vacuously.

    If signup started rejecting this name, or stopped writing display_name,
    the pages would be clean no matter how they rendered and the protection
    could be deleted with no red test. Require the row to exist.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT full_name, display_name FROM users WHERE username = ?", ("xssprobe",))
    row = cur.fetchone()
    conn.close()
    assert row is not None, "POST /signup did not create the user these tests need"
    assert PAYLOAD in str(row[0]) or PAYLOAD in str(row[1]), (
        "the payload no longer survives to the database, so these tests would "
        "pass against a vulnerable page"
    )


#: The inert form of PAYLOAD in an HTML text node.
ESCAPED = "&lt;img src=x onmouseover=alert(1)"

#: The inert form inside a <script>. /admin/intelligence-graph puts the value in
#: JSON, where the fix is a JSON unicode escape rather than an HTML entity --
#: html_escape there would corrupt the JSON. Same payload, different encoding,
#: because output encoding is contextual.
JSON_ESCAPED = "\\u003cimg src=x onmouseover=alert(1)"


@pytest.mark.parametrize(
    "path,escaped",
    [
        ("/admin/users", ESCAPED),
        ("/admin/pulse-users", ESCAPED),
        ("/admin/account-command", ESCAPED),
        ("/admin/privileges", ESCAPED),
        ("/admin/security", ESCAPED),
        ("/admin/support", ESCAPED),
        ("/admin/intelligence-graph", JSON_ESCAPED),
    ],
)
def test_no_admin_page_emits_the_payload_as_live_markup(seeded, path, escaped):
    body = _page(seeded, path)
    assert PAYLOAD not in body, (
        f"{path} rendered the payload verbatim: an anonymous POST becomes "
        f"script execution in an administrator's session"
    )
    assert LIVE_TAG not in body, (
        f"{path} contains {LIVE_TAG!r} as live markup. A render that escaped "
        f"`<` but not `>` would defeat the check above and still be exploitable"
    )
    # The assertions above are satisfied by a page that never renders the value
    # at all, which is how a seed that silently failed to land would look
    # exactly like a page that was never vulnerable. Requiring the escaped form
    # is what separates the two -- and it doubles as the check that escaping
    # neutralised the value rather than dropping the row, which would hide the
    # abusive account from the administrator looking for it.
    assert escaped in body, (
        f"{path} shows the payload neither live nor escaped, so this case "
        f"proves nothing: either the row is being dropped, or the fixture "
        f"stopped reaching this page and the check is now vacuous"
    )


def test_avatar_url_cannot_break_out_of_its_src_attribute(seeded):
    """The attribute case needs no angle bracket at all.

    `_avatar_cell` builds `<img src="' + clean_html(avatar_url) + '">`.
    clean_html leaves quotes alone, so this payload closed the attribute and
    added an event handler to the same tag -- no `<` involved, which is why a
    tag-stripper is structurally the wrong tool here.
    """
    body = _page(seeded, "/admin/pulse-users")
    assert ATTR_PAYLOAD not in body, (
        "users.avatar_url broke out of its src attribute on /admin/pulse-users"
    )
    # Not `"onmouseover=alert(1) x=" not in body`: that substring is present
    # and harmless once the quotes around it are entities, which is the same
    # trap TAG_OPENINGS exists to avoid. What matters is that both quotes were
    # encoded, so the value cannot have terminated the attribute.
    assert "&#x27; onmouseover=alert(1) x=&#x27;" in body, (
        "the avatar payload is neither escaped nor present -- the cell is "
        "being dropped rather than rendered safely"
    )


def test_intelligence_graph_json_cannot_close_the_script_element(seeded):
    """A different context with a different escape.

    /admin/intelligence-graph embeds `json.dumps(...)` of user display names in
    `const graph={...}` inside a <script>. json.dumps escapes `"` and `\\` but
    not `<`, so a display name containing `</script>` ends the script element
    and everything after it is parsed as markup. html_escape is not the fix
    here -- it would corrupt the JSON -- so `script_json` encodes `<`, `>` and
    `&` as JSON unicode escapes instead.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET display_name = ? WHERE username = ?",
        ("</script><img src=x onmouseover=alert(1)>", "xssprobe"),
    )
    conn.commit()
    conn.close()

    body = _page(seeded, "/admin/intelligence-graph")
    assert "</script><img" not in body, (
        "a display name closed the <script> element on /admin/intelligence-graph"
    )


def test_script_json_escapes_the_characters_that_end_a_script_element():
    """Pin the helper, and pin that it stays valid JSON.

    Encoding as `\\u003c` rather than dropping the character is what lets the
    page keep working: the browser's JSON parser decodes it back to `<`, so the
    admin still sees the real display name in the graph.
    """
    import json

    encoded = bot.script_json({"label": "</script><img src=x>"})
    assert "</script>" not in encoded
    assert "<" not in encoded
    assert json.loads(encoded) == {"label": "</script><img src=x>"}


def test_clean_html_is_still_a_stripper_not_an_escaper():
    """Pin the premise the whole module rests on.

    If `clean_html` is ever hardened into a real escaper this fails, and
    whoever did it gets to decide deliberately whether the render-site escaping
    is still needed. It is -- output encoding is contextual, and clean_html is
    applied at ingest as well as at render -- but that should be a decision
    rather than an accident.
    """
    assert bot.clean_html(PAYLOAD) == PAYLOAD
    assert bot.clean_html(ATTR_PAYLOAD) == ATTR_PAYLOAD
