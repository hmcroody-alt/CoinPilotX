#!/usr/bin/env python3
"""Audit PulseSoc foreground Intelligence notification behavior."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


CHECKS = [
    (
        "active service worker hands foreground Intelligence pushes to clients",
        "static/service-worker.js",
        [
            "PULSESOC_FOREGROUND_NOTIFICATION",
            "postForegroundNotification(notification)",
            "isForegroundClient(client)",
            "self.registration.showNotification(notification.title, notification.options)",
        ],
    ),
    (
        "legacy root service worker matches foreground handoff behavior",
        "static/sw.js",
        [
            "PULSESOC_FOREGROUND_NOTIFICATION",
            "postForegroundNotification(notification)",
            "isForegroundClient(client)",
            "self.registration.showNotification(notification.title, notification.options)",
        ],
    ),
    (
        "notification controller has one-at-a-time in-app banner queue",
        "static/notifications.js",
        [
            "foregroundQueue",
            "foregroundVisible",
            "ensureForegroundBannerHost()",
            "enqueueForegroundNotification(payload)",
            "STATE.foregroundQueue.length > 8",
            "data-pulse-foreground-banner",
        ],
    ),
    (
        "foreground banner is contextual and non-blocking",
        "static/notifications.js",
        [
            "foregroundContext()",
            'return "media"',
            'return "messaging"',
            "STATE.foregroundTimer = window.setTimeout",
            "requestAnimationFrame(() => host.classList.add(\"is-visible\"))",
            "transform:translate3d",
        ],
    ),
    (
        "critical security-style notifications require acknowledgement",
        "static/notifications.js",
        [
            "foregroundIsCritical(payload)",
            "data-pulse-foreground-ack",
            "Acknowledge",
            "if (!critical)",
        ],
    ),
    (
        "foreground alerts still cue sound and vibration",
        "static/notifications.js",
        [
            "playSound();",
            "vibrate();",
            "navigator.vibrate",
        ],
    ),
    (
        "service-worker messages are consumed by page notification UI",
        "static/notifications.js",
        [
            "navigator.serviceWorker.addEventListener(\"message\"",
            "PULSESOC_FOREGROUND_NOTIFICATION",
            "refreshNotificationList().catch",
            "pollNotifications({ refreshList: false, force: true }).catch",
        ],
    ),
    (
        "implementation report exists",
        "reports/pulsesoc_foreground_notifications.md",
        [
            "Non-Intrusive Foreground Notifications",
            "Background and locked-screen behavior",
            "QA Results",
        ],
    ),
]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    return path.read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    for name, relative, required in CHECKS:
        try:
            text = read(relative)
        except FileNotFoundError:
            failures.append(f"{name}: missing {relative}")
            continue
        missing = [needle for needle in required if needle not in text]
        if missing:
            failures.append(f"{name}: {relative} missing {', '.join(missing)}")

    for relative in ("static/service-worker.js", "static/sw.js"):
        try:
            text = read(relative)
        except FileNotFoundError:
            continue
        if "showNotification" not in text:
            failures.append(f"{relative}: background/locked-screen showNotification path missing")
        if "visibleClients.length" not in text:
            failures.append(f"{relative}: foreground client visibility check missing")

    if failures:
        print("PulseSoc foreground notification audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc foreground notification audit passed")
    print(f"Checked {len(CHECKS)} implementation areas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
