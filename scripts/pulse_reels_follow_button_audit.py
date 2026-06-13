#!/usr/bin/env python3
"""Audit PulseSoc Reels compact follow button and creator header."""

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
        "creator header uses compact grid": ".reel-caption-creator{display:grid!important;grid-template-columns:auto minmax(0,1fr) auto!important" in source,
        "follow button has bounded compact width": "max-width:118px!important" in source and "max-width:96px!important" in source,
        "follow button uses PulseSoc gradient": "background:linear-gradient(135deg,#36e58f,#6edff6)!important" in source,
        "following state is dark with checkmark": ".reel-follow.is-following" in source and 'content:"✓"' in source,
        "follow tap has pop animation": ".reel-follow:active" in source and "is-popping" in source,
        "optimistic follow listener exists": "optimisticFollow" in source and "follow.textContent='Following'" in source,
        "backend follow API remains wired": "pulseApi('/api/pulse/follows/toggle'" in source,
        "follow state synchronizer exists": "syncReelFollowButtons" in source and "aria-pressed" in source,
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
    require("follow style is present in route", ".reel-caption-creator .reel-follow" in html)
    require("optimistic script is present in route", "optimisticFollow" in html)

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse reels follow button audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
