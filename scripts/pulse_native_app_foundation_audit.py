#!/usr/bin/env python3
"""Audit the Pulse React Native foundation scaffold."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "pulse-react-native"


def read(path):
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing {path}")
    return target.read_text(encoding="utf-8")


bot = read("bot.py")
app = read("mobile/pulse-react-native/src/App.tsx")
auth = read("mobile/pulse-react-native/src/auth/AuthProvider.tsx")
auth_screen = read("mobile/pulse-react-native/src/screens/AuthScreen.tsx")
notifications = read("mobile/pulse-react-native/src/notifications/notifications.ts")
linking = read("mobile/pulse-react-native/src/navigation/linking.ts")
config = read("mobile/pulse-react-native/src/config.ts")
package_json = read("mobile/pulse-react-native/package.json")

checks = {
    "react native package": '"react-native"' in package_json and '"expo"' in package_json,
    "no store native builds yet": not (MOBILE / "ios").exists() and not (MOBILE / "android").exists(),
    "auth api endpoints": "/api/mobile/auth/login" in bot and "/api/mobile/auth/register" in bot and "/api/mobile/auth/recover" in bot,
    "session persistence": "SecureStore" in read("mobile/pulse-react-native/src/api/client.ts") and "/api/mobile/auth/session" in auth,
    "login register recover UI": 'mode === "register"' in auth_screen and 'mode === "recover"' in auth_screen,
    "required navigation tabs": all(name in app for name in ["Feed", "Reels", "Videos", "Messages", "Notifications", "Profile"]),
    "existing pulse APIs reused": all(endpoint in app for endpoint in ["/api/pulse/feed", "/api/pulse/reels/feed", "/api/pulse/videos", "/api/pulse/messages/conversations", "/api/pulse/notifications", "/api/pulse/profile/me"]),
    "profile API": "/api/pulse/profile/me" in bot,
    "push token registration": "getExpoPushTokenAsync" in notifications and "/api/push/subscribe" in notifications,
    "deep links": "PULSE_DEEP_LINK_PREFIXES" in linking and "pulsesoc.com" in config and "coinpilotx.app" in config,
    "readiness reports": (ROOT / "reports" / "pulse_react_native_foundation.md").exists() and (ROOT / "reports" / "pulse_mobile_launch_readiness.md").exists(),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse native app foundation audit failed: " + ", ".join(failed))
print("pulse native app foundation audit ok")
