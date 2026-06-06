from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = ROOT / "reports" / "pulse_native_app_foundation.md"
bot = (ROOT / "bot.py").read_text()

checks = {
    "native report": report.exists(),
    "existing pulse APIs": "/api/pulse/notifications" in bot and "/pulse/messages" in bot and "/pulse/videos" in bot,
    "push token/device storage": "pulse_notification_devices" in bot,
    "no native project started yet": not (ROOT / "mobile").exists() and not (ROOT / "react-native").exists(),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse native app foundation audit failed: " + ", ".join(failed))
print("pulse native app foundation audit ok")
