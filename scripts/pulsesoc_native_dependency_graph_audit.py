#!/usr/bin/env python3
"""Audit the PulseSoc native migration dependency graph report."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pulsesoc_native_dependency_graph.md"
BOT = ROOT / "bot.py"
COMM_V2 = ROOT / "pulse_communications_v2" / "routes.py"
REALTIME_TRANSPORT = ROOT / "services" / "command_center_worker" / "realtime_transport.py"
COMMAND_WORKER = ROOT / "services" / "command_center_worker" / "app.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sqlite_tables() -> set[str]:
    db_path = ROOT / "coinpilotx.db"
    if not db_path.exists():
        return set()
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def main() -> int:
    report = read(REPORT)
    bot = read(BOT)
    comm = read(COMM_V2)
    realtime_sources = "\n".join([comm, read(REALTIME_TRANSPORT), read(COMMAND_WORKER)])
    tables = sqlite_tables()

    required_sections = [
        "Frontend Inventory",
        "Backend Inventory By Domain",
        "Database Inventory",
        "Realtime Architecture",
        "Media Pipeline",
        "Third-Party Dependency Map",
        "Native Readiness Score",
        "Master Migration Backlog",
    ]
    for section in required_sections:
        require(section in report, f"report includes {section}")

    required_routes = [
        "/api/mobile/auth/session",
        "/api/pulse/feed",
        "/api/pulse/messages/conversations",
        "/api/pulse/reels/feed",
        "/api/pulse/status/rail",
        "/api/pulse/live/start",
        "/api/pulse/live/<int:live_id>/livekit/token",
        "/api/pulse/media/upload",
        "/api/pulse/media/<int:media_id>/stream",
        "/api/pulse/notifications",
        "/api/premium/status",
        "/api/pulse/profile/me",
    ]
    for route in required_routes:
        require(route in bot, f"backend route exists: {route}")
        normalized = route.replace("<int:", "<").replace("<int:media_id>", "<media_id>").replace("<int:live_id>", "<live_id>")
        require(route in report or normalized in report, f"report maps route: {route}")

    comm_tokens = [
        "/api/pulse/comm/v2/realtime",
        "/api/pulse/comm/v2/realtime/stream",
        "/api/calls/start",
        "/api/calls/<path:call_id>/join-token",
        "typing_started",
        "presence_updated",
    ]
    for token in comm_tokens:
        require(token in realtime_sources, f"Communications/realtime source includes {token}")
        require(token.replace("<path:", "<") in report or token in report, f"report maps Communications V2 token {token}")

    required_tables = [
        "users",
        "sessions",
        "conversations",
        "private_messages",
        "message_attachments",
        "pulse_posts",
        "pulse_reels",
        "pulse_status",
        "pulse_media_assets",
        "pulse_live_streams",
        "communication_calls",
        "notifications",
        "notification_delivery_jobs",
        "intelligence_events",
        "subscriptions",
        "premium_entitlements",
        "marketplace_listings",
        "pulse_ad_campaigns",
        "stripe_events",
    ]
    for table in required_tables:
        require(table in report, f"report maps table {table}")
        if tables:
            require(table in tables, f"local schema includes {table}")

    external_services = [
        "Railway",
        "Postgres",
        "Cloudflare R2",
        "Mux",
        "LiveKit",
        "Stripe",
        "Brevo",
        "Twilio",
        "Expo",
        "APNs",
        "FCM",
        "OpenAI",
        "Gemini",
        "Claude",
        "Groq",
        "DeepSeek",
    ]
    for service in external_services:
        require(service in report, f"report includes dependency {service}")

    print("PulseSoc native dependency graph audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
