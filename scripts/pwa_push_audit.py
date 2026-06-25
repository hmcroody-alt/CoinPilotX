#!/usr/bin/env python3
"""Audit PulseSoc PWA Web Push wiring without requiring live VAPID secrets."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> int:
    bot = read("bot.py")
    notifications_js = read("static/notifications.js")
    service_worker = read("static/service-worker.js")
    push_service = read("services/push_service.py")

    require("/api/push/public-key" in bot, "PWA public VAPID key endpoint exists")
    require("/api/push/subscribe" in bot and "/api/push/unsubscribe" in bot, "PWA subscribe/unsubscribe endpoints exist")
    require("Notification.requestPermission()" in notifications_js, "PWA asks notification permission from a user action")
    require("navigator.serviceWorker.register(\"/static/service-worker.js\")" in notifications_js, "PWA registers the push service worker")
    require("registration.pushManager.subscribe" in notifications_js, "PWA creates browser push subscriptions")
    require("applicationServerKey: urlBase64ToUint8Array(keyPayload.public_key)" in notifications_js, "PWA uses the server VAPID public key")
    require('self.addEventListener("push"' in service_worker, "Service worker handles push events")
    require('self.addEventListener("notificationclick"' in service_worker, "Service worker handles notification click events")
    require("conversationId ? `/pulse/messages/${conversationId}`" in service_worker, "PWA chat notification click opens the exact conversation")
    require("VAPID_PUBLIC_KEY" in push_service and "VAPID_PRIVATE_KEY" in push_service, "WebPush provider checks VAPID variables at send time")
    require("pywebpush" in push_service and "webpush(" in push_service, "WebPush provider sends through pywebpush when configured")
    require("VAPID push variables are not configured." in push_service, "Missing VAPID has an explicit safe failure reason")
    require("COMMAND_CENTER_INTERNAL_TOKEN" not in notifications_js + service_worker, "PWA browser code never exposes internal worker token")
    print("pwa_push_audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
