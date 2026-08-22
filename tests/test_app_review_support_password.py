"""App Review fixes — item 7 (support ticket reference + user confirmation email)
and item 8 (password reset hardening).

Covers:
  (a) reference format + persistence + same reference in the API response
  (b) the user confirmation email is sent with the same reference (send patched)
  (c) /api/mobile/auth/recover is rate limited (7th POST in the window -> 429)
  (d) the legacy plaintext-token fallback is gone: a token stored only in the
      plaintext `token` column never matches

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_review_support_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

REFERENCE_RE = re.compile(r"^PS-\d{4}-[0-9A-F]{8}$")


def _use_module_database():
    """Re-point the process at this module's temp database and guarantee schema.

    ``services.db`` resolves ``DATABASE_URL`` lazily on every connection, and
    pytest imports every selected module during collection *before* running any
    test. A module collected after this one (``test_app_review_convergence``
    sets its own path at import time) therefore leaves the environment pointing
    at *its* database by the time these tests execute, so the request under test
    opens a database where ``init_db`` never ran and fails with "no such table:
    support_tickets".

    Re-pointing alone is not enough: ``init_db`` short-circuits on the module
    global ``INIT_DB_COMPLETED``, so once any other test module has built a
    schema somewhere else our database stays empty and the failure just moves
    to "no such table: password_reset_tokens". Clearing the flag and rebuilding
    is idempotent (``CREATE TABLE IF NOT EXISTS`` throughout) and is what makes
    this file order-independent rather than passing only in one particular
    pytest argument order.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


class SupportTicketReferenceTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()

    @classmethod
    def setUpClass(cls):
        # Build the schema before probing for the table. Without this the probe
        # runs against a database no one has initialised yet and the whole class
        # silently skips -- which looks like a pass in CI while actually testing
        # nothing about the support ticket reference.
        _use_module_database()
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='support_tickets'")
        if not cur.fetchone():
            conn.close()
            raise unittest.SkipTest("init_db did not create support_tickets in the temp database")
        conn.close()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def test_reference_generator_format(self):
        seen = set()
        for _ in range(20):
            ref = bot.generate_support_ticket_reference()
            self.assertRegex(ref, REFERENCE_RE)
            seen.add(ref)
        self.assertGreater(len(seen), 1, "references should not all collide")

    def test_reference_column_exists_in_schema(self):
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(support_tickets)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        self.assertIn("reference", columns)

    def test_api_ticket_returns_and_persists_reference(self):
        sent_internal = []
        sent_confirmation = []
        real_channel = bot.send_channel_email
        real_confirm = bot.send_support_ticket_confirmation_email
        bot.send_channel_email = lambda *a, **k: sent_internal.append((a, k)) or True
        bot.send_support_ticket_confirmation_email = (
            lambda to_email, name, reference, issue_type, subject, user_id=0:
            sent_confirmation.append({"to": to_email, "reference": reference}) or True
        )
        try:
            resp = self.client.post(
                "/api/support/ticket",
                json={
                    "email": "review-fix@example.com",
                    "name": "Review Fixture",
                    "issue_type": "general support",
                    "subject": "Reference test",
                    "message": "Testing the PS reference flow.",
                },
            )
        finally:
            bot.send_channel_email = real_channel
            bot.send_support_ticket_confirmation_email = real_confirm
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        reference = data.get("reference")
        self.assertRegex(reference, REFERENCE_RE)
        # honesty wording: promises "will be sent", never "delivered"
        self.assertIn("will be sent", data.get("message", ""))
        self.assertNotIn("delivered", data.get("message", "").lower())
        # persisted with the same reference
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT reference FROM support_tickets WHERE id=?", (data["ticket_id"],))
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], reference)
        # both emails triggered with the same reference
        self.assertEqual(len(sent_confirmation), 1)
        self.assertEqual(sent_confirmation[0]["reference"], reference)
        self.assertEqual(sent_confirmation[0]["to"], "review-fix@example.com")
        self.assertEqual(len(sent_internal), 1)
        internal_subject = sent_internal[0][0][1]
        self.assertIn(reference, internal_subject)

    def test_confirmation_email_helper_uses_reference(self):
        calls = []

        def fake_send(to_email, subject, text, html, user_id, **kwargs):
            calls.append({"to": to_email, "subject": subject, "text": text, "html": html, "kwargs": kwargs})
            return True

        real = bot.send_platform_email
        bot.send_platform_email = fake_send
        try:
            ok = bot.send_support_ticket_confirmation_email(
                "user@example.com", "Casey", "PS-2026-ABCDEF12", "billing", "Refund question", user_id=7
            )
        finally:
            bot.send_platform_email = real
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["to"], "user@example.com")
        self.assertIn("PS-2026-ABCDEF12", calls[0]["subject"])
        self.assertIn("PS-2026-ABCDEF12", calls[0]["text"])
        self.assertIn("PS-2026-ABCDEF12", calls[0]["html"])
        self.assertEqual(calls[0]["kwargs"].get("email_type"), "support_ticket_confirmation")

    def test_confirmation_email_skips_gracefully_without_email(self):
        real = bot.send_platform_email
        bot.send_platform_email = lambda *a, **k: self.fail("should not send without a valid email")
        try:
            self.assertFalse(bot.send_support_ticket_confirmation_email("", "x", "PS-2026-00000000", "a", "b"))
            self.assertFalse(bot.send_support_ticket_confirmation_email("not-an-email", "x", "PS-2026-00000000", "a", "b"))
        finally:
            bot.send_platform_email = real


class PasswordResetHardeningTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()

    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def test_mobile_recover_routes_in_abuse_guard_dict(self):
        import inspect

        source = inspect.getsource(bot.basic_abuse_guard)
        self.assertIn('"/api/mobile/auth/recover"', source)
        self.assertIn('"/api/pulse/mobile/auth/recover"', source)

    def test_mobile_recover_is_rate_limited(self):
        # Two layers apply: pulse_security_core (5/600s per ip+device) fires first,
        # then basic_abuse_guard (6/300s per ip). Either way the route must throttle.
        bot.RATE_LIMIT_BUCKETS.clear()
        statuses = []
        for _ in range(8):
            resp = self.client.post("/api/mobile/auth/recover", json={"email": "nobody@example.com"})
            statuses.append(resp.status_code)
        self.assertEqual(statuses[:5], [200] * 5, f"first 5 should pass, got {statuses}")
        self.assertEqual(statuses[5:], [429] * 3, f"6th+ request should be limited, got {statuses}")
        bot.RATE_LIMIT_BUCKETS.clear()

    def test_masked_email_never_silently_no_ops(self):
        """A masked address must be refused, not treated as "no such user".

        The native Security screen showed the account's email masked
        ("h***@gmail.com") and passed that same string back as the recovery
        identifier. ``is_valid_email`` accepts it -- it is a syntactically fine
        address -- so it sailed through validation, matched no user, and the
        request ended in the enumeration-resistant "no match" branch. The user
        was told "Check your email" and no email was ever sent.
        """
        self.assertTrue(bot.is_valid_email("h***@gmail.com"), "precondition: the mask parses as a valid address")
        sent = []
        real_send = bot.send_password_reset_email
        bot.send_password_reset_email = lambda *a, **k: sent.append(a) or True
        try:
            result = bot.safe_password_reset_request("h***@gmail.com", source="unit_test")
        finally:
            bot.send_password_reset_email = real_send
        self.assertTrue(result.get("masked_input"), "a masked address must be flagged, not silently dropped")
        self.assertEqual(sent, [], "no reset email should be attempted for a masked address")

    def test_authenticated_change_request_requires_login(self):
        resp = self.client.post("/api/account/password/change-request", json={})
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_change_request_ignores_client_supplied_email(self):
        """The route must resolve the address from the session, not the body.

        This is the fix for the masked-email bug: an authenticated caller has no
        reason to be trusted with an address, so supplying one must not steer
        where the link goes.
        """
        import inspect

        source = inspect.getsource(bot.api_account_password_change_request)
        self.assertIn("load_account_by_id", source)
        self.assertNotIn("payload", source, "the route must not read an email out of the request body")
        guard = inspect.getsource(bot.basic_abuse_guard)
        self.assertIn('"/api/account/password/change-request"', guard)

    def test_plaintext_token_fallback_removed(self):
        plaintext = "legacy-plaintext-token-abc123"
        conn = bot.db()
        cur = conn.cursor()
        bot.ensure_password_reset_token_columns(cur, conn)
        cur.execute(
            "INSERT INTO password_reset_tokens (user_id, token, token_hash, expires_at, created_at) VALUES (?, ?, NULL, ?, ?)",
            (424242, plaintext, "2099-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        row = bot.load_password_reset_record(cur, plaintext)
        self.assertIsNone(row, "a token stored only in plaintext must never match")
        # the hashed path still works
        hashed_token = "hashed-path-token-xyz"
        cur.execute(
            "INSERT INTO password_reset_tokens (user_id, token, token_hash, expires_at, created_at) VALUES (?, NULL, ?, ?, ?)",
            (424243, bot.password_reset_token_hash(hashed_token), "2099-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        row = bot.load_password_reset_record(cur, hashed_token)
        self.assertIsNotNone(row)
        conn.close()


if __name__ == "__main__":
    unittest.main()
