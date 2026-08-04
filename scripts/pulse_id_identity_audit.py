#!/usr/bin/env python3
"""Fail closed when the canonical Pulse ID foundation is disconnected."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, *needles: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in text, f"{path}: missing {needle!r}"


require(
    "services/pulse_id_service.py",
    "def canonical_pulse_id",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_pulse_id",
    "def resolve_user_id",
)
require(
    "bot.py",
    "pulse_id_service.ensure_schema",
    "pulse_id_service.ensure_user_pulse_id",
    '"pulse_id": user.get("pulse_id")',
    "upper(COALESCE(u.pulse_id,''))=upper(?)",
    '/api/pulse/identity/<path:pulse_id>',
)
require("migrations/pulse_id_identity.sql", "ADD COLUMN IF NOT EXISTS pulse_id", "UNIQUE INDEX")
require("mobile-native/src/components/ProfileHeader.tsx", "PulseIdBadge", "Pulse Identity")
require("mobile-native/src/screens/PulseIdentityScreen.tsx", "QR Identity", "Connected Wallets")
require("mobile-native/src/screens/SearchScreen.tsx", "PulseIdBadge")
require("mobile-native/src/screens/NewChatScreen.tsx", "PulseIdBadge")
require("mobile-native/src/api/businessProfile.ts", "pulseId")

print("PASS: canonical Pulse ID schema, allocation, resolution, API, search, and native identity surfaces are wired")
