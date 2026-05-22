import os
import requests


BREVO_SMTP_URL = "https://api.brevo.com/v3/smtp/email"


def _coalesce(*values):
    for value in values:
        if value:
            return value
    return ""


def sender_config(channel="transactional", from_email=None, from_name=None):
    channel = (channel or "transactional").lower()
    if channel == "support":
        default_email = os.getenv("SUPPORT_FROM_ADDRESS", "support@coinpilotx.app")
        default_name = os.getenv("SUPPORT_FROM_NAME", "CoinPilotXAI Support")
    elif channel == "security":
        default_email = os.getenv("SECURITY_FROM_ADDRESS", "security@coinpilotx.app")
        default_name = os.getenv("SECURITY_FROM_NAME", "CoinPilotXAI Security")
    else:
        default_email = "noreply@coinpilotx.app"
        default_name = "CoinPilotXAI"
    return {
        "email": _coalesce(from_email, os.getenv("BREVO_SENDER_EMAIL"), os.getenv("MAIL_FROM_ADDRESS"), default_email),
        "name": _coalesce(from_name, os.getenv("BREVO_SENDER_NAME"), os.getenv("MAIL_FROM_NAME"), default_name),
        "channel": channel,
    }


def provider_status():
    config = sender_config()
    return {
        "provider": "brevo",
        "ready": bool(os.getenv("BREVO_API_KEY")),
        "sender_email": config["email"],
        "sender_name": config["name"],
    }


def send_brevo_email(to_email, subject, text_body, html_body="", from_email=None, from_name=None, channel="transactional"):
    api_key = os.getenv("BREVO_API_KEY")
    config = sender_config(channel=channel, from_email=from_email, from_name=from_name)
    if not to_email:
        return {"ok": False, "status_code": None, "response": {"message": "recipient email missing"}, "error": "recipient email missing", "sender": config}
    if not api_key:
        return {"ok": False, "status_code": None, "response": {"message": "BREVO_API_KEY is not loaded."}, "error": "BREVO_API_KEY is not loaded.", "sender": config}
    try:
        response = requests.post(
            BREVO_SMTP_URL,
            headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            json={
                "sender": {"email": config["email"], "name": config["name"]},
                "to": [{"email": to_email}],
                "subject": subject,
                "textContent": text_body or "",
                "htmlContent": html_body or (text_body or "").replace("\n", "<br>"),
            },
            timeout=15,
        )
        try:
            body = response.json() if response.text else {}
        except Exception:
            body = {"raw": response.text}
        message_id = body.get("messageId")
        if not message_id and isinstance(body.get("messageIds"), list) and body["messageIds"]:
            message_id = body["messageIds"][0]
        return {
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response": body,
            "provider_response": body,
            "message_id": message_id or "",
            "error": "" if 200 <= response.status_code < 300 else (body.get("message") or body.get("code") or response.text[:500]),
            "sender": config,
        }
    except Exception as exc:
        return {"ok": False, "status_code": None, "response": {}, "provider_response": {}, "message_id": "", "error": str(exc), "sender": config}


def send_email(to_email, subject, html_body, text_body=None, email_type=None, user_id=None, metadata=None, channel="transactional", from_email=None, from_name=None):
    return send_brevo_email(
        to_email,
        subject,
        text_body or html_body,
        html_body,
        from_email=from_email,
        from_name=from_name,
        channel=channel,
    )


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
    return send_payment_confirmation(user, payment_details)


def send_payment_confirmation(user, payment_details=None):
    payment_details = payment_details or {}
    amount = payment_details.get("amount")
    currency = (payment_details.get("currency") or "USD").upper()
    amount_line = f"<p><strong>Payment amount:</strong> {amount} {currency}</p>" if amount else ""
    return send_email(
        (user or {}).get("email"),
        "Your Pulse Premium Is Active",
        (
            "<p>Your Pulse Premium access is active.</p>"
            f"{amount_line}"
            "<p>Dashboard: <a href='https://coinpilotx.app/dashboard'>https://coinpilotx.app/dashboard</a></p>"
            "<p>If you experience any issue after payment, please email support@coinpilotx.app and include the email address used for your CoinPilotXAI account.</p>"
            "<p>CoinPilotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice.</p>"
        ),
        "Your Pulse Premium access is active. Dashboard: https://coinpilotx.app/dashboard. Support: support@coinpilotx.app",
        email_type="payment_confirmation",
    )


def send_pro_activation(user, payment_details=None):
    return send_payment_confirmation(user, payment_details)


def send_reset_email(user, reset_url):
    return send_password_reset_email(user, reset_url)


def send_signup_verification(user, verification_url):
    return send_email_verification(user, verification_url)


def send_support_email(to_email, subject, html_body, text_body=None):
    return send_email(to_email, subject, html_body, text_body, channel="support")


def send_security_email(to_email, subject, html_body, text_body=None):
    return send_email(to_email, subject, html_body, text_body, channel="security")


def send_payment_issue_email(user, payment_details=None):
    return send_email(
        (user or {}).get("email"),
        "Action needed: Pulse Premium payment issue",
        "<p>Stripe reported a payment issue for your Pulse Premium subscription.</p>",
        "Stripe reported a payment issue for your Pulse Premium subscription.",
    )


def send_trial_started_email(user):
    return send_welcome_email(user)


def send_trial_expiring_email(user):
    return send_email((user or {}).get("email"), "Your legacy trial expires soon", "<p>Your legacy trial expires soon. Core access remains free.</p>", "Your legacy trial expires soon. Core access remains free.")


def send_trial_ended_email(user):
    return send_email((user or {}).get("email"), "Your legacy trial has ended", "<p>Your legacy trial has ended. Core access remains free.</p>", "Your legacy trial has ended. Core access remains free.")


def send_admin_invitation_email(admin_user, invite_url):
    return send_email(
        (admin_user or {}).get("email"),
        "CoinPilotXAI Inc. admin invitation",
        f"<p><a href='{invite_url}'>Accept admin invitation</a></p>",
        f"Accept admin invitation: {invite_url}",
    )
