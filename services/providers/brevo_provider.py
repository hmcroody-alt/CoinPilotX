from __future__ import annotations

import os

from services import email_service


def validate():
    return {
        "ok": bool(os.getenv("BREVO_API_KEY")),
        "status": "healthy" if os.getenv("BREVO_API_KEY") else "missing_config",
        "provider": "brevo",
        "sender": email_service.sender_config(),
    }


def health():
    return validate()


def send(to_email, subject, text_body, html_body="", **kwargs):
    return email_service.send_email(to_email, subject, html_body or text_body, text_body, **kwargs)


def retry(*args, **kwargs):
    return send(*args, **kwargs)


def rate_limit(*_args, **_kwargs):
    return {"ok": True, "allowed": True}
