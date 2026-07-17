#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    layer = read("mobile-native/src/calls/IncomingCallLayer.tsx")
    report = read("reports/pulsesoc_native_home_call_overlay_removal.md")

    require("Voice in progress" not in layer and "Video in progress" not in layer, "active-call mini popup copy must be removed globally", failures)
    require("callBubbleMain" not in layer, "active-call mini popup main Pressable must not mount", failures)
    require("callBubbleEnd" not in layer, "active-call mini popup End button must not mount", failures)
    require("showFloatingCall" not in layer, "route-specific mini popup visibility policy should not remain", failures)
    require("setFloatingCall(connected || null)" in layer, "active call state must remain preserved while visual popup is removed", failures)
    require('navigationRef.navigate("Call"' in layer, "canonical Call route must remain available", failures)
    require('currentRouteName !== "Call"' not in layer, "old one-off Call-only visibility guard should not remain", failures)

    for phrase in [
        "Home never mounts the active-call popup",
        "No bottom padding is reserved",
        "Dedicated Call screen remains canonical",
        "Audio behavior is unchanged",
        "Physical-device-only",
    ]:
        require(phrase in report, f"report missing `{phrase}`", failures)

    if failures:
        print("PulseSoc native Home call overlay audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native Home call overlay audit passed.")
    print("Validated global active-call mini popup removal, preserved active call state, and canonical Call route access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
