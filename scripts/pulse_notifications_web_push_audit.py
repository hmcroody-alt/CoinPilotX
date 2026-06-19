from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()
js = (ROOT / "static" / "notifications.js").read_text()
sw = (ROOT / "static" / "service-worker.js").read_text()
push = (ROOT / "services" / "push_service.py").read_text()

checks = {
    "public key api": "/api/push/public-key" in bot,
    "subscribe api": "/api/push/subscribe" in bot,
    "unsubscribe api": "/api/push/unsubscribe" in bot,
    "device table": "pulse_notification_devices" in bot,
    "save device": "save_pulse_device" in bot and "def save_pulse_device" in (ROOT / "services" / "notification_service.py").read_text(),
    "permission prompt on action": "Notification.requestPermission()" in js and "enable-push" in js,
    "service worker push": 'self.addEventListener("push"' in sw,
    "click deep link": "/pulse/notifications" in sw,
    "vapid readiness": "VAPID_PUBLIC_KEY" in push and "VAPID_PRIVATE_KEY" in push,
    "expo native token detection": "_is_expo_token" in push and "ExpoPushToken[" in push,
    "expo native delivery": "https://exp.host/--/api/v2/push/send" in push and "PUSH_DEFAULT_SOUND" in push,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification web push audit failed: " + ", ".join(failed))
print("pulse notification web push audit ok")
