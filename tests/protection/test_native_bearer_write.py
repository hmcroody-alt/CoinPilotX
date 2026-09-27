"""The native write gate, driven with the credential the app actually sends.

`tests/protection/test_csrf_contract.py` already asserts that the bearer
exemption fires. It asserts it like this::

    if bearer:
        flask.g.mobile_access_user_id = 8

That is the *conclusion* of `bearer_is_csrf_safe()`, assigned directly. The
production path is the other branch of that function -- the one taken when the
flag is **not** set, which is every native request, because
`bot.account_user_id()` only sets it by reaching its bearer branch and the
re-verification inside `csrf.bearer_is_csrf_safe()` is what actually decides.
Header parsing, HMAC verification, claim validity, the
`mobile_security_sessions` lookup and the device-hash comparison were all
downstream of a line no test ever ran.

The cost was a production incident. A seller with 58 products in a CJ import
cart pressed "Import & publish" and got "That import didn't run." Every
dropshipping **write** was answering `403 {"code":"csrf"}` before the importer
was reached, while every **read** succeeded -- so the cart rendered perfectly
and the button did nothing. Measured in the production log: import 403, refresh
200, import 403 again, 238ms apart.

So this file mints a real access token through `issue_mobile_security_tokens()`
-- the same function `/api/mobile/auth/refresh` calls, with no monkeypatching of
`_bot`, no injected verifier and no pre-set flag -- and asks the live gate. A
test that sets the answer cannot fail; this one can.

Run: python3 -m pytest tests/protection/test_native_bearer_write.py
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `bot` resolves DATABASE_URL at import and never re-reads it, and importing it
# runs `init_db()`. Both have to be settled before the import below, or the test
# creates several hundred tables in whichever database the developer exported.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(
    prefix="native-bearer-protection-", suffix=".db"
)[1]
os.environ.setdefault("FLASK_SECRET_KEY", "native-bearer-protection")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

import bot  # noqa: E402
from services import business_os_commerce_routes as commerce  # noqa: E402
from services import csrf  # noqa: E402
from services import db as db_service  # noqa: E402

#: What the app sends. `pulseApi` sets this on the refresh call explicitly, and
#: it is the only input to the device fingerprint when no device id is present
#: -- which is what production shows for every live session
#: (`device_id_present: false`).
NATIVE_UA = "PulseSoc/1.0.3 (iPhone; iOS 26.0) PulseSocNative"

#: A bearer-exempt write path. Any would do; this is the one that failed.
WRITE_PATH = "/api/business-os/dropshipping/connections/c1/import"


def create_seller():
    """A live account, inserted the way the auth suites insert one.

    A plain function rather than a pytest fixture, because this file has to run
    two ways. `scripts/protection/run_protection_suite.py` executes every
    `tests/protection/test_*.py` as a script and calls each `test_*` with no
    arguments, so a fixture parameter would make every check error out -- and
    the suite is what noticed, by refusing a file that ran zero checks. There is
    no teardown to hang off a fixture anyway: each call inserts its own row into
    a per-process temp database that is thrown away with the process.
    """
    conn = db_service.connect()
    cur = conn.cursor()
    now = bot.datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, 'active', 1, 1, ?, ?, ?)
        """,
        (
            f"bearerwrite_{secrets.token_hex(4)}",
            "Bearer Write",
            "Bearer Write",
            f"bearer-write-{secrets.token_hex(6)}@example.com",
            bot.generate_password_hash("Protection-Suite-1!"),
            now, now, now,
        ),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(user_id)


def mint_bearer(user_id):
    """An access token minted exactly as ``/api/mobile/auth/refresh`` mints one.

    Inside its own request context, because the device fingerprint the token
    carries in its ``dh`` claim is derived from the *issuing* request's headers
    and is later compared against the stored session row. Minting it under a
    different shape than the app refreshes under would prove nothing about
    production.
    """
    with bot.app.test_request_context(
        "/api/mobile/auth/refresh", method="POST",
        headers={"User-Agent": NATIVE_UA, "X-PulseSoc-Platform": "ios"},
    ):
        payload = bot.issue_mobile_security_tokens(
            {"user_id": user_id},
            {"source": "native_automatic_refresh", "platform": "ios"},
        )
    assert payload.get("access_token"), "issue_mobile_security_tokens minted nothing"
    return payload["access_token"]


def native_write_context(user_id, token, **overrides):
    """The request the app sends: session cookie *and* bearer, no CSRF token.

    Both credentials, because that is the shape that made the bearer exemption
    dead code the first time -- `account_user_id()` short-circuited on the
    cookie and never set the flag the gate read.
    """
    headers = {"User-Agent": NATIVE_UA}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(overrides.pop("headers", {}))
    return bot.app.test_request_context(
        WRITE_PATH, method="POST", data="{}",
        content_type="application/json", headers=headers, **overrides,
    )


def test_a_real_native_bearer_satisfies_the_write_gate():
    """The whole incident, in one assertion.

    No `flask.g.mobile_access_user_id` is set here. If the re-verification path
    refuses a token the server itself minted seconds earlier, every native
    write on the platform is refused, and this is the test that says so.
    """
    seller = create_seller()
    token = mint_bearer(seller)
    with native_write_context(seller, token):
        flask.session["account_user_id"] = seller
        allowed = commerce._csrf_ok()
        detail = csrf.refusal_detail()
    assert allowed, (
        "A bearer this server minted, for a live session, was refused by the "
        f"write gate. Every native write is 403. refusal_detail={detail}"
    )
    assert detail["bearer"] == csrf.BEARER_OK


def test_the_refusal_says_which_check_the_bearer_failed():
    """Each cause reports itself, because each has a different repair.

    This is what the seller's 403 could not say. `absent` is fixed in
    `pulseApi`, `expired` means the client's refresh is not landing,
    `no-live-session` is a server-side revocation, `bad-signature` is a key
    mismatch between workers. One opaque code for all four is why the incident
    could not be diagnosed from the outside.
    """
    seller = create_seller()
    token = mint_bearer(seller)
    cases = {
        None: csrf.BEARER_ABSENT,
        "not-a-token": "malformed",
        f"{token[:-4]}beef": "bad-signature",
    }
    seen = {}
    for supplied, expected in cases.items():
        with native_write_context(seller, supplied):
            flask.session["account_user_id"] = seller
            assert not commerce._csrf_ok(), f"{supplied!r} was accepted as a bearer"
            seen[expected] = csrf.refusal_detail()["bearer"]
    assert seen == {expected: expected for expected in cases.values()}, seen


def test_a_wrong_scheme_is_not_read_as_a_bearer():
    """`Basic` is not `Bearer`, and the refusal says which."""
    seller = create_seller()
    with native_write_context(seller, None, headers={"Authorization": "Basic abc"}):
        flask.session["account_user_id"] = seller
        assert not commerce._csrf_ok()
        assert csrf.refusal_detail()["bearer"] == csrf.BEARER_NOT_BEARER


def test_the_diagnostic_carries_no_secrets():
    """A refusal is logged in production, so it must be safe to log.

    Asserted against the token itself rather than against a list of key names:
    a future field holding the value would pass a key-name check.
    """
    seller = create_seller()
    token = mint_bearer(seller)
    with native_write_context(seller, token):
        flask.session[csrf.CSRF_SESSION_KEY] = "a-session-csrf-token"
        flask.session["account_user_id"] = seller
        rendered = repr(csrf.refusal_detail())
    assert token not in rendered
    assert "a-session-csrf-token" not in rendered
    assert str(seller) not in rendered.replace("'", "")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
