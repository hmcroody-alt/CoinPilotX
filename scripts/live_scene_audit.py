#!/usr/bin/env python3
"""Audit Pulse Live scene and transition engine."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import live_scene_engine  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    scenes = live_scene_engine.scene_catalog()
    keys = {scene["key"] for scene in scenes}
    require({"camera_only", "split_screen", "interview", "podcast", "screen_share", "vertical_live", "gaming", "brb", "starting_soon", "ending_soon"}.issubset(keys), "scene catalog includes creator broadcast presets")
    state = live_scene_engine.default_scene_state("vertical_live")
    require(state["active_scene"]["key"] == "vertical_live", "vertical mobile live scene can activate")
    require(state["transitions"], "scene engine exposes transitions")
    require("animated_lower_third" in state["overlays"], "scene engine supports overlays")
    print("live scene audit ok")


if __name__ == "__main__":
    main()
