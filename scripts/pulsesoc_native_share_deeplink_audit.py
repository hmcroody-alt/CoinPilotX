#!/usr/bin/env python3
"""Static release gate for PulseSoc native metadata sharing and object links."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main() -> None:
    app = json.loads((NATIVE / "app.json").read_text(encoding="utf-8"))["expo"]
    ios = app.get("ios") or {}
    android = app.get("android") or {}
    linking = (NATIVE / "src/navigation/linking.ts").read_text(encoding="utf-8")
    sharing = (NATIVE / "src/sharing/nativeShare.ts").read_text(encoding="utf-8")
    backend = (ROOT / "bot.py").read_text(encoding="utf-8")
    association_service = (ROOT / "services/native_app_links.py").read_text(encoding="utf-8")
    production_entitlements = (NATIVE / "ios/PulseSocNative/PulseSocNative.entitlements").read_text(encoding="utf-8")
    development_entitlements = (NATIVE / "ios/PulseSocNative/PulseSocNative.dev.entitlements").read_text(encoding="utf-8")
    native_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (NATIVE / "src").rglob("*")
        if path.suffix in {".ts", ".tsx"} and "__tests__" not in path.parts
    )

    require('"pulsesoc://"' in linking, "custom-scheme deep links remain registered")
    require('"https://pulsesoc.com"' in linking, "production universal-link origin is registered")
    require("applinks:pulsesoc.com" in (ios.get("associatedDomains") or []), "iOS associated domain is configured")
    require("applinks:pulsesoc.com" in production_entitlements, "production Xcode entitlement includes the associated domain")
    require("applinks:pulsesoc.com" in development_entitlements, "development Xcode entitlement includes the associated domain")
    require('"/.well-known/apple-app-site-association"' in backend, "Apple association endpoint is registered")
    require('"/.well-known/assetlinks.json"' in backend, "Android association endpoint is registered")
    require("PULSESOC_APPLE_TEAM_ID" in association_service, "Apple association uses deployment-owned Team ID configuration")
    require("PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS" in association_service, "Android association uses deployment-owned signing fingerprints")

    filters = android.get("intentFilters") or []
    verified_https = any(
        item.get("action") == "VIEW"
        and item.get("autoVerify") is True
        and any(
            data.get("scheme") == "https"
            and data.get("host") == "pulsesoc.com"
            and data.get("pathPrefix") == "/pulse"
            for data in item.get("data") or []
        )
        for item in filters
    )
    require(verified_https, "Android verified PulseSoc HTTPS intent filter is configured")
    require("Share.share" in sharing, "native operating-system share sheet has one canonical adapter")
    require("author" in sharing and "description" in sharing and "title" in sharing, "share adapter composes human-readable metadata")
    require("Share.share({ message:" not in native_sources, "URL-only native share calls are removed")
    require("/pulse/status/${encodeURIComponent" in (NATIVE / "src/api/status.ts").read_text(encoding="utf-8"), "Status uses its canonical object path")
    require("/pulse/live/${encodeURIComponent" in (NATIVE / "src/api/live.ts").read_text(encoding="utf-8"), "Live uses its canonical object path")

    print("PASS: PulseSoc native share and deep-link static release gate")


if __name__ == "__main__":
    main()
