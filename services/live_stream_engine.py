"""Pulse Live engagement and safety primitives."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4


def start_stream(user_id, title="", category="Crypto Education", premium_only=False):
    return {
        "ok": True,
        "stream_key": "cpx_live_" + uuid4().hex,
        "channel": f"pulse_live_{int(user_id or 0)}_{uuid4().hex[:10]}",
        "title": title or "CoinPilotXAI Pulse Live",
        "category": category or "Crypto Education",
        "premium_only": bool(premium_only),
        "started_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def join_stream(live_id, user_id):
    return {"ok": True, "live_id": int(live_id or 0), "user_id": int(user_id or 0), "event": "viewer_joined"}


def leave_stream(live_id, user_id):
    return {"ok": True, "live_id": int(live_id or 0), "user_id": int(user_id or 0), "event": "viewer_left"}


def add_guest(live_id, host_user_id, guest_user_id):
    return {"ok": True, "live_id": int(live_id or 0), "host_user_id": int(host_user_id or 0), "guest_user_id": int(guest_user_id or 0)}


def remove_guest(live_id, guest_user_id):
    return {"ok": True, "live_id": int(live_id or 0), "guest_user_id": int(guest_user_id or 0), "removed": True}


def create_clip(live_id, start_ms=0, end_ms=0, title=""):
    return {"ok": True, "live_id": int(live_id or 0), "clip_id": uuid4().hex[:12], "start_ms": int(start_ms or 0), "end_ms": int(end_ms or 0), "title": title or "Pulse Live Clip"}


def generate_live_summary(messages=None, title=""):
    messages = messages or []
    return {"summary": (title or "Pulse Live") + f" · {len(messages)} chat moments captured.", "highlights": messages[:5]}


def detect_spam_in_chat(text=""):
    lowered = (text or "").lower()
    spam_terms = ["free money", "guaranteed", "click now", "airdrop claim"]
    hits = [term for term in spam_terms if term in lowered]
    return {"spam": bool(hits), "hits": hits, "action": "review" if hits else "allow"}


def detect_scam_in_chat(text=""):
    lowered = (text or "").lower()
    scam_terms = ["seed phrase", "private key", "send crypto", "wallet validate"]
    hits = [term for term in scam_terms if term in lowered]
    return {"scam_risk": bool(hits), "hits": hits, "action": "block" if hits else "allow"}
