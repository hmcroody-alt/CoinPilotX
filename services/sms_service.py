"""Brevo SMS setup, verification, and alert delivery helpers."""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

import requests

from . import user_context


BREVO_SMS_URL = "https://api.brevo.com/v3/transactionalSMS/sms"
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _now():
    return datetime.now().isoformat()


def is_sms_configured():
    return bool(
        (os.getenv("BREVO_API_KEY") or os.getenv("BREVO_SMS_API_KEY"))
        and os.getenv("BREVO_SMS_ENABLED", "").lower() == "true"
        and os.getenv("BREVO_SMS_SENDER")
    )


def normalize_phone(phone):
    phone = (phone or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    return phone if E164_RE.match(phone) else ""


def _hash_code(code):
    secret = os.getenv("SECRET_KEY", "coinpilotx-local")
    return hashlib.sha256(f"{secret}:{code}".encode("utf-8")).hexdigest()


def _log_delivery(user_id, channel, status, provider="brevo_sms", response="", error="", alert_rule_id=None, alert_event_id=None):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO notification_delivery_logs
        (user_id, alert_rule_id, alert_event_id, channel, status, provider, provider_response, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id or 0, alert_rule_id, alert_event_id, channel, status, provider, str(response or "")[:1000], str(error or "")[:1000], _now()),
    )
    conn.commit()
    conn.close()


def send_sms(to_phone, message, purpose="alert", user_id=0, alert_rule_id=None, alert_event_id=None):
    phone = normalize_phone(to_phone)
    if not phone:
        _log_delivery(user_id, "sms", "invalid_phone", error="Invalid E.164 phone number", alert_rule_id=alert_rule_id, alert_event_id=alert_event_id)
        return {"ok": False, "status": "invalid_phone", "message": "Enter a phone number in E.164 format, like +17185551234."}
    if not is_sms_configured():
        _log_delivery(user_id, "sms", "not_configured", error="Brevo SMS is not configured", alert_rule_id=alert_rule_id, alert_event_id=alert_event_id)
        return {"ok": False, "status": "not_configured", "message": "SMS not configured."}
    api_key = os.getenv("BREVO_SMS_API_KEY") or os.getenv("BREVO_API_KEY", "")
    payload = {"sender": os.getenv("BREVO_SMS_SENDER", "PulseSoc")[:11], "recipient": phone, "content": str(message or "")[:480]}
    try:
        response = requests.post(
            BREVO_SMS_URL,
            headers={"api-key": api_key, "Content-Type": "application/json", "accept": "application/json"},
            json=payload,
            timeout=10,
        )
        text = response.text[:1000]
        if 200 <= response.status_code < 300:
            _log_delivery(user_id, "sms", "sent", response=text, alert_rule_id=alert_rule_id, alert_event_id=alert_event_id)
            return {"ok": True, "status": "sent", "provider": "brevo_sms", "provider_response": text}
        status = "no_credits_or_provider_error" if response.status_code in {402, 403, 429} else "failed"
        _log_delivery(user_id, "sms", status, response=text, error=f"Brevo status {response.status_code}", alert_rule_id=alert_rule_id, alert_event_id=alert_event_id)
        return {"ok": False, "status": status, "message": "Brevo SMS provider rejected the request.", "provider_status": response.status_code}
    except Exception as exc:
        _log_delivery(user_id, "sms", "failed", error=str(exc), alert_rule_id=alert_rule_id, alert_event_id=alert_event_id)
        return {"ok": False, "status": "failed", "message": "SMS provider is temporarily unavailable."}


def send_verification_code(user_id, phone):
    phone = normalize_phone(phone)
    if not phone:
        return {"ok": False, "status": "invalid_phone", "message": "Use E.164 format, like +17185551234."}
    if not is_sms_configured():
        _log_delivery(user_id, "sms", "not_configured", error="Brevo SMS is not configured")
        return {"ok": False, "status": "not_configured", "message": "SMS not configured."}
    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET phone=?, phone_number=?, updated_at=? WHERE user_id=?", (phone, phone, _now(), user_id))
    cur.execute(
        "INSERT INTO sms_verification_codes (user_id, phone, code_hash, status, expires_at, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (user_id, phone, _hash_code(code), expires_at, _now()),
    )
    conn.commit()
    conn.close()
    result = send_sms(phone, f"Your PulseSoc verification code is {code}. It expires in 10 minutes.", purpose="verification", user_id=user_id)
    if result.get("ok"):
        return {"ok": True, "status": "sent", "message": "Verification code sent."}
    return result


def verify_sms_code(user_id, code):
    code = (code or "").strip()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM sms_verification_codes
        WHERE user_id=? AND status='pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    record = dict(row) if row and hasattr(row, "keys") else None
    if not record or (record.get("expires_at") or "") < _now() or record.get("code_hash") != _hash_code(code):
        conn.close()
        return {"ok": False, "status": "failed", "message": "Invalid or expired verification code."}
    now = _now()
    cur.execute("UPDATE sms_verification_codes SET status='used', used_at=? WHERE id=?", (now, record["id"]))
    cur.execute("UPDATE users SET phone=?, phone_number=?, phone_verified=1, sms_opt_in=1, sms_verified_at=?, updated_at=? WHERE user_id=?", (record["phone"], record["phone"], now, now, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "status": "verified", "message": "SMS is ready."}


def sms_readiness(user_id):
    user = user_context.get_user_by_id(user_id) or {}
    phone = user.get("phone_number") or user.get("phone") or ""
    configured = is_sms_configured()
    ready = configured and bool(normalize_phone(phone)) and bool(user.get("phone_verified")) and bool(user.get("sms_opt_in"))
    if ready:
        return {"ready": True, "status": "ready", "message": "SMS ready.", "phone": phone}
    if not configured:
        return {"ready": False, "status": "not_configured", "message": "SMS provider not configured."}
    if not phone or not normalize_phone(phone):
        return {"ready": False, "status": "not_configured", "message": "SMS not configured."}
    if not user.get("phone_verified"):
        return {"ready": False, "status": "not_configured", "message": "Phone verification required."}
    return {"ready": False, "status": "not_configured", "message": "SMS opt-in required."}


def send_test_sms(user_id):
    readiness = sms_readiness(user_id)
    if not readiness.get("ready"):
        _log_delivery(user_id, "sms", readiness.get("status") or "not_configured", error=readiness.get("message"))
        return {"ok": False, **readiness}
    return send_sms(readiness.get("phone"), "PulseSoc SMS test: your alert text channel is ready.", purpose="test", user_id=user_id)


def send_alert_sms(user_id, alert_payload):
    readiness = sms_readiness(user_id)
    if not readiness.get("ready"):
        _log_delivery(user_id, "sms", readiness.get("status") or "not_configured", error=readiness.get("message"), alert_rule_id=(alert_payload or {}).get("alert_rule_id"), alert_event_id=(alert_payload or {}).get("alert_event_id"))
        return {"ok": False, **readiness}
    symbol = (alert_payload or {}).get("symbol") or "Market"
    message = (alert_payload or {}).get("message") or f"PulseSoc alert: {symbol} condition triggered."
    return send_sms(readiness.get("phone"), message, purpose="alert", user_id=user_id, alert_rule_id=(alert_payload or {}).get("alert_rule_id"), alert_event_id=(alert_payload or {}).get("alert_event_id"))
