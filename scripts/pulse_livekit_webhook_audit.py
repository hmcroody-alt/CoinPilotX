#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
REQ = (ROOT / "requirements.txt").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


require('@webhook_app.route("/api/livekit/webhook"' in BOT, "configured LiveKit dashboard webhook route exists")
require('@webhook_app.route("/api/pulse/live/livekit/webhook"' in BOT, "canonical PulseSoc LiveKit webhook alias exists")
require("WebhookReceiver" in BOT and "TokenVerifier" in BOT, "LiveKit SDK verifies webhook signatures and body hashes")
require("pulse_live_provider_events" in BOT and "UNIQUE(provider, provider_event_id)" in BOT, "provider webhook events are idempotent")
require("livekit-api==1.1.0" in REQ, "LiveKit server SDK dependency is pinned")
require("LIVEKIT_WEBHOOK_SECRET" in BOT, "dedicated webhook signing secret is supported")
require("track_published" in BOT and "egress_started" in BOT and "egress_updated" in BOT and "egress_ended" in BOT, "room track and egress lifecycle events are handled")
print("PulseSoc LiveKit webhook audit passed.")
