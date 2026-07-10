#!/usr/bin/env python3
"""Audit the PulseSoc native authenticated wiring QA pass."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_token(text: str, token: str, label: str, failures: list[str]) -> None:
    require(token in text, f"{label} missing token: {token}", failures)


def forbid_secret_like(text: str, label: str, failures: list[str]) -> None:
    forbidden_patterns = [
        r"AuthWireQA-[A-Za-z0-9_-]+",
        r"password\\s*[:=]\\s*[^\\s`]+",
        r"pulsesoc\\.qa\\.password",
    ]
    for pattern in forbidden_patterns:
        require(not re.search(pattern, text, flags=re.IGNORECASE), f"{label} appears to contain credential text matching {pattern}", failures)


def main() -> None:
    failures: list[str] = []

    linking = read("mobile-native/src/navigation/linking.ts")
    types = read("mobile-native/src/navigation/types.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    proxy = read("scripts/pulsesoc_native_local_qa_proxy.py")
    qa_report = read("reports/pulsesoc_native_authenticated_wiring_qa.md")
    route_matrix = read("reports/pulsesoc_native_route_matrix.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require_token(linking, 'TrustSafetySupport: "pulse/support"', "linking", failures)
    require_token(linking, 'CreatorStudioAlias: "pulse/creator"', "linking", failures)
    require_token(types, "CreatorStudioAlias: undefined", "navigation types", failures)
    require_token(navigator, 'name="CreatorStudioAlias"', "navigator", failures)
    require_token(home, 'route: "/pulse/creator-studio"', "Home drawer", failures)

    for token in ["Access-Control-Allow-Origin", "Access-Control-Allow-Credentials", "def do_HEAD", "ThreadingHTTPServer"]:
        require_token(proxy, token, "local QA proxy", failures)

    for token in [
        "PulseSoc Native Authenticated Wiring QA",
        "37 representative authenticated routes",
        "Creator shorthand alias",
        "Support alias",
        "Back navigation",
        "No Chrome Incognito",
    ]:
        require_token(qa_report, token, "authenticated wiring QA report", failures)

    for token in [
        "| Surface | Requested route | Result | Classification | Notes |",
        "/pulse/creator",
        "/pulse/support",
        "/pulse/dashboard/module/system-status/feed_status",
        "Provider fallback boundary",
    ]:
        require_token(route_matrix, token, "route matrix", failures)

    require_token(progress, "Native Authenticated Wiring QA Pass", "native progress", failures)
    require_token(progress, "Current native migration: 96%", "native progress", failures)
    require_token(progress, "Recommended next mission: PulseSoc Native Messenger Foundation Replacement QA", "native progress", failures)

    for label, text in {
        "authenticated wiring QA report": qa_report,
        "route matrix": route_matrix,
        "native progress": progress,
    }.items():
        forbid_secret_like(text, label, failures)

    if failures:
        raise AssertionError("\\n".join(failures))

    print("PulseSoc native authenticated wiring QA audit passed.")


if __name__ == "__main__":
    main()
