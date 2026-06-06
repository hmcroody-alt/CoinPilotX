from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()
service = (ROOT / "services" / "notification_service.py").read_text()

checks = {
    "list api": '@webhook_app.route("/api/pulse/notifications", methods=["GET"])' in bot,
    "unread api": "/api/pulse/notifications/unread-count" in bot,
    "read api": "/api/pulse/notifications/<int:notification_id>/read" in bot,
    "read all api": "/api/pulse/notifications/read-all" in bot,
    "delete api": 'methods=["DELETE"]' in bot and "delete_pulse_notification" in bot,
    "preferences api": "/api/pulse/notifications/preferences" in bot,
    "service list": "def list_pulse_notifications" in service,
    "service create": "def create_pulse_notification" in service,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification API audit failed: " + ", ".join(failed))
print("pulse notification api audit ok")
