from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bot = (ROOT / "bot.py").read_text()
notifications_js = (ROOT / "static" / "notifications.js").read_text()

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
    "open action resolves safely": "/api/pulse/notifications/${noteId}/resolve" in notifications_js and "safeInternalUrl" in notifications_js,
    "frontend route allowlist": "NOTIFICATION_ROUTE_PREFIXES" in notifications_js and "url.pathname.startsWith(`${prefix}/`)" in notifications_js,
    "mark read updates badges": "markReadAction" in notifications_js and "applyBadgeCounts(payload)" in notifications_js,
    "delete action removes cards": "deleteNoteAction" in notifications_js and "pruneEmptyNotificationSections" in notifications_js,
    "backend action counts": "badge_counts" in bot and "pulse_badge_counts(user" in bot,
    "bad target logging": "PULSE_NOTIFICATION_BAD_TARGET" in bot,
    "route allowlist": "PULSE_NOTIFICATION_ALLOWED_ROUTE_PREFIXES" in bot and "path.startswith(f\"{prefix}/\")" in bot,
    "safe fallback routes": all(route in bot for route in ('"/pulse/alerts/<path:alert_id>"', '"/pulse/purchases/<path:purchase_id>"', '"/account/security"', '"/pulse/status/<path:status_id>"')),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification UI audit failed: " + ", ".join(failed))
print("pulse notification ui audit ok")
