"""Twilio notification foundation for Pulse Communications 2.0."""

from __future__ import annotations

import base64
import logging
import os
import urllib.parse
import urllib.request
from typing import Any

from . import infrastructure


def notifications_enabled() -> bool:
    return (os.environ.get("COMM_V2_TWILIO_NOTIFICATIONS_ENABLED") or "").strip().lower() == "true"


def dry_run_enabled() -> bool:
    return (os.environ.get("COMM_V2_TWILIO_DRY_RUN") or "true").strip().lower() != "false"


def diagnostics() -> dict:
    data = infrastructure.diagnostics().get("twilio", {})
    data["notifications_enabled"] = notifications_enabled()
    data["dry_run"] = dry_run_enabled()
    return data


def _clean(value: Any, limit: int = 700) -> str:
    return " ".join(str(value or "").split())[:limit]


def _send_sms(to_number: str, body: str, *, event_type: str, user_id: int = 0) -> dict:
    to_number = _clean(to_number, 80)
    body = _clean(body, 1500)
    if not notifications_enabled():
        return {"ok": True, "provider": "twilio", "dry_run": True, "skipped": True, "reason": "notifications_disabled", "event_type": event_type}
    if dry_run_enabled():
        logging.info("COMM_V2_TWILIO_DRY_RUN event_type=%s user_id=%s to_configured=%s", event_type, int(user_id or 0), bool(to_number))
        return {"ok": True, "provider": "twilio", "dry_run": True, "skipped": True, "event_type": event_type}
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    missing = [name for name, value in (("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token), ("TWILIO_FROM_NUMBER", from_number)) if not value]
    if missing:
        return {"ok": False, "provider": "twilio", "status": "not_configured", "missing_fields": missing, "message": "Twilio notifications are not fully configured."}
    if not to_number:
        return {"ok": False, "provider": "twilio", "status": "missing_recipient", "message": "Recipient phone number is required."}
    payload = urllib.parse.urlencode({"From": from_number, "To": to_number, "Body": body}).encode("utf-8")
    request = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(sid)}/Messages.json", data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return {"ok": True, "provider": "twilio", "dry_run": False, "status_code": response.status, "event_type": event_type}
    except Exception as exc:
        logging.warning("COMM_V2_TWILIO_SEND_FAILED event_type=%s user_id=%s reason=%s", event_type, int(user_id or 0), type(exc).__name__)
        return {"ok": False, "provider": "twilio", "status": "send_failed", "message": "Twilio could not send this notification."}


def send_sms_verification(to_number: str, code: str, *, user_id: int = 0) -> dict:
    return _send_sms(to_number, f"Your CoinPilotXAI verification code is {str(code)[:12]}.", event_type="sms_verification", user_id=user_id)


def send_message_alert(to_number: str, preview: str = "", *, user_id: int = 0) -> dict:
    return _send_sms(to_number, f"New Pulse message: {_clean(preview, 140)}", event_type="message_alert", user_id=user_id)


def send_room_invite_alert(to_number: str, room_title: str = "", inviter: str = "", *, user_id: int = 0) -> dict:
    return _send_sms(to_number, f"{_clean(inviter, 80) or 'A Pulse member'} invited you to {_clean(room_title, 100) or 'a Pulse room'}.", event_type="room_invite", user_id=user_id)


def send_security_alert(to_number: str, alert: str = "", *, user_id: int = 0) -> dict:
    return _send_sms(to_number, f"CoinPilotXAI security alert: {_clean(alert, 180)}", event_type="security_alert", user_id=user_id)
