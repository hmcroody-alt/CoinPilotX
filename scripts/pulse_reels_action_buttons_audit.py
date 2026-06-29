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
    css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    required = {
        "svg icon normalizer exists": "function reelActionIconSvg" in source and "icons={" in source,
        "like uses svg icon": "like:'<svg" in source and "Like reel" in source,
        "comment uses svg icon": "comment:'<svg" in source and "View comments" in source,
        "share uses svg icon": "share:'<svg" in source and "Share reel" in source,
        "save uses svg icon": "save:'<svg" in source and "Save reel" in source,
        "remix uses svg icon": "remix:'<svg" in source and "Remix reel" in source,
        "follow uses svg icon": "follow:'<svg" in source and "Follow creator" in source,
        "more uses svg icon": "more:'<svg" in source and "More reel options" in source,
        "glass action class exists": "reel-action-button" in source and ".reel-action.reel-action-button" in source,
        "transparent action CSS exists": "background:rgba(4,10,20,.26)" in source and "backdrop-filter:blur(14px) saturate(150%)" in source,
        "standalone transparent CSS exists": ".reel-action.reel-action-button" in css and "backdrop-filter: blur(14px) saturate(150%)" in css,
        "mobile glass layout is safe": "@media(max-width:480px)" in source and "bottom:calc(106px + env(safe-area-inset-bottom))" in source,
        "reduced motion is respected": "@media(prefers-reduced-motion:reduce)" in source and "animation:none!important" in source,
        "optimistic backend reaction wiring remains": "async function fireReel" in source and "pulseApi(`/api/pulse/reels/${id}/react`" in source,
        "save backend wiring remains": "pulseApi(`/api/pulse/reels/${save.dataset.reelSave}/save`" in source,
        "share wiring remains": "navigator.share" in source and "data-share-reel" in source,
        "comments wiring remains": "openComments" in source and "data-open-comments" in source,
        "follow wiring remains": "data-follow-creator" in source and "/api/pulse/follows/toggle" in source,
        "follow without creator id is safely unavailable": "Creator follow is unavailable for this Reel." in source
        and "avatar.classList.add('is-unavailable')" in source,
        "more menu wiring remains": "openReelMenu" in source and "data-reel-more" in source,
        "dynamic button normalizer exists": "normalizeReelActionButtons" in source and "new MutationObserver" in source,
        "duplicate renderer remix button is removed": ".reel-action[data-reel-remix]" in source and "button.remove()" in source,
        "remix safe unavailable handler exists": "const remix=e.target.closest('[data-reel-remix]')" in source
        and "Remix is not available for this Reel yet." in source,
        "toggle buttons expose aria pressed": "aria-pressed" in source and "toggle:true" in source,
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
