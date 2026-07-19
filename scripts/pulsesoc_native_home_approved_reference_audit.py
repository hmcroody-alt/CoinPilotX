#!/usr/bin/env python3
"""Static release gate for the approved native PulseSoc Home reference."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(f"missing {label}: {needle}")


def forbid(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle in text:
        failures.append(f"forbidden {label}: {needle}")


def main() -> int:
    failures: list[str] = []
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    navigation = read("mobile-native/src/navigation/GlobalNavigation.tsx")
    radio = read("mobile-native/src/core/pulseRadio.ts")
    app = read("mobile-native/App.tsx")
    package = read("mobile-native/package.json")

    for needle, label in (
        ('label: "For You"', "For You feed"),
        ('label: "Following"', "Following feed"),
        ('label: "Friends"', "Friends feed"),
        ('label: "Communities"', "Communities feed"),
        ("listFeed", "production feed API"),
        ("listStatuses", "production Status API"),
        ("HomePulseComposer", "canonical Home composer"),
        ('pointerEvents="none" style={styles.heroAtmosphere}', "non-interactive hero decoration"),
        ("useIsFocused()", "route focus motion gate"),
        ("AccessibilityInfo.isReduceMotionEnabled", "Reduce Motion gate"),
        ("Battery.useLowPowerMode()", "Low Power Mode gate"),
        ('AppState.currentState === "active"', "foreground motion gate"),
        ("useNativeDriver: true", "native-driver animation"),
        ("PulseRadioHeroControl", "isolated radio control"),
        ('accessibilityLabel={`Pulse Radio, ${stateLabel}`}', "truthful radio accessibility state"),
        ('title={loading ? "Opening the PulseSoc network"', "first-content loading state below filters"),
    ):
        require(home, needle, label, failures)

    require(composer, "CREATE A SIGNAL", "approved composer title", failures)
    require(composer, "publishing || hasDraft", "draft-only indicator", failures)
    require(composer, "media.chooseImages", "production photo picker", failures)
    require(composer, "media.chooseVideo", "production video picker", failures)
    require(composer, "onOpenCamera", "production camera route", failures)
    require(composer, "initiallyExpanded = false", "collapsed composer default", failures)

    tab_needles = [
        'name: "Home", routeName: "Home", label: "Home"',
        'name: "Reels", routeName: "Reels", label: "Reels"',
        'name: "Create", routeName: "Create", label: "Create"',
        'name: "Messenger", routeName: "Messenger", label: "Messages"',
        'name: "Profile", routeName: "Profile", label: "Profile"',
    ]
    positions = []
    for needle in tab_needles:
        require(navigation, needle, "approved bottom navigation", failures)
        positions.append(navigation.find(needle))
    if all(position >= 0 for position in positions) and positions != sorted(positions):
        failures.append("approved bottom-navigation order changed")
    forbid(navigation, 'label: "Alerts"', "Alerts bottom tab", failures)
    require(navigation, "expo-haptics", "brief selection haptics", failures)
    require(navigation, 'testID="global-bottom-navigation"', "canonical navigation mount", failures)

    require(radio, 'status: "paused"', "paused radio initial state", failures)
    require(radio, 'message: "Tap to play"', "paused radio copy", failures)
    require(radio, "export async function togglePulseRadio", "explicit radio intent", failures)
    require(radio, 'if (next !== "active"', "radio background pause", failures)
    forbid(home, "playPulseRadio(", "Home radio autoplay", failures)

    require(package, '"expo-battery"', "Low Power Mode dependency", failures)
    require(package, '"expo-haptics"', "native haptics dependency", failures)

    # The global incoming-call layer may retain full-screen incoming-call handling,
    # but release source must not mount a mini/global active-call controller.
    forbid(app, "Active PulseSoc Call", "global active-call banner", failures)
    forbid(app, "Voice in progress", "global active-call mini-controller", failures)
    forbid(home, "homeMiniPlayer", "Home mini-player", failures)
    forbid(home, "No status yet</Text>", "repeated fake Status circles", failures)
    forbid(home, ">Optimal<", "fabricated network health label", failures)
    forbid(home, ">LN<", "internal-only visible identity", failures)

    report = ROOT / "reports/pulsesoc_native_home_approved_reference_2026-07-18.md"
    if not report.exists():
        failures.append("missing approved-reference implementation report")

    if failures:
        print("PulseSoc native Home approved-reference audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native Home approved-reference audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
