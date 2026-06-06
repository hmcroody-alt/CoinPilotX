from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()
service = (ROOT / "services" / "notification_service.py").read_text()

required = ["messages", "comments", "likes", "mentions", "follows", "lives", "roast_battle", "premium", "security"]
checks = {
    "preference table": "pulse_notification_preferences" in bot,
    "preference function": "def pulse_preferences" in service and "def update_pulse_preferences" in service,
    "settings page": "/pulse/settings/notifications" in bot,
    "channels": all(channel in bot for channel in ["in_app", "push", "email", "sms"]),
    "security recommended": "Recommended on" in bot and 'category == "security"' in service,
    "all categories": all(category in service for category in required),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification preferences audit failed: " + ", ".join(failed))
print("pulse notification preferences audit ok")
