#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    report = read("reports/pulsesoc_native_verification_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    screen = read("mobile-native/src/screens/VerificationCenterScreen.tsx")
    api = read("mobile-native/src/api/verification.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")

    for route in (
        "/pulse/verification",
        "/pulse/verification/<track>",
        "/dashboard/account/verification",
    ):
        require(report, route, f"QA route coverage for {route}")

    for entry in (
        "Settings entry point",
        "Profile entry point",
        "Premium entry point",
        "Trust entry point",
        "Document upload handoff guard",
        "Appeal validation guard",
        "No critical",
        "Not Fully Verified",
    ):
        require(report, entry, f"QA report section {entry}")

    for endpoint in (
        "/api/dashboard/account/state",
        "/api/pulse/profile/me",
        "/api/premium/status",
        "/api/dashboard/account/verification/request",
        "/api/dashboard/account/verification/appeal",
        "/api/dashboard/account/verification/document",
    ):
        require(api + report, endpoint, f"server-authoritative endpoint {endpoint}")

    for screen_text in (
        "Verification Center",
        "Start verification request",
        "Choose private document",
        "Submit appeal",
        "Open protected web verification",
        "Start a verification request before uploading private evidence.",
        "Add an appeal note for an existing rejected, suspended, or needs-more-info request.",
    ):
        require(screen, screen_text, f"native verification UI text {screen_text}")

    require(linking, "pulse/verification/:track?", "native verification deep link")
    require(linking, "dashboard/account/verification", "dashboard verification alias")
    require(notification_routing, "verificationRouteTarget", "notification verification routing")
    require(progress, "Native Verification Center Practical QA", "progress update")
    require(progress, "Native Account Health + Appeals Center", "recommended next action")

    forbidden_user_copy = "LogiNexus"
    if forbidden_user_copy in screen or forbidden_user_copy in api:
        raise AssertionError("Internal LogiNexus naming must not appear in native Verification Center user-facing code.")

    print("PulseSoc native verification QA audit passed.")


if __name__ == "__main__":
    main()
