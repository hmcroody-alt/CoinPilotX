"""A process without Brevo credentials must not pretend to be an email sender.

Production logged `failed_brevo_not_configured` for months while Brevo was
working. The rows were real -- every one carried `Missing: BREVO_API_KEY` -- but
they did not mean what they said. `telegram_worker.py` runs `bot.main()`, which
calls `run_trial_maintenance(force=True)` at boot and hourly thereafter; that
service holds `BREVO_SENDER_EMAIL` but not `BREVO_API_KEY`. So a worker whose
job is Telegram polling was sweeping every trialing user and attempting a send
it could never complete. The attempt failed, wrote a row that reads like a
platform outage, and fell through to the outbox -- where `email_worker`, which
does hold the key, delivered the mail about five minutes later. Every failure in
production is paired with exactly that success.

Two things were wrong and both are tested here.

1. The doomed attempt. `send_platform_email` computed `provider_status()` only
   to log it, then called the provider regardless. It now hands the message to
   the outbox up front and logs `queued`, because queued is what happened.

2. The far more dangerous half. `process_email_delivery_jobs` increments
   `retry_count` at *claim* time, before the provider is reached. Every worker
   importing `bot` inherits the opportunistic processor, so an unconfigured one
   draining the queue would spend five attempts against mail it could not send
   and then dead-letter it permanently -- verification and password-reset mail
   included. It now declines to claim at all.

The third group covers the reason codes. `failed_brevo_not_configured` has to
keep meaning "genuinely unusable", so a failure that never reached the provider
must not borrow the name.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="brevo_semantics_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"

import bot  # noqa: E402
from services import email_service  # noqa: E402

RECIPIENT = "queued-recipient@example.com"

# The exact production shape: the sender address resolves (it falls back to the
# support address even when unset), so the only thing missing is the key.
UNCONFIGURED = {"BREVO_API_KEY": "", "SENDINBLUE_API_KEY": "", "BREVO_SMTP_API_KEY": "",
                "BREVO_SENDER_EMAIL": "no-reply@pulsesoc.com", "BREVO_SENDER_NAME": "PulseSoc"}
CONFIGURED = dict(UNCONFIGURED, BREVO_API_KEY="xkeysib-test-not-a-real-key")


def _env(**overrides):
    return patch.dict(os.environ, dict(UNCONFIGURED, **overrides), clear=False)


class BrevoKeyIsCanonical(unittest.TestCase):
    """1-2. One name for the key, and an honest readiness verdict."""

    def test_1_only_brevo_api_key_is_read(self):
        with _env(BREVO_API_KEY="xkeysib-canonical"):
            config = email_service.brevo_api_key_config()
        self.assertTrue(config["configured"])
        self.assertEqual(config["source"], "BREVO_API_KEY")

    def test_2_legacy_alias_names_no_longer_configure_the_key(self):
        # Neither alias is set on any deployed service, so honouring them only
        # ever implied the key could arrive under three names -- which is how a
        # service ends up half-configured without anyone noticing.
        with _env(SENDINBLUE_API_KEY="xkeysib-legacy", BREVO_SMTP_API_KEY="xsmtpsib-legacy"):
            config = email_service.brevo_api_key_config()
        self.assertFalse(config["configured"])
        self.assertEqual(config["source"], "")

    def test_3_missing_key_alone_makes_the_provider_not_ready(self):
        with _env():
            status = email_service.provider_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["missing_fields"], ["BREVO_API_KEY"])
        # Sender resolving is what made the production error name exactly one
        # variable; if this stops holding the log line changes shape.
        self.assertTrue(status["sender_email_configured"])


class UnconfiguredProcessDefersInsteadOfFailing(unittest.TestCase):
    """4-7. The send path in a process that holds no credentials."""

    def setUp(self):
        bot.init_db()
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("DELETE FROM failed_email_queue")
        cur.execute("DELETE FROM email_logs")
        conn.commit()
        conn.close()

    def _queue_rows(self):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("SELECT recipient_email, status, retry_count FROM failed_email_queue")
        rows = [tuple(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _log_statuses(self):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("SELECT status FROM email_logs ORDER BY id")
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def test_4_the_provider_is_never_called(self):
        with _env(), patch.object(email_service, "send_email") as provider:
            bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        provider.assert_not_called()

    def test_4b_a_configured_process_does_call_the_provider(self):
        # The contrast that makes test_4 mean something: the same call in a
        # process that holds the key must still reach Brevo directly. Without
        # this, test_4 would keep passing if the send path stopped working.
        sent = {"ok": True, "status_code": 201, "message_id": "<direct@smtp-relay>", "response": {}}
        with patch.dict(os.environ, CONFIGURED, clear=False), \
                patch.object(email_service, "send_email", return_value=sent) as provider:
            accepted = bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        self.assertTrue(accepted)
        provider.assert_called_once()
        self.assertEqual(self._log_statuses(), ["sent_brevo"])
        self.assertEqual(self._queue_rows(), [])

    def test_5_the_message_is_durably_queued(self):
        with _env(), patch.object(email_service, "send_email"):
            accepted = bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        self.assertTrue(accepted, "a durably queued message is accepted, not failed")
        self.assertEqual(self._queue_rows(), [(RECIPIENT, "pending", 0)])

    def test_6_the_log_says_queued_not_not_configured(self):
        with _env(), patch.object(email_service, "send_email"):
            bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        statuses = self._log_statuses()
        self.assertEqual(statuses, ["queued"])
        self.assertNotIn("failed_brevo_not_configured", statuses)

    def test_7_the_drainer_refuses_to_claim_without_credentials(self):
        with _env(), patch.object(email_service, "send_email"):
            bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        with _env():
            result = bot.process_email_delivery_jobs(limit=10)
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result.get("deferred"), "provider_not_ready")
        # Claiming spends an attempt. An unconfigured drainer must leave
        # retry_count alone so a configured one still has all five.
        self.assertEqual(self._queue_rows(), [(RECIPIENT, "pending", 0)])

    def test_8_repeated_unconfigured_drains_cannot_dead_letter(self):
        with _env(), patch.object(email_service, "send_email"):
            bot.send_platform_email(RECIPIENT, "Confirm your email", "body")
        for _ in range(6):  # one more than max_attempts
            with _env():
                bot.process_email_delivery_jobs(limit=10)
        recipient, status, retry_count = self._queue_rows()[0]
        self.assertEqual(status, "pending")
        self.assertEqual(retry_count, 0)

    def test_9_a_configured_process_still_delivers_the_queued_message(self):
        with _env(), patch.object(email_service, "send_email"):
            bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body")
        sent = {"ok": True, "status_code": 201, "message_id": "<queued@smtp-relay>", "response": {}}
        with patch.dict(os.environ, CONFIGURED, clear=False):
            result = bot.process_email_delivery_jobs(limit=10, provider_send=lambda *a, **k: sent)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(self._queue_rows()[0][1], "sent")

    def test_10_one_logical_email_queues_once(self):
        key = "trial-lifecycle:41:day_21"
        with _env(), patch.object(email_service, "send_email"):
            for _ in range(3):
                bot.send_platform_email(RECIPIENT, "Your legacy trial expires soon", "body",
                                        idempotency_key=key)
        self.assertEqual(len(self._queue_rows()), 1)


class FailureReasonCodesStayDistinct(unittest.TestCase):
    """11. "Not configured" must keep meaning genuinely unusable."""

    def test_11_a_failure_that_never_reached_brevo_is_not_called_not_configured(self):
        # No status_code and no error_code is a transport failure -- DNS, TLS,
        # connect timeout. Calling it "not_configured", as the trailing fallback
        # used to, sends whoever reads the admin log to check environment
        # variables that were correct all along.
        self.assertEqual(
            bot.email_status_from_result({"ok": False, "error": "connection reset"}),
            "failed_brevo_network_error",
        )

    def test_genuinely_missing_config_keeps_its_name(self):
        self.assertEqual(
            bot.email_status_from_result({"ok": False, "missing_fields": ["BREVO_API_KEY"]}),
            "failed_brevo_not_configured",
        )

    def test_each_provider_rejection_keeps_its_own_code(self):
        cases = {
            "failed_brevo_401": {"ok": False, "status_code": 401},
            "failed_brevo_403": {"ok": False, "status_code": 403},
            "failed_brevo_rate_limited": {"ok": False, "status_code": 429},
            "failed_brevo_unauthorized_ip": {"ok": False, "error_code": "brevo_unauthorized_ip"},
            "failed_brevo_email_disabled": {"ok": False, "error_code": "brevo_email_disabled"},
            "failed_brevo_500": {"ok": False, "status_code": 500},
        }
        for expected, result in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(bot.email_status_from_result(result), expected)


if __name__ == "__main__":
    unittest.main()
