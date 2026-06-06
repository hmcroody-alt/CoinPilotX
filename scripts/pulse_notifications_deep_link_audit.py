from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()
service = (ROOT / "services" / "notification_service.py").read_text()
sw = (ROOT / "static" / "sw.js").read_text()

checks = {
    "deep link column": "deep_link TEXT" in bot,
    "target fallback": "target_url" in bot and "item[\"deep_link\"]" in service,
    "open note link": "data-open-note" in bot,
    "service worker click": "notificationclick" in sw,
    "pulse fallback route": "/pulse/notifications" in sw,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification deep link audit failed: " + ", ".join(failed))
print("pulse notification deep link audit ok")
