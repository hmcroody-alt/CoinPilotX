#!/usr/bin/env python3
"""Audit PulseSoc Reels action buttons for clean icon UI and wiring."""

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
    required = {
        "like uses heart icon span": '<span class="reel-action-icon" aria-hidden="true">❤️</span>' in source,
        "comment uses speech icon span": '<span class="reel-action-icon" aria-hidden="true">💬</span>' in source,
        "share uses arrow icon span": '<span class="reel-action-icon" aria-hidden="true">↗️</span>' in source,
        "save uses bookmark icon span": '<span class="reel-action-icon" aria-hidden="true">🔖</span>' in source,
        "repost/remix uses loop icon span": '<span class="reel-action-icon" aria-hidden="true">🔁</span>' in source,
        "mobile labels hidden": "@media(max-width:900px)" in source and ".reel-action-label{display:none!important}" in source,
        "action buttons fit mobile rail": "max-width:54px!important" in source and "width:48px!important" in source,
        "tap pop animation is present": ".reel-action:active .reel-action-icon" in source and ".reel-action.is-popping .reel-action-icon" in source,
        "optimistic backend reaction wiring remains": "async function fireReel" in source and "pulseApi(`/api/pulse/reels/${id}/react`" in source,
        "dynamic button normalizer exists": "normalizeReelActionButtons" in source and "new MutationObserver" in source,
        "duplicate renderer remix button is removed": ".reel-action[data-reel-remix]" in source and "button.remove()" in source,
    }
    for label, condition in required.items():
        require(label, condition)

    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = -920871340
    page = client.get("/pulse/reels?tab=for_you")
    html = page.get_data(as_text=True)
    require("reels page returns 200", page.status_code == 200)
    require("reels route includes action rail", "reels-action-rail" in html)
    require("reels route includes icon normalizer script", "normalizeReelActionButtons" in html)
    require("reels route does not render chopped literal labels in source", ">Lik<" not in html and ">Co<" not in html and ">Sav<" not in html)

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse reels action buttons audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
