from __future__ import annotations

import os

from services import sms_service


def validate():
    configured = bool((os.getenv("BREVO_SMS_API_KEY") or os.getenv("BREVO_API_KEY")) and os.getenv("BREVO_SMS_ENABLED", "").lower() == "true" and os.getenv("BREVO_SMS_SENDER"))
    return {"ok": configured, "status": "healthy" if configured else "missing_config", "provider": "brevo_sms"}


def health():
    return validate()


def send(user_id, *_args, **_kwargs):
    return sms_service.send_test_sms(user_id)


def retry(*args, **kwargs):
    return send(*args, **kwargs)


def rate_limit(*_args, **_kwargs):
    return {"ok": True, "allowed": True}
