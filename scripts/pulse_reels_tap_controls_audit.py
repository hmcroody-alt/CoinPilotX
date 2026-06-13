#!/usr/bin/env python3
"""Audit PulseSoc Reels tap controls and double-tap like behavior."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import bot  # noqa: E402


FAILURES: list[str] = []


def require(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}")
        FAILURES.append(label)


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    checks = {
        "mobile center play overlay hidden": "@media(max-width:900px){.reel-center-play{display:none!important" in source,
        "small tap sound icon exists": ".reel-tap-sound-icon" in source and "showTapSoundIcon" in source,
        "single tap toggles sound": "setReelsSound(!reelsSoundEnabled)" in source and "Sound on" in source and "Muted" in source,
        "tap handler blocks old center controls": "e.stopImmediatePropagation()" in source and "reelsFeed.addEventListener('click'" in source,
        "double tap likes reel": "reelsFeed.addEventListener('dblclick'" in source and "fireReel(card.dataset.reelId,e)" in source,
        "double tap guard prevents single tap conflict": "let reelTapTimer=null" in source and "clearTimeout(reelTapTimer)" in source,
        "old explicit sound button remains wired": "data-toggle-reel-sound" in source and "updateReelControls(card)" in source,
    }
    for label, condition in checks.items():
        require(label, condition)

    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = -920871340
    page = client.get("/pulse/reels?tab=for_you")
    html = page.get_data(as_text=True)
    require("reels page returns 200", page.status_code == 200)
    require("tap control script is present", "showTapSoundIcon" in html and "reelTapTimer" in html)
    require("broken center label source is suppressed on mobile", ".reel-center-play{display:none!important" in html)

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse reels tap controls audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
