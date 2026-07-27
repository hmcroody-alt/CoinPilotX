#!/usr/bin/env python3
"""Audit Reels mobile playback behavior.

The preload check in here is behavioral, not textual. See
:func:`expect_preload_is_single_shot` for why.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
REELS_CSS = ROOT / "static/css/pulse_reels_experience.css"
RUNNER = ROOT / "tests/protection/reels_preload_runner.py"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def expect_preload_is_single_shot() -> None:
    """A Reel in the preload window is downloaded at most once, however often the
    window is re-asserted.

    This check used to be a pair of greps for ``const flag='reelLightPreloaded'+mode``
    and ``card.dataset[flag]==='1'`` -- that is, for the literal text of an EARLY RETURN
    on a per-card "already warmed" flag. Matching that text was never the same as
    holding the guarantee, and in this case the text was actively wrong: the early
    return meant a card that had been warmed once and then torn down by
    ``releaseFarReelMedia`` (``preload='none'``, buffer dropped) could never be re-armed,
    so scrolling back up left the Reel about to become active holding nothing. The audit
    was pinning a defect in place.

    The guarantee itself is unchanged and is now measured instead of matched: the
    shipping ``preloadNextReel`` / ``releaseFarReelMedia`` are extracted from bot.py and
    run against the Reels harness, and the media fetches they choose to make are
    counted. Re-asserting the window is free because the fetch is gated on
    ``readyState===0`` -- an element that already holds data is not re-downloaded -- and
    that gate, unlike the flag, still permits a released Reel to be re-armed.
    """
    spec = importlib.util.spec_from_file_location("reels_preload_runner", RUNNER)
    if spec is None or spec.loader is None:  # pragma: no cover - path guard
        raise AssertionError(f"Reels preload runner is missing at {RUNNER}")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    r = runner.run_reel_scenarios(
        ["rapid_scroll_idempotent", "sequential_walk", "poster_warm_once"])

    rapid = r["rapid_scroll_idempotent"]
    expect(rapid["extra"] == 0 and rapid["fetches"] == rapid["fetchesAfterFirst"],
           "Reels preload is single-shot per adjacent window")
    expect(rapid["drops"] == rapid["dropsAfterFirst"],
           "Reels preload does not thrash media teardown either")

    walk = r["sequential_walk"]
    expect(all(c["video"]["fetches"] <= 1 for c in walk["cards"]),
           "no Reel is downloaded twice while walking the feed forward")

    poster = r["poster_warm_once"]
    expect(poster["images"] == 3 and poster["fetches"] == 2,
           "repeated window passes warm each poster once and re-download nothing")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    reels_css = REELS_CSS.read_text(encoding="utf-8")

    for token in [
        "PulseMediaRenderer.renderMedia",
        "playReelVideo",
        "primaryReelVideo",
        "syncPlayback",
        "preloadNextReel",
        "pulseReelsSoundEnabled",
        "pulseMediaSoundEnabled",
    ]:
        expect(token in bot, f"Reels includes {token}")

    expect("muxHls=media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "Reels force canonical Mux HLS before raw media")
    expect("if(visible&&v===primaryReelVideo(card))" in bot, "only the active visible Reel plays")
    expect(
        "offscreen_pause" in bot
        and "if(card.dataset.reelWindow!=='released')v.preload='metadata'" in bot,
        "offscreen Reels pause and reduce preload unless released",
    )
    expect_preload_is_single_shot()
    expect("Pulse reel stream failed without cache-busting retry" in bot, "Reels avoid infinite stream retry loops")
    expect("playsinline webkit-playsinline" in bot + renderer, "Reels videos have mobile inline playback attributes")
    expect("nativeHlsSupported(video)" in renderer and "pulseNativeHls" in renderer, "Reels inherit native HLS handling")
    expect(".reel-details-panel" in reels_css and "display: none !important" in reels_css, "mobile Reels hide desktop details panels")
    expect(".reel-comments-preview" in reels_css and ".reel-inline-comment" in reels_css, "mobile Reels hide comments preview and inline input")
    expect(".reel-comments:not(.open)" in reels_css, "mobile Reels comments are hidden by default")


if __name__ == "__main__":
    main()
