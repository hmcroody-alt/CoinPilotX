import os
import requests


def send_brevo_email(to_email, subject, text_body, html_body="", from_email=None, from_name=None):
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        return {"ok": False, "status_code": None, "response": {"message": "BREVO_API_KEY is not loaded."}}
    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"email": from_email or os.getenv("MAIL_FROM_ADDRESS", "support@coinpilotx.app"), "name": from_name or os.getenv("MAIL_FROM_NAME", "CoinPilotXAI Inc.")},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": text_body,
            "htmlContent": html_body or text_body.replace("\n", "<br>"),
        },
        timeout=15,
    )
    try:
        body = response.json() if response.text else {}
    except Exception:
        body = {"raw": response.text}
    return {"ok": 200 <= response.status_code < 300, "status_code": response.status_code, "response": body}


def send_email(to_email, subject, html_body, text_body=None, email_type=None, user_id=None, metadata=None):
    return send_brevo_email(to_email, subject, text_body or html_body, html_body)


def send_welcome_email(user):
    name = (user or {}).get("full_name") or "there"
    return send_email(
        (user or {}).get("email"),
        "Welcome to CoinPilotX — Powered by CoinPilotXAI Inc.",
        f"<p>Hi {name}, welcome to CoinPilotX.</p>",
        f"Hi {name}, welcome to CoinPilotX.",
    )


def send_email_verification(user, verification_url):
    return send_email(
        (user or {}).get("email"),
        "Verify your CoinPilotX email",
        f"<p><a href='{verification_url}'>Verify email</a></p>",
        f"Verify your email: {verification_url}",
    )


def send_password_reset_email(user, reset_url):
    return send_email(
        (user or {}).get("email"),
        "Reset your CoinPilotX password",
        f"<p><a href='{reset_url}'>Reset password</a></p>",
        f"Reset your password: {reset_url}",
    )


def send_password_changed_email(user):
    return send_email(
        (user or {}).get("email"),
        "Your CoinPilotX password was changed",
        "<p>Your CoinPilotX password was changed successfully.</p>",
        "Your CoinPilotX password was changed successfully.",
    )


def send_username_recovery_email(user):
    email = (user or {}).get("email")
    return send_email(email, "Your CoinPilotX account login", f"<p>Login email: {email}</p>", f"Login email: {email}")


def send_update_signup_email(lead):
    return send_email(
        (lead or {}).get("email"),
        "You’re on the CoinPilotXAI Inc. update list",
        "<p>Thanks for joining the CoinPilotXAI Inc. update list.</p>",
        "Thanks for joining the CoinPilotXAI Inc. update list.",
    )


def send_upgrade_confirmation_email(user, payment_details=None):
    return send_email(
        (user or {}).get("email"),
        "Your CoinPilotXAI Pro Upgrade Is Active",
        "<p>Your CoinPilotX Pro access is active.</p>",
        "Your CoinPilotX Pro access is active.",
    )


def send_payment_issue_email(user, payment_details=None):
    return send_email(
        (user or {}).get("email"),
        "Action needed: CoinPilotXAI Pro payment issue",
        "<p>Stripe reported a payment issue for your CoinPilotX Pro subscription.</p>",
        "Stripe reported a payment issue for your CoinPilotX Pro subscription.",
    )


def send_trial_started_email(user):
    return send_welcome_email(user)


def send_trial_expiring_email(user):
    return send_email((user or {}).get("email"), "Your CoinPilotX Pro trial expires soon", "<p>Your Pro trial expires soon.</p>", "Your Pro trial expires soon.")


def send_trial_ended_email(user):
    return send_email((user or {}).get("email"), "Your CoinPilotX Pro trial has ended", "<p>Your Pro trial has ended.</p>", "Your Pro trial has ended.")


def send_admin_invitation_email(admin_user, invite_url):
    return send_email(
        (admin_user or {}).get("email"),
        "CoinPilotXAI Inc. admin invitation",
        f"<p><a href='{invite_url}'>Accept admin invitation</a></p>",
        f"Accept admin invitation: {invite_url}",
    )
