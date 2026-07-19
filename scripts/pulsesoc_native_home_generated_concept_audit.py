#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "mobile-native/src/screens/HomeScreen.tsx": [
        "PulseNetworkHero",
        "HomePulseComposer",
        "StatusRail",
        "heroRadioPill",
        "heroQuickRow",
        "homeAtmosphereRoot",
        "PulseRadioHeroControl",
        "onOpenUndx",
        "onOpenSafety",
    ],
    "mobile-native/src/components/HomePulseComposer.tsx": [
        "collapsedQuickRow",
        "collapsedQuickTools",
        "collapsedCreateButton",
        "media.chooseImages",
        "media.chooseVideo",
        "onOpenCamera",
        "createPost",
        "createReel",
    ],
    "mobile-native/src/navigation/GlobalNavigation.tsx": [
        "LogiNexusBottomNavigation",
        "LogiNexusGlobalHeader",
        "navigation.navigate(\"Home\", { openComposer: true })",
        "global-bottom-navigation",
    ],
    "reports/pulsesoc_native_home_generated_concept_mapping.md": [
        "Existing Implementations Reused",
        "Wiring Preservation",
        "Performance Decision",
    ],
}


def main() -> int:
    missing = []
    for rel, needles in REQUIRED.items():
        path = ROOT / rel
        if not path.exists():
            missing.append(f"missing file: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                missing.append(f"{rel}: missing {needle}")
    if missing:
        print("PulseSoc native Home generated concept audit failed:")
        for item in missing:
            print(f"- {item}")
        return 1
    print("PulseSoc native Home generated concept audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
