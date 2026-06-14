#!/usr/bin/env python3
"""Audit PulseSoc MutationObservers for write-after-observe loop risks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = [
    ROOT / "bot.py",
    ROOT / "static",
    ROOT / "templates",
]

FAILURES: list[str] = []


def runtime_files() -> list[Path]:
    files: list[Path] = []
    for path in RUNTIME_PATHS:
        if path.is_file():
            files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in {".js", ".html", ".py"}:
                files.append(child)
    return files


def check(condition: bool, label: str, details: str = "") -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}{': ' + details if details else ''}")
        FAILURES.append(label)


def observer_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"new\s+MutationObserver\s*\(", text):
        start = max(0, match.start() - 260)
        observe = text.find(".observe", match.end())
        if observe == -1:
            blocks.append(text[start : match.start() + 900])
            continue
        end = text.find(";", observe)
        if end == -1:
            end = observe + 900
        blocks.append(text[start : min(len(text), end + 1)])
    return blocks


def main() -> int:
    all_blocks: list[tuple[Path, str]] = []
    for path in runtime_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in observer_blocks(text):
            all_blocks.append((path, block))

    check(bool(all_blocks), "runtime MutationObservers were discovered")
    risky: list[str] = []
    for path, block in all_blocks:
        relative = path.relative_to(ROOT)
        normalized = " ".join(block.split())
        watches_attributes = re.search(r"attributes\s*:\s*true", block) is not None
        watches_style_or_class = re.search(r"attributeFilter\s*:\s*\[[^\]]*['\"](?:style|class)['\"]", block) is not None
        writes_style_or_class = any(token in block for token in [".style.", ".style[", ".style =", ".classList.", "className", "setAttribute('style", 'setAttribute("style', "setAttribute('class", 'setAttribute("class'])
        if watches_attributes and (watches_style_or_class or writes_style_or_class):
            risky.append(f"{relative}: {normalized[:420]}")
    check(not risky, "no observer watches attributes while mutating style/class", "\n".join(risky[:8]))

    status = (ROOT / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")
    media = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    check("statusCloseHardened" in status, "status close style writes are idempotent")
    check("attributeFilter" not in status, "status viewer does not observe class/style attributes")
    check("controlsObserver.observe(document.documentElement" in media and "childList: true" in media and "subtree: true" in media, "media control observer only watches child additions")

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse MutationObserver audit ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
