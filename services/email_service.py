import os
import requests


BREVO_SMTP_URL = "https://api.brevo.com/v3/smtp/email"


def _truthy_env(key, default=True):
    if key not in os.environ:
        return bool(default)
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _coalesce(*values):
    for value in values:
        if value:
            return value
    return ""


def brevo_api_key_config():
    candidates = [
        ("BREVO_API_KEY", os.getenv("BREVO_API_KEY")),
        ("SENDINBLUE_API_KEY", os.getenv("SENDINBLUE_API_KEY")),
        ("BREVO_SMTP_API_KEY", os.getenv("BREVO_SMTP_API_KEY")),
    ]
    for source, raw_value in candidates:
        if raw_value:
            value = raw_value.strip()
            return {
                "value": value,
                "source": source,
                "configured": bool(value),
                "has_surrounding_whitespace": raw_value != value,
            }
    return {"value": "", "source": "", "configured": False, "has_surrounding_whitespace": False}


def sender_config(channel="transactional", from_email=None, from_name=None):
    channel = (channel or "transactional").lower()
    if channel == "support":
        default_email = os.getenv("SUPPORT_EMAIL") or os.getenv("SUPPORT_FROM_ADDRESS") or "support@pulsesoc.com"
        default_name = os.getenv("SUPPORT_FROM_NAME", "PulseSoc Support")
    elif channel == "security":
        default_email = os.getenv("SECURITY_EMAIL") or os.getenv("SECURITY_FROM_ADDRESS") or "security@pulsesoc.com"
        default_name = os.getenv("SECURITY_FROM_NAME", "PulseSoc Security")
    else:
        default_email = os.getenv("DEFAULT_FROM_EMAIL") or "noreply@pulsesoc.com"
        default_name = "PulseSoc"
    sender_candidates = [
        ("explicit", from_email),
        ("BREVO_FROM_EMAIL", os.getenv("BREVO_FROM_EMAIL")),
        ("BREVO_SENDER", os.getenv("BREVO_SENDER")),
        ("BREVO_SENDER_EMAIL", os.getenv("BREVO_SENDER_EMAIL")),
        ("MAIL_FROM_ADDRESS", os.getenv("MAIL_FROM_ADDRESS")),
        ("DEFAULT_FROM_EMAIL", os.getenv("DEFAULT_FROM_EMAIL")),
        ("channel_default", default_email),
    ]
    name_candidates = [
        ("explicit", from_name),
        ("BREVO_SENDER_NAME", os.getenv("BREVO_SENDER_NAME")),
        ("MAIL_FROM_NAME", os.getenv("MAIL_FROM_NAME")),
        ("channel_default", default_name),
    ]
    sender_email = ""
    sender_source = ""
    for source, value in sender_candidates:
        if value:
            sender_email = value
            sender_source = source
            break
    sender_name = ""
    sender_name_source = ""
    for source, value in name_candidates:
        if value:
            sender_name = value
            sender_name_source = source
            break
    return {
        "email": sender_email,
        "name": sender_name,
        "channel": channel,
        "email_source": sender_source,
        "name_source": sender_name_source,
        "using_default_sender": sender_source == "channel_default",
    }


def provider_status():
    config = sender_config()
    api_key_config = brevo_api_key_config()
    api_key = api_key_config["value"]
    missing = []
    if not api_key:
        missing.append("BREVO_API_KEY")
    if not config.get("email"):
        missing.append("sender email")
    sender_domain = config["email"].split("@")[-1].lower() if "@" in config["email"] else ""
    return {
        "provider": "brevo",
        "ready": not missing and _truthy_env("BREVO_EMAIL_ENABLED", True),
        "enabled": _truthy_env("BREVO_EMAIL_ENABLED", True),
        "api_key_configured": bool(api_key),
        "api_key_source": api_key_config["source"],
        "api_key_has_surrounding_whitespace": bool(api_key_config["has_surrounding_whitespace"]),
        "sender_email_configured": bool(config.get("email")),
        "sender_name_configured": bool(config.get("name")),
        "default_from_email_configured": bool(os.getenv("DEFAULT_FROM_EMAIL")),
        "support_email_configured": bool(os.getenv("SUPPORT_EMAIL")),
        "security_email_configured": bool(os.getenv("SECURITY_EMAIL")),
        "missing_fields": missing,
        "sender_email": config["email"],
        "sender_name": config["name"],
        "sender_email_source": config.get("email_source") or "",
        "sender_name_source": config.get("name_source") or "",
        "sender_domain": sender_domain,
        "using_default_sender": bool(config.get("using_default_sender")),
    }


