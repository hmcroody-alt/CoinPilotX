#!/usr/bin/env python3
"""Audit the PulseSoc Native Seller/Store practical QA hardening."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "mobile-native/App.tsx",
        "mobile-native/src/session/qaSimulatorAuth.ts",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "reports/pulsesoc_native_seller_store_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    app = read("mobile-native/App.tsx")
    qa_auth = read("mobile-native/src/session/qaSimulatorAuth.ts")
    seller = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    report = read("reports/pulsesoc_native_seller_store_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "pendingQaRedirectTarget",
        "routeNotificationTarget(pendingQaRedirectTarget)",
        "tryHandleQaSimulatorAuthUrl",
    ]:
        require(token in app, f"App missing QA redirect handling token {token}", failures)

    for token in [
        "__DEV__ && isLocalApiBaseUrl(PULSE_API_BASE_URL)",
        "isLocalWebHost(parsed.hostname)",
        'parsed.pathname === "/qa/simulator-login"',
        "safeRedirectTarget",
        "!redirect.startsWith(\"/\")",
        'redirect.startsWith("/api/")',
        'redirect.startsWith("/admin/")',
        "signIn(identifier.trim(), password)",
    ]:
        require(token in qa_auth, f"QA auth helper missing safe boundary token {token}", failures)

    for token in [
        "Open store media",
        "accessibilityLabel={`Open store media ${index + 1}`}",
        "mediaOverlay",
        "NativeMediaViewer",
    ]:
        require(token in seller, f"SellerStoreScreen missing QA media accessibility token {token}", failures)

    for token in [
        "PulseSoc Native Seller/Store Practical QA Hardening",
        "Authenticated QA Results",
        "Backend Contract Finding",
        "GET /api/pulse/marketplace/search?limit=5",
        "did not expose `cover_image_url`",
        "No critical, security, data-loss, production-breaking",
        "Native Marketplace/Seller Media Payload Contract Hardening",
    ]:
        require(token in report, f"QA report missing {token}", failures)

    for token in [
        "Native Seller/Store Practical QA Hardening",
        "Native Completion Snapshot by Subsystem",
        "Native Marketplace/Seller Media Payload Contract Hardening",
    ]:
        require(token in progress, f"master progress missing {token}", failures)

    source_bundle = "\n".join([app, qa_auth, seller])
    require("LogiNexus" not in source_bundle, "internal LogiNexus name leaked into native source", failures)
    require("git add ." not in report + "\n" + progress, "reports should not instruct git add .", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native seller store QA audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
