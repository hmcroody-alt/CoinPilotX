#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "mobile-native/src/screens/HomeScreen.tsx"
NAV = ROOT / "mobile-native/src/navigation/GlobalNavigation.tsx"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> None:
    home = HOME.read_text()
    nav = NAV.read_text()

    require(
        "useBottomNavScrollVisibility({ enabled: posts.length > 0 })" in home,
        "Home empty/loading/error states must not hide the bottom dock and expose a black cover.",
    )
    require(
        "paddingBottom: 172" in home,
        "Home content must reserve legitimate dock clearance so empty/feed cards are not covered.",
    )
    require(
        'pointerEvents={hidden ? "none" : "box-none"}' in nav,
        "Bottom navigation shell must not intercept touches outside the visible dock.",
    )
    require(
        '<View pointerEvents="auto" style={styles.bottomPanel}>' in nav,
        "Only the visible dock panel should receive bottom navigation touches.",
    )
    require(
        "backgroundColor: \"transparent\"" in nav and "bottomShell" in nav,
        "Bottom navigation shell must remain transparent instead of mounting a black cover.",
    )
    print("PASS: PulseSoc native Home black bottom cover audit passed.")


if __name__ == "__main__":
    main()
