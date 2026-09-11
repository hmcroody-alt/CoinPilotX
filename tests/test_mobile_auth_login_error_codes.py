"""/api/mobile/auth/login must name *which* refusal it is, without naming the account.

The native client keys its copy on the rejection code (`errors:auth.*` in
LoginScreen.tsx). Before these codes existed the 401 paths sent no discriminator
at all, so a security challenge -- which the server had already decided and
logged as `login_challenge_required` -- reached the user as "email or password
is incorrect", and they retried a password that was never wrong.

The line these tests hold is where the naming stops. `/login` (the HTML surface)
answers unknown-identifier and wrong-password with one identical sentence so the
endpoint cannot be used to enumerate accounts. A code is machine-readable, so
splitting it here would hand an unauthenticated caller exactly the distinction
the web surface refuses to give -- in a form that is *easier* to scrape than
prose. Both therefore share `invalid_credentials`, and that sameness is asserted
rather than left to convention.

The states that are named are safe to name: a challenge, a rate limit, an
unconfirmed email and a restriction each either require a correct password to
observe or are already visible to the account's owner, and the HTML surface
distinguishes all four today.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mobile_login_codes_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import cache_engine, pulse_security_core  # noqa: E402
from services import db as db_service  # noqa: E402

PASSWORD = "MobileLoginCodes!123"
CREDENTIAL_MESSAGE = "Email or password is incorrect."


def _use_module_database():
    """Re-point the process at this module's temp database and rebuild schema.

    ``services.db`` resolves ``DATABASE_URL`` on every connection and pytest
    imports every selected module before running any test, so a sibling module
    that sets its own path at import time leaves the environment pointing at
    *its* database by the time these tests run. ``init_db`` also short-circuits
    on ``INIT_DB_COMPLETED``, so clearing the flag is what makes this file
    order-independent rather than passing in one particular argument order.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _reset_failed_login_velocity():
    """Clear both security gates' memory of the *previous* test's requests.

    Every test here signs in wrongly on purpose, all from one IP and one email
    domain, and two independent limiters count that:

    * `login_security_preflight` counts recent `auth_events` rows, and answers
      `login_challenge_required` once the velocity threshold is passed.
    * the `pulse_security_core` middleware allows ten requests per five minutes
      to this path and answers a bare 429 with no `error_code` at all.

    Either one makes a later test fail about a state it was not exercising, and
    both are per-process rather than per-test. This is why the file failed as a
    whole while every test in it passed in isolation -- worth resetting
    explicitly rather than by keeping the test count under ten, which would
    decay the first time someone adds a case.
    """
    conn = db_service.connect()
    cur = conn.cursor()
    for table in ("auth_events", "failed_login_controls", "failed_login_safe_list"):
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception:
            # Absent on an older schema; nothing to reset.
            pass
    conn.commit()
    conn.close()
    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()


