#!/usr/bin/env python3
"""Audit user-creation transactional email delivery behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
REPORT = ROOT / "reports" / "user_creation_email_delivery_fix.md"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    require("def send_account_confirmation_email" in BOT, "account confirmation sender missing", failures)
    require("send_email_verification(user, verification_link, trace_id=trace_id" in BOT, "verification email must send through direct provider path with trace id", failures)
    require('"queued": False' in BOT and "Confirmation email sent." in BOT, "confirmation result must not treat queue-only as success", failures)
    require("def send_password_reset_email" in BOT and 'email_type="password_reset"' in BOT, "password reset must use typed direct provider send", failures)
    require("def finalize_email_log_for_trace" in BOT, "queued email logs must be finalized after provider processing", failures)
    require("finalize_email_log_for_trace(" in BOT and "EMAIL_JOB_PROCESSED" in BOT, "queue processor must update email log rows", failures)
    require("PULSESOC_SIGNUP_SUPPORT_COPY" in BOT and 'recipients = [("new_user", user.get("email"))]' in BOT, "signup support-copy welcome email must be opt-in only", failures)
    require('"queued": ("Queued", "status=\'queued\'")' in BOT, "admin email dashboard must expose queued filter", failures)
    require("Process Pending Queue" in BOT, "admin email dashboard must expose pending queue processing action", failures)
    require(REPORT.exists(), "fix report missing", failures)
    if failures:
        print("user creation email delivery audit failed")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("user creation email delivery audit ok")


if __name__ == "__main__":
    main()