def send_brevo_email(to_email, subject, text_body, html_body="", from_email=None, from_name=None, channel="transactional"):
    api_key = brevo_api_key_config()["value"]
    config = sender_config(channel=channel, from_email=from_email, from_name=from_name)
    if not _truthy_env("BREVO_EMAIL_ENABLED", True):
        return {
            "ok": False,
            "status_code": None,
            "response": {"message": "Brevo email notifications are disabled."},
            "error": "Brevo email notifications are disabled.",
            "error_code": "brevo_email_disabled",
            "sender": config,
        }
    if not to_email:
        return {"ok": False, "status_code": None, "response": {"message": "recipient email missing"}, "error": "recipient email missing", "sender": config}
    missing = []
    if not api_key:
        missing.append("BREVO_API_KEY")
    if not config.get("email"):
        missing.append("sender email")
    if missing:
        return {
            "ok": False,
            "status_code": None,
            "response": {"message": "Brevo is not fully configured.", "missing_fields": missing},
            "error": f"Brevo is not fully configured. Missing: {', '.join(missing)}",
            "error_code": "brevo_not_configured",
            "missing_fields": missing,
            "sender": config,
        }
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
        error = ""
        error_code = ""
        if 200 <= response.status_code < 300:
            error = ""
        elif response.status_code == 401:
            body_message = str(body.get("message") or "")
            lower_message = body_message.lower()
            if (
                "unrecognised ip address" in lower_message
                or "unrecognized ip address" in lower_message
                or "ip blocked" in lower_message
                or ("not authorized" in lower_message and "ip" in lower_message)
                or ("not authorised" in lower_message and "ip" in lower_message)
            ):
                error = "Brevo rejected the request because the Railway server IP is not authorized in Brevo."
                error_code = "brevo_unauthorized_ip"
            else:
                error = "Brevo rejected the request. Check BREVO_API_KEY in Railway."
                error_code = "brevo_unauthorized"
        elif response.status_code == 403:
            error = "Brevo rejected the sender or domain. Verify BREVO_SENDER_EMAIL and domain authentication in Brevo."
            error_code = "brevo_forbidden"
        elif response.status_code == 429:
            error = "Brevo rate limit reached. Retry shortly."
            error_code = "brevo_rate_limited"
        else:
            error = body.get("message") or body.get("code") or response.text[:500]
            error_code = body.get("code") or ""
        return {
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response": body,
            "provider_response": body,
            "message_id": message_id or "",
            "error": error,
            "error_code": error_code,
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
        "Welcome to PulseSoc — Powered by CoinPilotXAI",
        f"<p>Hi {name}, welcome to PulseSoc.</p>",
        f"Hi {name}, welcome to PulseSoc.",
    )


def send_email_verification(user, verification_url):
    return send_email(
        (user or {}).get("email"),
        "Verify your PulseSoc email",
        f"<p><a href='{verification_url}'>Verify email</a></p>",
        f"Verify your email: {verification_url}",
    )


def send_password_reset_email(user, reset_url):
    return send_email(
        (user or {}).get("email"),
        "Reset your PulseSoc password",
        f"<p><a href='{reset_url}'>Reset password</a></p>",
        f"Reset your password: {reset_url}",
    )


def send_password_changed_email(user):
    return send_email(
        (user or {}).get("email"),
        "Your PulseSoc password was changed",
        "<p>Your PulseSoc password was changed successfully.</p>",
        "Your PulseSoc password was changed successfully.",
    )


def send_username_recovery_email(user):
    email = (user or {}).get("email")
    return send_email(email, "Your PulseSoc account login", f"<p>Login email: {email}</p>", f"Login email: {email}")


def send_update_signup_email(lead):
    return send_email(
        (lead or {}).get("email"),
        "You’re on the PulseSoc update list",
        "<p>Thanks for joining the PulseSoc update list.</p>",
        "Thanks for joining the PulseSoc update list.",
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
        "Your PulseSoc Premium Is Active",
        (
            "<p>Your PulseSoc Premium access is active.</p>"
            f"{amount_line}"
            "<p>Account: <a href='https://pulsesoc.com/account'>https://pulsesoc.com/account</a></p>"
            "<p>If you experience any issue after payment, please email support@pulsesoc.com and include the email address used for your PulseSoc account.</p>"
            "<p>CoinPlotXAI Inc. provides educational AI intelligence only. Not financial, betting, investment, or legal advice.</p>"
        ),
        "Your PulseSoc Premium access is active. Account: https://pulsesoc.com/account. Support: support@pulsesoc.com",
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
        "Action needed: PulseSoc Premium payment issue",
        "<p>Stripe reported a payment issue for your PulseSoc Premium subscription.</p>",
        "Stripe reported a payment issue for your PulseSoc Premium subscription.",
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
        "CoinPlotXAI Inc. admin invitation",
        f"<p><a href='{invite_url}'>Accept admin invitation</a></p>",
        f"Accept admin invitation: {invite_url}",
    )
