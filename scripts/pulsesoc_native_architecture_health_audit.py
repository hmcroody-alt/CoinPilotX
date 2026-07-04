#!/usr/bin/env python3
"""Static audit for the PulseSoc native architecture health checkpoint."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    report = read("reports/pulsesoc_native_architecture_health.md")
    progress = read("reports/pulsesoc_native_progress.md")
    cache = read("mobile-native/src/core/cache.ts")
    groups = read("mobile-native/src/api/groups.ts")
    saved = read("mobile-native/src/api/saved.ts")
    marketplace = read("mobile-native/src/api/marketplace.ts")

    for phrase in (
        "does not add a major user-facing feature",
        "Production WebView routes",
        "Duplicated Patterns Found",
        "Consolidation Completed",
        "Recommended Shared-Core Structure",
        "What Should Stay Feature-Local",
        "Device-Only Behavior Not Verified",
        "Native Live Discovery + Live Viewer Foundation",
    ):
        require(phrase in report, f"architecture report missing required section/detail: {phrase}")

    for token in (
        "readJsonCache",
        "writeJsonCache",
        "AsyncStorage.getItem",
        "AsyncStorage.setItem",
        "AsyncStorage.removeItem",
    ):
        require(token in cache, f"shared cache helper missing: {token}")

    for name, source in (("groups", groups), ("saved", saved), ("marketplace", marketplace)):
        require("readJsonCache" in source, f"{name} API must use shared readJsonCache")
        require("writeJsonCache" in source, f"{name} API must use shared writeJsonCache")

    for phrase in (
        "Architecture Health Report + Shared Core Consolidation",
        "Native Live Discovery + Live Viewer Foundation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report missing architecture checkpoint or next recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "architecture checkpoint must not introduce WebView")

    print("PulseSoc native architecture health audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
