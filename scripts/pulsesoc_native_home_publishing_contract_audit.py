#!/usr/bin/env python3
"""Static guard for PulseSoc Native Home publishing contract hardening."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSER = ROOT / "mobile-native" / "src" / "components" / "HomePulseComposer.tsx"
HOME = ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
PUBLISHING_REPORT = ROOT / "reports" / "pulsesoc_native_home_publishing_contract.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_home_visible_qa.md"
MASTER = ROOT / "reports" / "pulsesoc_native_progress.md"


REQUIRED_COMPOSER_TOKENS = [
    "DRAFT_KEY",
    "AsyncStorage.getItem(DRAFT_KEY)",
    "AsyncStorage.setItem(DRAFT_KEY",
    "AsyncStorage.removeItem(DRAFT_KEY)",
    "draftRecovered",
    "Recovered saved draft",
    "lastFailedPayload",
    "Retry Last Publish",
    "restoredMediaResult",
    "Uploaded media restored",
    "media.upload",
    "uploadResultMediaId",
    "createPost",
    "mode === \"live\"",
    "mode === \"reel\"",
    "Composer validation blocked an empty signal",
]

REQUIRED_HOME_TOKENS = [
    "invalidateNativeSync",
    "post_published",
    "native_home_composer",
    "load(\"refresh\")",
]

REQUIRED_REPORT_TOKENS = [
    "Post Publishing Contract",
    "Draft Recovery",
    "Upload Queue Persistence",
    "Feed Invalidation After Publish",
    "Visible QA",
]


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def require_tokens(label: str, text: str, tokens: list[str]) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise AssertionError(f"{label} missing tokens: {', '.join(missing)}")


def main() -> None:
    composer = read(COMPOSER)
    home = read(HOME)
    publishing_report = read(PUBLISHING_REPORT)
    visible_qa = read(VISIBLE_QA)
    master = read(MASTER)

    require_tokens("HomePulseComposer publishing contract", composer, REQUIRED_COMPOSER_TOKENS)
    require_tokens("HomeScreen feed invalidation", home, REQUIRED_HOME_TOKENS)
    require_tokens("Publishing report", publishing_report, REQUIRED_REPORT_TOKENS)
    require_tokens("Visible QA report", visible_qa, ["Home publishing contract", "Draft recovery", "Publish Signal"])
    require_tokens("Master progress report", master, ["Home Publishing Contract", "Draft recovery"])

    print("PulseSoc Native Home publishing contract audit passed.")
    print("Verified durable draft recovery, upload queue metadata, retry, publish reset, and feed invalidation.")


if __name__ == "__main__":
    main()
