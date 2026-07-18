import os
import requests


BREVO_SMTP_URL = "https://api.brevo.com/v3/smtp/email"
OFFICIAL_SUPPORT_EMAIL = "support@pulsesoc.com"
OFFICIAL_PRODUCT_NAME = "PulseSoc"
OFFICIAL_COMPANY_NAME = "CoinPlotXAI Inc."


def _truthy_env(key, default=True):
    if key not in os.environ:
        return bool(default)
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _coalesce(*values):
    for value in values:
        if value:
            return value
    return ""


def _clean_env(key):
    return (os.getenv(key) or "").strip()


def product_name():
    return _clean_env("PRODUCT_NAME") or OFFICIAL_PRODUCT_NAME


def company_name():
    return _clean_env("COMPANY_NAME") or OFFICIAL_COMPANY_NAME


def support_email():
    return (
        _clean_env("PUBLIC_SUPPORT_EMAIL")
        or _clean_env("SUPPORT_EMAIL")
        or _clean_env("BREVO_REPLY_TO")
        or OFFICIAL_SUPPORT_EMAIL
    )


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
        default_email = support_email()
        default_name = _clean_env("SUPPORT_FROM_NAME") or f"{product_name()} Support"
    elif channel == "security":
        default_email = support_email()
        default_name = _clean_env("SECURITY_FROM_NAME") or f"{product_name()} Security"
    else:
        default_email = support_email()
        default_name = product_name()
    sender_candidates = [
        ("explicit", from_email),
        ("BREVO_SENDER_EMAIL", _clean_env("BREVO_SENDER_EMAIL")),
        ("BREVO_FROM_EMAIL", _clean_env("BREVO_FROM_EMAIL")),
        ("BREVO_SENDER", _clean_env("BREVO_SENDER")),
        ("MAIL_FROM_ADDRESS", _clean_env("MAIL_FROM_ADDRESS")),
        ("DEFAULT_FROM_EMAIL", _clean_env("DEFAULT_FROM_EMAIL")),
        ("channel_default", default_email),
    ]
    name_candidates = [
        ("explicit", from_name),
        ("BREVO_SENDER_NAME", _clean_env("BREVO_SENDER_NAME")),
        ("PRODUCT_NAME", product_name()),
        ("MAIL_FROM_NAME", _clean_env("MAIL_FROM_NAME")),
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


def reply_to_config(channel="transactional"):
    channel = (channel or "transactional").lower()
    sender = sender_config(channel=channel)
    reply_candidates = [
        ("BREVO_REPLY_TO", _clean_env("BREVO_REPLY_TO")),
        ("PUBLIC_SUPPORT_EMAIL", _clean_env("PUBLIC_SUPPORT_EMAIL")),
        ("SUPPORT_EMAIL", _clean_env("SUPPORT_EMAIL")),
        ("SUPPORT_FROM_ADDRESS", _clean_env("SUPPORT_FROM_ADDRESS")),
        ("BREVO_SENDER_EMAIL", _clean_env("BREVO_SENDER_EMAIL")),
        ("sender", sender.get("email")),
        ("official_support", OFFICIAL_SUPPORT_EMAIL),
    ]
    name_candidates = [
        ("BREVO_REPLY_TO_NAME", _clean_env("BREVO_REPLY_TO_NAME")),
        ("SUPPORT_FROM_NAME", _clean_env("SUPPORT_FROM_NAME")),
        ("BREVO_SENDER_NAME", _clean_env("BREVO_SENDER_NAME")),
        ("PRODUCT_NAME", product_name()),
    ]
    reply_email = ""
    reply_source = ""
    for source, value in reply_candidates:
        if value:
            reply_email = value
            reply_source = source
            break
    reply_name = ""
    reply_name_source = ""
    for source, value in name_candidates:
        if value:
            reply_name = value
            reply_name_source = source
            break
    return {
        "email": reply_email,
        "name": reply_name or product_name(),
        "channel": channel,
        "email_source": reply_source,
        "name_source": reply_name_source,
    }


def provider_status():
    config = sender_config()
    reply_to = reply_to_config()
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
        "public_support_email_configured": bool(os.getenv("PUBLIC_SUPPORT_EMAIL")),
        "security_email_configured": bool(os.getenv("SECURITY_EMAIL")),
        "missing_fields": missing,
        "sender_email": config["email"],
        "sender_name": config["name"],
        "reply_to_email": reply_to["email"],
        "reply_to_name": reply_to["name"],
        "reply_to_email_source": reply_to.get("email_source") or "",
        "sender_email_source": config.get("email_source") or "",
        "sender_name_source": config.get("name_source") or "",
        "sender_domain": sender_domain,
        "using_default_sender": bool(config.get("using_default_sender")),
    }


def send_brevo_email(to_email, subject, text_body, html_body="", from_email=None, from_name=None, channel="transactional"):
    api_key = brevo_api_key_config()["value"]
    config = sender_config(channel=channel, from_email=from_email, from_name=from_name)
    reply_to = reply_to_config(channel=channel)
    if not _truthy_env("BREVO_EMAIL_ENABLED", True):
        return {
            "ok": False,
            "status_code": None,
            "response": {"message": "Brevo email notifications are disabled."},
            "error": "Brevo email notifications are disabled.",
            "error_code": "brevo_email_disabled",
            "sender": config,
            "reply_to": reply_to,
        }
    if not to_email:
        return {"ok": False, "status_code": None, "response": {"message": "recipient email missing"}, "error": "recipient email missing", "sender": config, "reply_to": reply_to}
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
            "reply_to": reply_to,
        }
    payload = {
        "sender": {"email": config["email"], "name": config["name"]},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_body or "",
        "htmlContent": html_body or (text_body or "").replace("\n", "<br>"),
    }
    if reply_to.get("email"):
        payload["replyTo"] = {"email": reply_to["email"], "name": reply_to["name"]}
    try:
        response = requests.post(
            BREVO_SMTP_URL,
            headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
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
            "reply_to": reply_to,
        }
    except Exception as exc:
        return {"ok": False, "status_code": None, "response": {}, "provider_response": {}, "message_id": "", "error": str(exc), "sender": config, "reply_to": reply_to}


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
        "Welcome to PulseSoc",
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
        "PulseSoc admin invitation",
        f"<p><a href='{invite_url}'>Accept admin invitation</a></p>",
        f"Accept admin invitation: {invite_url}",
    )
