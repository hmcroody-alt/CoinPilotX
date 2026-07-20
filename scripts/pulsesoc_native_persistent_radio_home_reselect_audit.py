#!/usr/bin/env python3
"""Static release gate for persistent radio and Home tab reselect behavior."""

from __future__ import annotations

import json
import plistlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="ignore")


def require(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []
    pulse_radio = read("mobile-native/src/core/pulseRadio.ts")
    coordinator = read("mobile-native/src/core/mediaPlaybackCoordinator.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    nav = read("mobile-native/src/navigation/GlobalNavigation.tsx")
    music = read("mobile-native/src/screens/MusicScreen.tsx")
    voice = read("mobile-native/src/core/voiceMessagePlayback.ts")
    call = read("mobile-native/src/screens/CallScreen.tsx")
    reels = read("mobile-native/src/screens/ReelsScreen.tsx")

    app_json = json.loads(read("mobile-native/app.json"))
    info_plist_path = ROOT / "mobile-native/ios/PulseSocNative/Info.plist"
    info = None
    if info_plist_path.exists():
        with info_plist_path.open("rb") as handle:
            info = plistlib.load(handle)

    require("app.json declares UIBackgroundModes audio", "audio" in app_json["expo"]["ios"]["infoPlist"].get("UIBackgroundModes", []), failures)
    if info is not None:
        require("generated Info.plist declares UIBackgroundModes audio", "audio" in info.get("UIBackgroundModes", []), failures)
    require("radio does not pause on AppState background", "AppState.addEventListener" not in pulse_radio, failures)
    require("radio audio mode stays active in background", "staysActiveInBackground: true" in pulse_radio, failures)
    require("radio disables ducking for app media clarity", "shouldDuckAndroid: false" in pulse_radio, failures)
    require("radio subscribes to centralized coordinator", "subscribeMediaPlayback" in pulse_radio, failures)
    require("radio tracks user playback intent", "userWantsPlayback" in pulse_radio and "interruptedBy" in pulse_radio, failures)
    require("radio schedules bounded resume", "scheduleRadioResume" in pulse_radio and "setTimeout" in pulse_radio, failures)
    require("coordinator preserves radio in background", "activeOwner?.kind === \"radio\"" in coordinator, failures)
    require("voice messages do not directly clear radio user intent", "pausePulseRadio" not in voice, failures)
    require("calls do not directly clear radio user intent", "pausePulseRadio" not in call, failures)
    require("reels do not force-pause radio on screen entry", "pausePulseRadio" not in reels, failures)
    require("music screen observes persistent radio state", "subscribePulseRadio" in music and "getPulseRadioState" in music, failures)
    require("music screen controls persistent radio", "togglePulseRadio" in music, failures)
    require("home registers reselect handler", "registerHomeReselectHandler" in home, failures)
    require("home reselect scrolls FlatList to top", "scrollToOffset({ offset: 0" in home, failures)
    require("home reselect refreshes existing feed route", "await load(\"refresh\")" in home, failures)
    require("home refresh is single-flight", "refreshingRef.current" in home and "mode === \"refresh\" && refreshingRef.current" in home, failures)
    require("bottom nav triggers Home reselect", "triggerHomeReselect" in nav and "item.name === \"Home\" && active" in nav, failures)

    # Guard against reintroducing the old component-owned radio failure mode.
    require("MusicScreen no longer owns radio Audio.Sound", not re.search(r"radioSound|radioRef|pulseRadioSound", music), failures)

    if failures:
        print("PulseSoc persistent radio/Home reselect audit: FAIL")
        for item in failures:
            print(f"- {item}")
        return 1
    print("PulseSoc persistent radio/Home reselect audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
