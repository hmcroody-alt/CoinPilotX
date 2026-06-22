#!/usr/bin/env python3
from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def require(path, needle, label):
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"FAIL: {label}")
    print(f"PASS: {label}")


def main():
    active_app = json.loads((ROOT / "mobile/pulse-react-native/app.json").read_text(encoding="utf-8"))["expo"]
    legacy_app = json.loads((ROOT / "mobile/app.json").read_text(encoding="utf-8"))["expo"]
    if active_app.get("ios", {}).get("supportsTablet") is not False:
        raise SystemExit("FAIL: active iOS config must be iPhone-only")
    print("PASS: active iOS config is iPhone-only")
    if legacy_app.get("ios", {}).get("supportsTablet") is not False:
        raise SystemExit("FAIL: legacy iOS config must not advertise tablet support")
    print("PASS: legacy iOS config is iPhone-only")
    require("templates/account.html", '@media (min-width: 761px) and (max-width: 1180px)', "iPad auth breakpoint")
    require("templates/account.html", 'href="/account/delete"', "settings deletion entry")
    require("templates/account.html", 'page == "delete_account"', "account deletion confirmation page")
    require("bot.py", "Password confirmation is required before deletion completes.", "Pulse settings deletion entry")
    require("bot.py", 'def permanently_delete_account(', "backend account deletion")
    require("bot.py", '@webhook_app.route("/api/account/delete"', "account deletion API")
    require("bot.py", "def ios_native_app_request()", "native iOS detection")
    require("bot.py", "def ios_paid_digital_unavailable_response(", "shared iOS paid digital restriction")
    require("bot.py", "Paid digital access is not available in this iOS build.", "iOS paid digital restriction copy")
    require("bot.py", "@webhook_app.route(\"/api/pulse/payments/checkout\"", "creator checkout route exists")
    require("bot.py", "if ios_native_app_request():\n        return ios_paid_digital_unavailable_response(api=True)", "iOS checkout APIs blocked")
    require("mobile/pulse-react-native/App.tsx", "PulseSocNativeApp/1.0", "native WebView identification")
    require("mobile/pulse-react-native/App.tsx", "applicationNameForUserAgent={PULSESOC_NATIVE_USER_AGENT}", "WebView user agent applied")
    for name in (
        "01-menu-utility.png",
        "02-menu-primary.png",
        "03-videos-menu.png",
        "04-home-feed.png",
        "05-login.png",
        "06-welcome.png",
    ):
        screenshot = ROOT / "reports" / "app-store-screenshots" / "iphone-65" / name
        if not screenshot.exists():
            raise SystemExit(f"FAIL: App Store screenshot missing: {name}")
    print("PASS: App Store screenshot set")
    print("PASS: App Store review repair audit")


if __name__ == "__main__":
    main()
