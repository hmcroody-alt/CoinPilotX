#!/usr/bin/env python3
"""Audit PulseSoc push payload sound, vibration, badge, and category contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUSH = (ROOT / "services" / "push_service.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
WEB = (ROOT / "static" / "notifications.js").read_text(encoding="utf-8")
NATIVE = (ROOT / "mobile" / "pulse-react-native" / "services" / "push.ts").read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    for needle, label in [
        ('"sound": os.getenv("PUSH_DEFAULT_SOUND") or "default"', "Expo provider default sound"),
        ('"priority": "high"', "Expo provider high priority"),
        ('"channelId": channel_id', "Expo provider channel id"),
        ('"categoryId": push_type or "pulse"', "Expo provider category id"),
        ('"badge"', "Expo provider badge support"),
        ('"vibrate": [200, 100, 200]', "web push vibration pattern"),
    ]:
        require(needle in PUSH, f"push_service missing {label}", failures)

    for needle, label in [
        ('"sound": metadata.get("sound") or "default"', "service push sound metadata"),
        ('"priority": metadata.get("priority")', "service push priority metadata"),
        ('"badge": int(metadata.get("badge")', "service push badge metadata"),
        ('"category": metadata.get("category") or category', "service push category metadata"),
        ('"vibration": metadata.get("vibration") or [200, 100, 200]', "service push vibration metadata"),
    ]:
        require(needle in SERVICE, f"notification_service missing {label}", failures)

    for needle, label in [
        ("Notification.requestPermission", "web push permission request"),
        ('sound: payload?.sound || "default"', "PulseShell bridge default sound"),
        ("badge: Number(payload?.badge", "PulseShell bridge badge"),
        ("vibrate()", "browser vibration fallback"),
        ("playSound()", "browser sound fallback"),
    ]:
        require(needle in WEB, f"static notifications missing {label}", failures)

    for needle, label in [
        ("Notifications.requestPermissionsAsync", "native permission request"),
        ("shouldPlaySound", "native foreground sound policy"),
        ('sound: "default"', "native default sound"),
        ("enableVibrate: true", "Android vibration channel"),
        ("vibrationPattern", "Android vibration pattern"),
        ("Vibration.vibrate", "native vibration call"),
        ("categoryIdentifier", "native category identifier"),
        ("badge: badge > 0 ? badge : undefined", "native badge sync"),
        ("bypassDnd: false", "OS Focus/DND respect"),
    ]:
        require(needle in NATIVE, f"native push missing {label}", failures)

    if failures:
        print("push sound vibration audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("push sound vibration audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
