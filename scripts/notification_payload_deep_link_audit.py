from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def main():
    checks = {
        "actor_name payload": "actor_name",
        "actor_avatar payload": "actor_avatar",
        "preview text payload": "preview_text",
        "original preview payload": "original_preview",
        "post deep link": "pulse://post/",
        "status deep link": "pulse://status/",
        "message deep link": "pulse://pulse/messages-v2?conversation=",
        "comment id payload": "comment_id",
        "reply id payload": "reply_id",
        "conversation id payload": "conversation_id",
        "safe unavailable fallback": "Reply hidden or unavailable.",
    }
    failures = []
    combined = SERVICE + "\n" + BOT + "\n" + (ROOT / "mobile" / "pulse-react-native" / "src" / "screens" / "NotificationsScreen.tsx").read_text(encoding="utf-8")
    for label, needle in checks.items():
        if needle not in combined:
            failures.append(f"{label} missing")
    if failures:
        print("notification payload deep link audit failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("notification payload deep link audit passed.")


if __name__ == "__main__":
    main()
