"""Recovery must refuse input that is not an address, and confirmation state must sit behind the password.

Two separate lines, both drawn by the same rule: say everything the account's
owner already knows, and nothing that an anonymous caller could use to learn
whether an account exists.

*Recovery.* `/api/mobile/auth/recover` answered "If an account exists, password
recovery has been sent" to anything, including strings with no `@` in them. That
is not enumeration protection, it is a false promise -- no lookup ran and no mail
could ever be addressed. All 102 `forgot_password_invalid_email` events in
production came from this one route, every one with a masked address of "Not
set", which only happens when the input contains no `@`. The tests here pin the
distinction: a syntax refusal is a judgement about the caller's own keystrokes
and is safe to make, while a *parsing* address must produce the identical generic
answer whether or not it matches an account.

*Confirmation ordering.* Both login surfaces checked `email_verified` before
verifying the password. `email_not_confirmed` names a real account, so answering
it first let anyone submit an address with an empty password and learn whether it
was registered -- the exact distinction the shared `invalid_credentials` 401
refuses to make. The message is safe only once a correct password has been shown.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="auth_recovery_order_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import cache_engine, pulse_security_core  # noqa: E402
from services import db as db_service  # noqa: E402

PASSWORD = "RecoveryOrder!123"
GENERIC_RECOVERY = "If an account exists, password recovery has been sent."


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _reset_limiters():
    """Clear all three per-process gates between tests.

    `login_security_preflight` counts recent `auth_events`; the
    `pulse_security_core` middleware allows ten requests per five minutes per
    path; `basic_abuse_guard` holds its own dict and allows six recovery
    attempts per five minutes. None is per-test, so without this the file fails
    as a whole while every test in it passes alone.
    """
    conn = db_service.connect()
    cur = conn.cursor()
    for table in ("auth_events", "failed_login_controls", "failed_login_safe_list"):
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    pulse_security_core._RATE_BUCKETS.clear()
    cache_engine._MEMORY.clear()
    bot.RATE_LIMIT_BUCKETS.clear()


def _make_user(*, confirmed=True):
    email = f"recovery-{secrets.token_hex(6)}@example.com"
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 1, ?, ?, ?)
        """,
        (
            f"recovery_{secrets.token_hex(4)}",
            "Recovery Order",
            "Recovery Order",
            email,
            bot.generate_password_hash(PASSWORD),
            1 if confirmed else 0,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return email


class AuthCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        _reset_limiters()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()

    def _recover(self, email):
        return self.client.post("/api/mobile/auth/recover", json={"email": email})

    def _login(self, identifier, password, ip="203.0.113.7"):
        return self.client.post(
            "/api/mobile/auth/login",
            json={"identifier": identifier, "password": password},
            environ_overrides={"REMOTE_ADDR": ip},
        )


class RecoveryRefusesInputThatIsNotAnAddress(AuthCase):

    def test_a_input_with_no_at_sign_is_refused(self):
        response = self._recover("roody")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get("error_code"), "invalid_email")

    def test_b_the_refusal_does_not_claim_mail_was_sent(self):
        body = self._recover("roody").get_json()
        self.assertNotIn("password recovery has been sent", (body.get("message") or ""))
        self.assertNotIn("password recovery has been sent", (body.get("error") or ""))

    def test_c_several_malformed_shapes_are_all_refused(self):
        for value in ("roody", "roody@", "@example.com", "roody example.com", "   ", ""):
            with self.subTest(value=value):
                _reset_limiters()
                self.assertEqual(self._recover(value).status_code, 400)


class RecoveryStaysEnumerationSafeForRealAddresses(AuthCase):

    def test_d_an_address_that_parses_but_matches_nothing_gets_the_generic_answer(self):
        response = self._recover(f"absent-{secrets.token_hex(5)}@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get("message"), GENERIC_RECOVERY)

    def test_e_a_known_address_gets_a_byte_identical_answer(self):
        known = _make_user()
        _reset_limiters()
        hit = self._recover(known)
        _reset_limiters()
        miss = self._recover(f"absent-{secrets.token_hex(5)}@example.com")
        self.assertEqual(hit.status_code, miss.status_code)
        self.assertEqual(hit.get_json(), miss.get_json())

    def test_f_case_and_whitespace_reach_the_same_account(self):
        known = _make_user()
        _reset_limiters()
        response = self._recover(f"  {known.upper()}  ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get("message"), GENERIC_RECOVERY)


class ConfirmationStateSitsBehindThePassword(AuthCase):

    def test_g_unconfirmed_account_with_a_wrong_password_is_only_invalid_credentials(self):
        email = _make_user(confirmed=False)
        response = self._login(email, "WrongPassword!987")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get("error_code"),
                         bot.MOBILE_LOGIN_INVALID_CREDENTIALS)

    def test_h_an_empty_password_cannot_reveal_that_the_account_exists(self):
        # The oracle in its cheapest form: no password at all. A registered but
        # unconfirmed address and an address that was never registered must be
        # indistinguishable on the wire.
        email = _make_user(confirmed=False)
        registered = self._login(email, "")
        _reset_limiters()
        absent = self._login(f"absent-{secrets.token_hex(5)}@example.com", "",
                             ip="203.0.113.8")
        self.assertEqual(registered.status_code, absent.status_code)
        self.assertEqual(registered.get_json().get("error_code"),
                         absent.get_json().get("error_code"))

    def test_i_the_correct_password_does_surface_the_unconfirmed_state(self):
        email = _make_user(confirmed=False)
        response = self._login(email, PASSWORD)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get("error_code"), "email_not_confirmed")

    def test_j_a_confirmed_account_still_signs_in(self):
        # The control. Without it every assertion above would keep passing if
        # login broke outright.
        email = _make_user(confirmed=True)
        response = self._login(email, PASSWORD)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])

    def _web_login(self, email, password, ip="203.0.113.9"):
        """POST the real web form.

        Two gates sit in front of the branch under test: `verify_csrf` and the
        terms checkbox. Skipping either makes the request fail before it reaches
        the ordering at all, which is how the first draft of this test passed
        against the unfixed code.
        """
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        return self.client.post(
            "/login",
            data={"email": email, "password": password, "terms_accepted": "on",
                  "csrf_token": "test-csrf-token"},
            follow_redirects=True,
            environ_overrides={"REMOTE_ADDR": ip},
        )

    def test_k_the_web_surface_orders_the_two_checks_the_same_way(self):
        email = _make_user(confirmed=False)
        body = self._web_login(email, "WrongPassword!987").get_data(as_text=True).lower()
        self.assertNotIn("confirm your email", body)
        self.assertIn("incorrect", body)

    def test_l_the_web_surface_still_asks_for_confirmation_behind_the_password(self):
        # The control for test_k. Without it, test_k passes just as well when the
        # request never reaches the branch -- which is exactly what CSRF and the
        # terms checkbox were doing to it.
        email = _make_user(confirmed=False)
        body = self._web_login(email, PASSWORD).get_data(as_text=True).lower()
        self.assertIn("confirm your email", body)


if __name__ == "__main__":
    unittest.main()
