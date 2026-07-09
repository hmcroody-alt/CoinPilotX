#!/usr/bin/env python3
"""Audit PulseSoc Native Home foundation completion evidence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSER = ROOT / "mobile-native" / "src" / "components" / "HomePulseComposer.tsx"
HOME = ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
COMPLETION = ROOT / "reports" / "pulsesoc_native_home_completion.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_home_visible_publish_qa.md"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"


REQUIRED_COMPOSER_TOKENS = [
    "testID=\"home-composer-input\"",
    "testID=\"home-composer-counter\"",
    "testID={`home-composer-mode-${item.key}`}",
    "testID=\"home-composer-photo\"",
    "testID=\"home-composer-video\"",
    "testID=\"home-composer-status\"",
    "testID=\"home-composer-retry\"",
    "testID=\"home-composer-publish\"",
    "testID=\"home-composer-recovered-draft\"",
    "testID=\"home-composer-clear-draft\"",
    "AsyncStorage.getItem(DRAFT_KEY)",
    "AsyncStorage.setItem(DRAFT_KEY",
    "AsyncStorage.removeItem(DRAFT_KEY)",
    "Retry Last Publish",
    "Composer validation blocked an empty signal",
    "media.upload",
    "createPost",
]

REQUIRED_HOME_TOKENS = [
    "invalidateNativeSync",
    "post_published",
    "native_home_composer",
    "load(\"refresh\")",
]

REQUIRED_REPORT_TOKENS = [
    "Server-Authoritative Publish Proof",
    "Text-only publish returned `ok=true`",
    "Visible QA Status",
    "not yet complete",
    "Native Home visible browser publish proof recovery",
]

REQUIRED_VISIBLE_QA_TOKENS = [
    "Result: blocked before final visible proof.",
    "Text-only post publish succeeded",
    "Not Yet Visibly Proven",
    "Home foundation cannot be marked complete",
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
    completion = read(COMPLETION)
    visible = read(VISIBLE_QA)
    progress = read(PROGRESS)

    require_tokens("Home composer QA/publish contract", composer, REQUIRED_COMPOSER_TOKENS)
    require_tokens("Home feed invalidation", home, REQUIRED_HOME_TOKENS)
    require_tokens("Home completion report", completion, REQUIRED_REPORT_TOKENS)
    require_tokens("Visible publish QA report", visible, REQUIRED_VISIBLE_QA_TOKENS)
    require_tokens("Native progress report", progress, ["Can Home foundation be considered complete: NO", "Visible QA: 78%"])

    print("PulseSoc Native Home completion audit passed.")
    print("Verified QA handles, publishing contract evidence, and honest incomplete visible QA status.")


if __name__ == "__main__":
    main()