def _make_user(*, confirmed=True, login_enabled=1):
    email = f"login-codes-{secrets.token_hex(6)}@example.com"
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?)
        """,
        (
            f"logincodes_{secrets.token_hex(4)}",
            "Login Codes",
            "Login Codes",
            email,
            bot.generate_password_hash(PASSWORD),
            1 if confirmed else 0,
            login_enabled,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return email


def _blocked_gate(*, challenge):
    """What ``login_security_preflight`` returns when it refuses a login.

    Patched rather than provoked. Reaching the real gate means driving the
    failed-login velocity counters past their thresholds, which couples this
    file to those thresholds and to wall-clock windows; what is under test here
    is only the route's translation of a refusal into wire fields.
    """
    return {
        "allowed": False,
        "status": 403 if challenge else 429,
        "message": "Security challenge required." if challenge else "Too many failed login attempts.",
        "challenge": {"question": "2 + 2?", "token": "abc"} if challenge else None,
        "reason": "suspicious_failed_login_velocity" if challenge else "active_control",
    }


class MobileLoginErrorCodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        _reset_failed_login_velocity()

    def login(self, identifier, password=PASSWORD):
        return self.client.post(
            "/api/mobile/auth/login",
            json={"identifier": identifier, "password": password},
        )

    def assert_code(self, response, status, code):
        body = response.get_json() or {}
        self.assertEqual(response.status_code, status, body)
        self.assertEqual(body.get("error_code"), code, body)
        # `error` is the older spelling of the same field and the one the
        # shipped client falls back to. They must never disagree: the fallback
        # would then silently select different copy than the primary read.
        self.assertEqual(body.get("error"), code, body)
        self.assertIs(body.get("ok"), False, body)
        return body

    def test_unknown_identifier_is_invalid_credentials(self):
        body = self.assert_code(
            self.login(f"nobody-{secrets.token_hex(6)}@example.com"), 401, "invalid_credentials"
        )
        self.assertEqual(body.get("message"), CREDENTIAL_MESSAGE)

    def test_wrong_password_is_invalid_credentials(self):
        email = _make_user()
        body = self.assert_code(self.login(email, "NotThePassword!9"), 401, "invalid_credentials")
        self.assertEqual(body.get("message"), CREDENTIAL_MESSAGE)

    def test_unknown_identifier_and_wrong_password_are_indistinguishable(self):
        """The anti-enumeration guarantee, asserted as an equality.

        Any future field that splits these two -- a different code, a different
        status, a different sentence -- turns this endpoint into an account
        oracle for an unauthenticated caller. Compared whole rather than
        field-by-field so a *newly added* discriminator fails too.
        """
        email = _make_user()
        unknown = self.login(f"nobody-{secrets.token_hex(6)}@example.com")
        wrong = self.login(email, "NotThePassword!9")

        self.assertEqual(unknown.status_code, wrong.status_code)
        unknown_body = unknown.get_json() or {}
        wrong_body = wrong.get_json() or {}
        # trace_id is per-request by construction and carries no account state.
        unknown_body.pop("trace_id", None)
        wrong_body.pop("trace_id", None)
        self.assertEqual(unknown_body, wrong_body)

    def test_unconfirmed_email_is_named(self):
        email = _make_user(confirmed=False)
        body = self.assert_code(self.login(email), 403, "email_not_confirmed")
        self.assertEqual(body.get("message"), "Please confirm your email before logging in.")

    def test_restricted_account_is_named(self):
        email = _make_user(login_enabled=0)
        body = self.assert_code(self.login(email), 403, "account_restricted")
        self.assertTrue(body.get("message"))

    def test_challenge_required_is_named_and_carries_the_challenge(self):
        with patch.object(bot, "login_security_preflight", return_value=_blocked_gate(challenge=True)):
            body = self.assert_code(self.login(_make_user()), 403, "login_challenge_required")
        self.assertEqual(body.get("challenge", {}).get("question"), "2 + 2?")

    def test_rate_limited_is_named(self):
        with patch.object(bot, "login_security_preflight", return_value=_blocked_gate(challenge=False)):
            body = self.assert_code(self.login(_make_user()), 429, "login_rate_limited")
        self.assertEqual(body.get("message"), "Too many failed login attempts.")

    def test_challenge_on_a_real_account_is_not_reported_as_bad_credentials(self):
        """The original bug, at the boundary where it actually happened.

        The gate is consulted a second time *after* the password is found wrong,
        so a correct-password user tripping the velocity counter and a
        wrong-password user tripping it both arrive here. Neither may be told
        the password was the problem.
        """
        email = _make_user()
        with patch.object(bot, "login_security_preflight", return_value=_blocked_gate(challenge=True)):
            body = self.assert_code(self.login(email, "NotThePassword!9"), 403, "login_challenge_required")
        self.assertNotEqual(body.get("message"), CREDENTIAL_MESSAGE)

    def test_every_failure_code_is_distinct_per_state(self):
        """Mutual exclusion across the whole surface.

        Each assertion above passes if two states collapse onto one code; only
        comparing the set catches that. `invalid_credentials` appears once here
        because its two producers are deliberately one state.
        """
        confirmed = _make_user()
        codes = [
            (self.login(f"nobody-{secrets.token_hex(6)}@example.com"), "invalid_credentials"),
            (self.login(confirmed, "NotThePassword!9"), "invalid_credentials"),
            (self.login(_make_user(confirmed=False)), "email_not_confirmed"),
            (self.login(_make_user(login_enabled=0)), "account_restricted"),
        ]
        for response, expected in codes:
            self.assertEqual((response.get_json() or {}).get("error_code"), expected)

        with patch.object(bot, "login_security_preflight", return_value=_blocked_gate(challenge=True)):
            challenge = (self.login(confirmed).get_json() or {}).get("error_code")
        with patch.object(bot, "login_security_preflight", return_value=_blocked_gate(challenge=False)):
            limited = (self.login(confirmed).get_json() or {}).get("error_code")

        distinct = {"invalid_credentials", "email_not_confirmed", "account_restricted", challenge, limited}
        self.assertEqual(len(distinct), 5, distinct)
        self.assertNotIn(None, distinct)

    def test_a_correct_login_still_succeeds(self):
        """Guards the refactor, not the codes.

        The three gate branches used to rebind the name `payload`, which also
        held the parsed request body that the success path reads back when it
        mints tokens. Collapsing them into a helper removed that shadowing; this
        proves the success path still works.
        """
        response = self.login(_make_user())
        body = response.get_json() or {}
        self.assertEqual(response.status_code, 200, body)
        self.assertIs(body.get("ok"), True, body)
        self.assertNotIn("error_code", body)


if __name__ == "__main__":
    unittest.main()
