from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOBILE_SCREEN = ROOT / "mobile" / "pulse-react-native" / "src" / "screens" / "NotificationsScreen.tsx"
BOT = ROOT / "bot.py"
SERVICE = ROOT / "services" / "notification_service.py"


def require(text, needle, label, failures):
    if needle not in text:
        failures.append(f"{label} missing: {needle}")


def main():
    failures = []
    screen = MOBILE_SCREEN.read_text(encoding="utf-8") if MOBILE_SCREEN.exists() else ""
    bot = BOT.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    require(screen, "preview_text", "mobile preview rendering", failures)
    require(screen, "original_preview", "mobile original context", failures)
    require(screen, "sendQuickReply", "mobile quick reply handler", failures)
    require(screen, "/api/pulse/status/${statusId}/reply", "status quick reply endpoint", failures)
    require(screen, "/api/pulse/posts/${postId}/comments", "post quick reply endpoint", failures)
    require(screen, "/api/pulse/messages/send", "message quick reply endpoint", failures)
    require(screen, "Mark Read", "mark read action", failures)
    require(screen, "Delete", "delete action", failures)
    require(service, "mobile_deep_link", "backend mobile deep links", failures)
    require(service, "original_preview", "backend original content preview", failures)
    require(bot, "status_reply", "status reply notification type", failures)
    require(bot, "comment_preview", "comment preview payload", failures)
    require(bot, "message_preview", "message preview payload", failures)
    if failures:
        print("mobile notification reply audit failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("mobile notification reply audit passed.")


if __name__ == "__main__":
    main()
