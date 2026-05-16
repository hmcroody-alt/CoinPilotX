"""Lightweight realtime payload helpers for CoinPilotXAI Arena.

The app can run these over polling today and upgrade to SocketIO/SSE later
without changing the response shape.
"""

from __future__ import annotations

from datetime import datetime


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def event(event_type: str, title: str, body: str = "", payload=None):
    return {
        "event_type": event_type,
        "title": title,
        "body": body,
        "payload": payload or {},
        "created_at": now_iso(),
    }


def live_room_payload(room: dict, messages: list, leaderboard: list, matches: list, commentary: str):
    return {
        "ok": True,
        "transport": "polling",
        "realtime_ready": True,
        "room": room or {},
        "messages": messages or [],
        "top_players": leaderboard or [],
        "active_matches": matches or [],
        "ai_commentary": commentary,
        "updated_at": now_iso(),
        "refresh_ms": 5000,
    }


def live_match_payload(match: dict, participants: list, positions: list, trades: list, events: list, spectators: int, commentary: str):
    return {
        "ok": True,
        "transport": "polling",
        "realtime_ready": True,
        "match": match or {},
        "participants": participants or [],
        "positions": positions or [],
        "trades": trades or [],
        "events": events or [],
        "spectators": spectators,
        "ai_commentary": commentary,
        "updated_at": now_iso(),
        "refresh_ms": 3000,
    }
