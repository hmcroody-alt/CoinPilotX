#!/usr/bin/env python3
"""Audit safe legal-name correction without touching operational identifiers."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OLD_EXACT = ("CoinPilotXAI Inc.", "CoinPilotXAI Inc", "COINPILOTXAI INC.", "COINPILOTXAI INC")
REQUIRED_NEW = "CoinPlotXAI Inc."
SKIP_DIRS = {".git", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
SKIP_PREFIXES = {ROOT / "reports" / "screenshots"}
ALLOW_OLD_REPORTS = {
    ROOT / "reports" / "legal_name_correction_audit.md",
    ROOT / "reports" / "legal_name_risky_items_pending_approval.md",
    ROOT / "reports" / "external_platform_legal_name_audit.md",
    ROOT / "scripts" / "legal_name_correction_audit.py",
}
SAFE_EXTS = {".py", ".html", ".js", ".css", ".md", ".txt", ".json", ".yml", ".yaml", ".svg", ".xml", ".toml", ".ini", ".plist", ".config"}
REQUIRED_FILES = [
    ROOT / "templates" / "terms.html",
    ROOT / "templates" / "privacy.html",
    ROOT / "templates" / "support.html",
    ROOT / "mobile" / "pulse-react-native" / "store-metadata" / "en-US" / "app-store.md",
    ROOT / "mobile" / "pulse-react-native" / "store-metadata" / "en-US" / "play-store.md",
]


def should_scan(path: Path) -> bool:
    if not path.is_file():
        return False
    if path in ALLOW_OLD_REPORTS:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if any(path == prefix or prefix in path.parents for prefix in SKIP_PREFIXES):
        return False
    return path.suffix.lower() in SAFE_EXTS


def main() -> int:
    failures = []
    for path in ROOT.rglob("*"):
        if not should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for old in OLD_EXACT:
            if old in text:
                failures.append(f"old exact legal name remains in {path.relative_to(ROOT)}")
                break
    for path in REQUIRED_FILES:
        text = path.read_text(encoding="utf-8")
        if REQUIRED_NEW not in text:
            failures.append(f"required legal name missing from {path.relative_to(ROOT)}")
    if failures:
        print("LEGAL_NAME_AUDIT failed")
        for failure in failures[:80]:
            print("-", failure)
        return 1
    print("LEGAL_NAME_AUDIT ok: exact legal display name corrected; operational identifiers excluded")
    return 0

if __name__ == "__main__":
    sys.exit(main())
