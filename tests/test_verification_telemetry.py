"""The auth journey had a hole exactly where the verification story lives.

The journey that prompted this investigation reads: three `mobile_login_unconfirmed`,
two `forgot_password_invalid_email`, `signup_started`, `signup_completed`,
`login_failed`. Every one of those is about the user knocking on the door. Not
one of them says whether the key was ever posted.

Nothing logged an auth event when a confirmation email was sent, when it failed
to send, when a verification link was clicked, or when one was refused. So
"unconfirmed login, three times" could equally mean the mail never went out, the
mail went out and bounced, or the mail arrived and was ignored -- and those have
three different fixes. The historical analysis for this mission had to be
reconstructed from `email_logs` and `email_verification_tokens` for exactly that
reason.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="verif_telemetry_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


class VerificationTelemetryCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        self.email = f"telemetry-{secrets.token_hex(6)}@example.com"
        now = bot.datetime.now().isoformat()
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM auth_events")
        cur.execute(
            "INSERT INTO users (username, display_name, full_name, email, password_hash, email_verified, "
            "account_status, login_enabled, access_enabled, signup_time, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 'active', 1, 1, ?, ?, ?)",
            (
                f"telem_{secrets.token_hex(4)}",
                "Telemetry",
                "Telemetry",
                self.email,
                bot.generate_password_hash("Telemetry!12345"),
                now,
                now,
                now,
            ),
        )
        conn.commit()
        cur.execute("SELECT user_id FROM users WHERE email=?", (self.email,))
        self.user_id = cur.fetchone()[0]
        conn.close()

    def _events(self):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT event_type, status, email, details FROM auth_events ORDER BY id")
        rows = [tuple(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _types(self):
        return [r[0] for r in self._events()]

    def _user(self):
        return bot.load_account_by_id(self.user_id)

    def test_a_a_sent_confirmation_email_is_recorded(self):
        with patch.object(bot, "send_email_verification", return_value=True):
            bot.send_account_confirmation_email(self._user(), source="signup")
        self.assertIn("verification_email_sent", self._types())

    def test_b_a_refused_confirmation_email_is_recorded_as_refused(self):
        # The whole point of the event is to tell these two apart. A failure
        # recorded as a send is worse than no event at all.
        with patch.object(bot, "send_email_verification", return_value=False):
            bot.send_account_confirmation_email(self._user(), source="signup")
        types = self._types()
        self.assertIn("verification_email_failed", types)
        self.assertNotIn("verification_email_sent", types)

    def test_c_the_source_distinguishes_a_first_send_from_a_resend(self):
        # Without this, "we sent it four times" and "we sent it once" look the
        # same, and the second is a delivery bug while the first is not.
        with patch.object(bot, "send_email_verification", return_value=True):
            bot.send_account_confirmation_email(self._user(), source="signup")
            bot.resend_account_confirmation_by_email(self.email, source="resend_button")
        sources = [bot.json.loads(r[3] or "{}").get("source") for r in self._events()]
        self.assertIn("signup", sources)
        self.assertIn("resend_button", sources)

    def test_d_the_event_carries_no_readable_email_address(self):
        # log_auth_event masks, but this is the property the admin console and
        # the §16 sweep both depend on, so pin it here rather than assume it.
        with patch.object(bot, "send_email_verification", return_value=True):
            bot.send_account_confirmation_email(self._user(), source="signup")
        for _, _, email, _ in self._events():
            self.assertNotEqual(email, self.email)
            self.assertIn("*", email)

    def test_e_clicking_the_link_is_recorded(self):
        token = bot.create_email_verification(self.user_id)
        response = self.client.get(f"/verify-email/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("email_confirmed", self._types())
        self.assertTrue(int(self._user().get("email_verified") or 0))

    def test_f_the_mobile_route_records_it_too(self):
        token = bot.create_email_verification(self.user_id)
        response = self.client.post("/api/mobile/auth/confirm-email", json={"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email_confirmed", self._types())

    def test_g_a_second_click_is_a_rejection_with_a_reason(self):
        # Clicking twice is ordinary -- mail clients prefetch links, people
        # double-tap. It must not read the same as an expired token, because
        # only one of those is a problem worth fixing.
        token = bot.create_email_verification(self.user_id)
        self.client.get(f"/verify-email/{token}")
        self.client.get(f"/verify-email/{token}")
        reasons = [
            bot.json.loads(r[3] or "{}").get("reason")
            for r in self._events()
            if r[0] == "verification_link_rejected"
        ]
        self.assertEqual(reasons, ["already_used"])

    def test_h_an_expired_token_is_recorded_as_expired(self):
        token = bot.create_email_verification(self.user_id)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE email_verification_tokens SET expires_at=? WHERE token=?",
            ((bot.datetime.now() - bot.timedelta(hours=1)).isoformat(), token),
        )
        conn.commit()
        conn.close()
        self.client.get(f"/verify-email/{token}")
        reasons = [
            bot.json.loads(r[3] or "{}").get("reason")
            for r in self._events()
            if r[0] == "verification_link_rejected"
        ]
        self.assertEqual(reasons, ["expired"])

    def test_i_an_unknown_token_is_recorded_as_unknown(self):
        self.client.get("/verify-email/not-a-real-token")
        reasons = [
            bot.json.loads(r[3] or "{}").get("reason")
            for r in self._events()
            if r[0] == "verification_link_rejected"
        ]
        self.assertEqual(reasons, ["unknown_token"])

    def test_j_the_three_refusals_are_indistinguishable_to_the_user(self):
        # The reason is for the operator. A visitor who could tell "already
        # used" from "no such token" could ask whether any given token ever
        # existed, so all three must render the same page.
        used = bot.create_email_verification(self.user_id)
        self.client.get(f"/verify-email/{used}")
        expired = bot.create_email_verification(self.user_id)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE email_verification_tokens SET expires_at=? WHERE token=?",
            ((bot.datetime.now() - bot.timedelta(hours=1)).isoformat(), expired),
        )
        conn.commit()
        conn.close()
        bodies = {
            self.client.get(f"/verify-email/{probe}").get_data(as_text=True)
            for probe in (used, expired, "not-a-real-token")
        }
        self.assertEqual(len(bodies), 1)
        body = bodies.pop().lower()
        for code in ("already_used", "unknown_token"):
            self.assertNotIn(code, body)
        # And the operator-side record does distinguish them.
        reasons = [
            bot.json.loads(r[3] or "{}").get("reason")
            for r in self._events()
            if r[0] == "verification_link_rejected"
        ]
        self.assertEqual(reasons, ["already_used", "expired", "unknown_token"])

    def test_k_the_helper_reads_the_row_it_is_given(self):
        self.assertEqual(bot.verification_token_rejection_reason(None), "unknown_token")
        self.assertEqual(bot.verification_token_rejection_reason((1, "2999-01-01", "2026-01-01")), "already_used")
        self.assertEqual(bot.verification_token_rejection_reason((1, "2000-01-01", None)), "expired")


if __name__ == "__main__":
    unittest.main()
