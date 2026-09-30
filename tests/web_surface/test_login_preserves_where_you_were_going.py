"""Signing in from a product page returns you to that product, not to Home.

The public marketplace PDP renders "Sign in to add to cart" as
`/login?next=<product path>` for anyone it cannot see a session for. The
promise that link makes is Product X -> Login -> Product X. Two separate
things used to break it, and neither was visible from the happy path:

  1. **The retry.** The hidden `next` field in `account.html` read
     `request.args.get('next')`. That is populated on the GET, so a first-try
     login worked and the feature looked finished. But the re-render after a
     failed attempt IS the POST, and a POST to `/login` carries no query
     string -- so the field came back empty and the second attempt landed on
     Home. Mistyping a password once is the ordinary case, not an edge.

  2. **The already-signed-in GET.** `login_page` short-circuited with a bare
     `redirect("/pulse")`, discarding a perfectly good `next`. A member who
     arrives with a valid cookie from another tab has nothing to log into and
     still wants the product.

Both now route through `safe_next_value()`, which is the same validator
`safe_redirect_target` uses. Sharing it is the point: a form field that
sanitised differently from the redirect would be a way to smuggle a target
past the check, so the hostile-value cases below are asserted on both the
emitted field and the resulting redirect.

Run: python3 -m pytest tests/web_surface/test_login_preserves_where_you_were_going.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
# Without a stable key the session cookie set below cannot be read back.
os.environ.setdefault("FLASK_SECRET_KEY", "login-next-preservation-tests")

import bot  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS,
# which would turn every assertion below into a redirect body.
HTTPS = {"X-Forwarded-Proto": "https"}

PRODUCT = "/pulse/marketplace/product/42"

NEXT_FIELD = re.compile(
    r"<input[^>]*name=\"next\"[^>]*value=\"([^\"]*)\"[^>]*>"
)
CSRF_FIELD = re.compile(
    r"<input[^>]*name=\"csrf_token\"[^>]*value=\"([^\"]*)\"[^>]*>"
)

#: Every one of these must be refused. `//host` is protocol-relative and
#: navigates off-site despite starting with a slash; the absolute form is the
#: textbook open redirect; `javascript:` is the XSS shape; `/admin` is
#: site-relative and therefore *allowed* by the path check, so it is asserted
#: separately below rather than smuggled in here.
HOSTILE = [
    "//evil.example.com/x",
    "https://evil.example.com/x",
    "http://evil.example.com/x",
    "javascript:alert(1)",
    "\\\\evil.example.com/x",
    "",
]


@pytest.fixture(scope="module")
def anon():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def member():
    """A signed-in client, for the already-authenticated GET short-circuit."""
    with bot.webhook_app.app_context():
        bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("gracehopper", "grace@example.com", "x"),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def login_page(client, next_value=None):
    path = "/login"
    if next_value is not None:
        from urllib.parse import quote

        path = f"/login?next={quote(next_value, safe='')}"
    response = client.get(path, headers=HTTPS)
    assert response.status_code == 200, (
        f"{path} returned HTTP {response.status_code}; the login form has to "
        "render for any of this to mean anything"
    )
    return response.get_data(as_text=True)


def next_values(html):
    return NEXT_FIELD.findall(html)


# ---------------------------------------------------------------------------
# The intent survives the round trip
# ---------------------------------------------------------------------------


def test_the_login_form_carries_the_requested_destination(anon):
    values = next_values(login_page(anon, PRODUCT))
    assert values, "the login form lost its hidden next field entirely"
    assert set(values) == {PRODUCT}


def test_a_plain_login_emits_an_empty_field_not_a_hardcoded_home(anon):
    # An absent `next` must stay absent. Defaulting it to "/pulse" here would
    # be indistinguishable, downstream, from a deliberate request to go to the
    # feed -- and would mean this field could never be read as "no intent".
    assert set(next_values(login_page(anon))) == {""}


def test_the_destination_survives_a_failed_attempt(anon):
    """The defect. A POST has no query string, so args-only reading loses it."""
    html = login_page(anon, PRODUCT)
    csrf = CSRF_FIELD.search(html)
    assert csrf, "could not read a csrf token out of the login form"

    response = anon.post(
        "/login",
        headers=HTTPS,
        data={
            "csrf_token": csrf.group(1),
            "email": "nobody@example.com",
            "password": "wrong-password",
            "terms_accepted": "on",
            "next": PRODUCT,
        },
    )
    body = response.get_data(as_text=True)
    # Whatever the failure mode (bad credentials, security challenge), the page
    # re-renders rather than redirecting, and that re-render is what must keep
    # the intent.
    assert response.status_code in (200, 400, 403), response.status_code
    values = next_values(body)
    assert values, "the re-rendered login form has no next field at all"
    assert set(values) == {PRODUCT}, (
        "a failed login attempt dropped the destination; the retry would land "
        "on Home"
    )


def test_an_already_signed_in_visitor_is_sent_to_the_destination(member):
    response = member.get(f"/login?next={PRODUCT}", headers=HTTPS)
    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["Location"].endswith(PRODUCT), (
        response.headers["Location"]
    )


def test_an_already_signed_in_visitor_with_no_destination_still_goes_home(member):
    # The short-circuit's original behaviour, preserved: honouring `next` must
    # not cost the default.
    response = member.get("/login", headers=HTTPS)
    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["Location"].endswith("/pulse")


# ---------------------------------------------------------------------------
# Untrusted input (brief §45)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_destination_is_never_echoed_into_the_form(anon, hostile):
    values = next_values(login_page(anon, hostile))
    assert values
    assert set(values) == {""}, (
        f"{hostile!r} was echoed back into the form; a value that reaches the "
        "field but not the redirect is the drift this shares a validator to "
        "prevent"
    )


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_destination_never_becomes_a_redirect(member, hostile):
    response = member.get(f"/login?next={hostile}", headers=HTTPS)
    assert response.status_code in (301, 302, 303, 307, 308)
    location = response.headers["Location"]
    assert "evil.example.com" not in location, location
    assert "javascript:" not in location.lower(), location
    assert location.endswith("/pulse"), location


def test_the_validator_agrees_with_itself_on_every_hostile_value():
    # The field and the redirect are only safe together if one cannot accept
    # what the other rejects. Asserted directly on the pair, so the guarantee
    # does not depend on which routes happen to be tested above.
    for hostile in HOSTILE:
        with bot.webhook_app.test_request_context(
            f"/login?next={hostile}", headers=HTTPS
        ):
            assert bot.safe_next_value() == ""
            assert bot.safe_redirect_target("pulse_page") == "/pulse"


def test_a_site_relative_path_is_allowed_including_admin():
    # Stated rather than asserted-against, because it is a deliberate choice
    # and not an oversight: `next` only ever produces a site-relative path, and
    # `/admin` is guarded by its own authorisation on arrival. Refusing it here
    # would be security theatre that breaks a real admin login flow.
    with bot.webhook_app.test_request_context("/login?next=/admin", headers=HTTPS):
        assert bot.safe_next_value() == "/admin"


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_field_is_populated_by_the_shared_validator(anon, monkeypatch):
    # If `account.html` went back to reading `request.args` directly, the
    # assertions above would still pass on the GET -- the args are right there.
    # Neutering the validator is what tells them apart: only a template fed
    # from `render_account_page` follows it.
    assert set(next_values(login_page(anon, PRODUCT))) == {PRODUCT}

    monkeypatch.setattr(bot, "safe_next_value", lambda: "/sentinel")
    assert set(next_values(login_page(anon, PRODUCT))) == {"/sentinel"}


def test_mutation_the_short_circuit_is_not_a_hardcoded_path(member, monkeypatch):
    monkeypatch.setattr(bot, "safe_redirect_target", lambda *a, **k: "/sentinel")
    response = member.get(f"/login?next={PRODUCT}", headers=HTTPS)
    assert response.headers["Location"].endswith("/sentinel")
