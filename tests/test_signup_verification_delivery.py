"""Signing up again with an unverified address must actually send the mail.

Half the accounts in production are unverified, and 26 of 51 verification tokens
expired without ever being redeemed. One route explains a good deal of that.

Someone who never receives the verification email does the obvious thing and
signs up again. `create_account` refuses -- the address is taken -- and the
handler recognised the existing unverified account and answered
`requires_email_confirmation: true`, which the app renders as "check your email".
No second email was sent. The user waits for mail that was never addressed, and
the account stays unverified permanently, because re-signing-up is the only
recovery most people will try.

The response also contradicted itself: its message said delivery had failed while
`email_delivery_failed` said it had not. That flag was `bool(trace_id)` -- a fact
about the caller's bookkeeping, not about the send -- and it is the flag the
client reads to decide whether to show the "we couldn't send it, resend below"
copy. So the one path where nothing had been sent was also the one path that
suppressed the resend prompt.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="signup_delivery_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import cache_engine, pulse_security_core  # noqa: E402
from services import db as db_service  # noqa: E402

PASSWORD = "SignupDelivery!123"


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _reset_limiters():
    """Clear all three per-process gates. See the note in
    tests/test_auth_recovery_and_confirmation_order.py -- `/signup` is capped at
    eight attempts per five minutes by `basic_abuse_guard`, which is well under
    the number of signups this file performs."""
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


def _make_unverified(email):
    now = bot.datetime.now().isoformat()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified,
         account_status, login_enabled, access_enabled, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, 'active', 1, 1, ?, ?, ?)
        """,
        (
            f"signup_{secrets.token_hex(4)}",
            "Signup Delivery",
            "Signup Delivery",
            email,
            bot.generate_password_hash(PASSWORD),
            now,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


class SignupDeliveryCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        _reset_limiters()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        self.email = f"stranded-{secrets.token_hex(6)}@example.com"
        _make_unverified(self.email)

    def _signup(self, email, ip="203.0.113.21"):
        return self.client.post(
            "/api/mobile/auth/register",
            json={
                "full_name": "Signup Delivery",
                "email": email,
                "password": PASSWORD,
                "age_confirmed": True,
            },
            environ_overrides={"REMOTE_ADDR": ip},
        )

    def test_a_re_signup_on_an_unverified_address_sends_the_mail(self):
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "abc123"}) as sender:
            response = self._signup(self.email)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sender.call_count, 1)
        # The existing account, not a new one -- the point is that nothing was
        # duplicated and the mail still went out.
        self.assertEqual(sender.call_args.args[0].get("email"), self.email)

    def test_b_and_tells_the_user_to_check_their_mail_only_then(self):
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "abc123"}):
            body = self._signup(self.email).get_json()
        self.assertTrue(body.get("requires_email_confirmation"))
        self.assertFalse(body.get("email_delivery_failed"))
        self.assertIn("check your email", (body.get("message") or "").lower())

    def test_c_a_refused_send_is_reported_as_a_refused_send(self):
        # The control for test_b. Before the fix this path reported
        # `email_delivery_failed: false` no matter what happened, because the
        # flag was derived from the trace id rather than the outcome.
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": False, "trace_id": "def456"}):
            body = self._signup(self.email).get_json()
        self.assertTrue(body.get("requires_email_confirmation"))
        self.assertTrue(body.get("email_delivery_failed"))
        self.assertNotIn("check your email", (body.get("message") or "").lower())
        self.assertIn("could not be delivered", (body.get("message") or "").lower())

    def test_d_the_message_and_the_flag_never_disagree(self):
        # The defect was not either field alone -- it was that one said delivery
        # failed while the other said it had not, in the same body.
        for ok in (True, False):
            with self.subTest(ok=ok):
                _reset_limiters()
                with patch.object(bot, "send_account_confirmation_email", return_value={"ok": ok, "trace_id": "t"}):
                    body = self._signup(self.email).get_json()
                claims_failure = "could not be delivered" in (body.get("message") or "").lower()
                self.assertEqual(claims_failure, bool(body.get("email_delivery_failed")))

    def test_e_re_signup_still_does_not_create_a_second_account(self):
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "t"}):
            self._signup(self.email)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE email=?", (self.email,))
        count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_f_a_verified_address_is_still_refused(self):
        # The control that keeps the fix from turning signup into a resend
        # oracle for confirmed accounts. Only the unverified branch may resend.
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET email_verified=1 WHERE email=?", (self.email,))
        conn.commit()
        conn.close()
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "t"}) as sender:
            response = self._signup(self.email)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(sender.call_count, 0)

    def test_g_the_web_signup_form_resends_too(self):
        # The same dead end existed on the server-rendered form, which is the
        # surface the verification link itself lands on.
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": True, "trace_id": "t"}) as sender:
            body = self.client.post(
                "/signup",
                data={
                    "full_name": "Signup Delivery",
                    "email": self.email,
                    "password": PASSWORD,
                    "age_confirmed": "on",
                    "terms_accepted": "on",
                    "csrf_token": "test-csrf-token",
                },
                follow_redirects=True,
                environ_overrides={"REMOTE_ADDR": "203.0.113.22"},
            ).get_data(as_text=True).lower()
        self.assertEqual(sender.call_count, 1)
        self.assertIn("check your email", body)

    def test_h_the_helper_defaults_to_not_claiming_a_failure(self):
        # A delivery failure is something you observe, never something you get
        # by omission. The old default came from `bool(trace_id)`.
        body = bot.unverified_signup_delivery_response("someone@example.com")
        self.assertFalse(body["email_delivery_failed"])
        body = bot.unverified_signup_delivery_response("someone@example.com", "m", "trace-id-present")
        self.assertFalse(body["email_delivery_failed"])
        body = bot.unverified_signup_delivery_response("someone@example.com", "m", "", delivery_failed=True)
        self.assertTrue(body["email_delivery_failed"])

    def test_i_the_resend_action_is_always_offered(self):
        # Whatever else the body says, the client needs somewhere to send the
        # user next; this is the contract `VerifyEmailStep` renders against.
        with patch.object(bot, "send_account_confirmation_email", return_value={"ok": False, "trace_id": "t"}):
            body = self._signup(self.email).get_json()
        self.assertEqual(body["actions"]["resend_email"], "/api/mobile/auth/resend-confirmation")
        self.assertEqual(body["actions"]["change_email_address"], "/api/mobile/auth/change-confirmation-email")


if __name__ == "__main__":
    unittest.main()
