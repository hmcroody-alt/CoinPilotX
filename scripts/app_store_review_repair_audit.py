#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path, needle, label):
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"FAIL: {label}")
    print(f"PASS: {label}")


def main():
    require("templates/account.html", '@media (min-width: 761px) and (max-width: 1180px)', "iPad auth breakpoint")
    require("templates/account.html", 'href="/account/delete"', "settings deletion entry")
    require("templates/account.html", 'page == "delete_account"', "account deletion confirmation page")
    require("bot.py", 'def permanently_delete_account(', "backend account deletion")
    require("bot.py", '@webhook_app.route("/api/account/delete"', "account deletion API")
    require("bot.py", "def ios_native_app_request()", "native iOS detection")
    require("bot.py", "Premium purchases are not available in this iOS build.", "iOS external payment restriction")
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
