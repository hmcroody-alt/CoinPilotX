from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()

checks = {
    "center route": '@webhook_app.route("/pulse/notifications"' in bot,
    "settings route": '@webhook_app.route("/pulse/settings/notifications"' in bot,
    "bell badge": "data-notification-unread" in bot,
    "header notifications": "data-header-notifications" in bot and "pulse-bell-icon" in bot and "pulse-topnav-messages" not in bot,
    "sectioned center": "Priority" in bot and "Earlier This Week" in bot and "pulse-notification-section" in bot,
    "filters": all(label in bot for label in ("All", "Priority", "Social", "Live", "Crypto", "Security", "Marketplace", "System")),
    "deep link open": "data-open-note" in bot,
    "mark read action": "data-read-note" in bot,
    "delete action": "data-delete-note" in bot,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification UI audit failed: " + ", ".join(failed))
print("pulse notification ui audit ok")
