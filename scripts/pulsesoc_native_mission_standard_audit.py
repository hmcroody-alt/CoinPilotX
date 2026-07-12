#!/usr/bin/env python3
"""Guard the mandatory PulseSoc native mission and simulator evidence standard."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"FAIL: missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require_terms(label: str, text: str, terms: list[str]) -> None:
    missing = [term for term in terms if term not in text]
    if missing:
        raise SystemExit(f"FAIL: {label} missing required terms: {', '.join(missing)}")


def main() -> int:
    standard = read("docs/pulsesoc_native_mission_standard.md")
    template = read("reports/pulsesoc_native_mission_report_template.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require_terms("mission standard", standard, [
        "authoritative UI, feature, workflow, backend, and business-logic source",
        "throughout implementation",
        "xcrun simctl list devices available",
        "iPhone 17e", "iPhone 17", "iPhone 17 Pro", "iPhone 17 Pro Max",
        "reports/screenshots/<mission-slug>/",
        "Simulator verified", "Code-path verified", "Mock-state verified", "Physical-device-only",
        "simulator QA percentage was updated honestly",
        "stage only intended files", "push to origin",
    ])
    require_terms("mission report template", template, [
        "Production source-of-truth map", "Production comparison", "Simulator device matrix",
        "Compact", "Standard", "Pro", "Pro Max", "Required state evidence",
        "Offline", "Reconnecting", "Permission denied", "Keyboard open", "Long content",
        "Exact screenshot path", "Simulator QA percentage and calculation",
        "Physical-device release checklist", "Next feature supported",
    ])
    require_terms("native progress report", progress, [
        "Mandatory Native Mission QA Standard",
        "docs/pulsesoc_native_mission_standard.md",
        "reports/pulsesoc_native_mission_report_template.md",
        "scripts/pulsesoc_native_mission_standard_audit.py",
    ])
    print("PulseSoc native mission standard audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
