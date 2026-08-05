#!/usr/bin/env python3
"""Protect Reels, Videos, Statuses, and mobile navigation contracts.

Most checks here are source-presence checks: they pin a wiring decision that is hard
to exercise headlessly (a pointerdown handler, a camera constraint) and are honest
about being exactly that.

The Reels PRELOAD WINDOW is different, and is checked behaviorally instead. It used to
be asserted with ``"reelLightPreloaded'+(idx+1)" in BOT`` — a grep for one identifier
in the middle of a minified line. That assertion could not distinguish a working
preload window from a broken one; it only detected that a particular string still
existed, so it failed the moment the flag was renamed while passing happily through a
real defect in the same function. It has been replaced by
:func:`check_reels_preload_window`, which extracts the shipping functions out of bot.py
and RUNS them, so the verdict comes from observed behavior: which neighbours get armed,
at what ``preload`` level, how many network fetches that costs, which cards get torn
down, and whether a card can be re-armed after teardown.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

# The extraction-and-run machinery lives in a sibling module because the mobile
# playback audit needs exactly the same behavioral guarantee, and a guarantee stated
# twice is a guarantee that will eventually disagree with itself.
from reels_preload_runner import (  # noqa: E402
    REEL_FUNCTIONS,
    extract_js_function,
    run_reel_scenarios,
)

BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
RENDERER = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
CAMERA = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")

assert REEL_FUNCTIONS and callable(extract_js_function)


def expect(condition: bool, label: str) -> None:
    # Counted so scripts/protection/run_protection_suite.py can prove this file
    # actually executed. A suite that exits 0 having checked nothing is the
    # failure mode the runner exists to catch.
    expect.calls = getattr(expect, "calls", 0) + 1
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def check_reels_preload_window() -> None:
    """The current Reel plus the next two are prepared, cheaply, and cleaned up.

    Every number below is produced by the shipping code; the harness only supplies
    DOM primitives and counts the media loads that the shipping code chooses to make.
    ``fetches`` are ``load()`` calls that pull bytes; ``drops`` are ``load()`` calls
    on a ``preload='none'`` element, which free a buffer and pull nothing.
    """
    r = run_reel_scenarios([
        "window_shape", "window_shape_autodetect", "rapid_scroll_idempotent",
        "fling_skips_cards", "sequential_walk", "short_feed_one", "short_feed_two",
        "end_of_feed", "penultimate", "empty_feed", "release_then_replay",
        "release_stops_and_frees", "load_failure_is_contained",
        "release_failure_is_contained", "poster_warm_once",
    ])

    def by_id(cards):
        return {c["id"]: c for c in cards}

    # --- shape: current + next two are prepared, at the right levels -------------
    for key in ("window_shape", "window_shape_autodetect"):
        cards = by_id(r[key]["cards"])
        expect(cards["r4"]["window"] == "next1" and cards["r5"]["window"] == "next2",
               f"{key}: the next two Reels are the ones prepared")
        expect(cards["r4"]["video"]["preload"] == "auto"
               and cards["r5"]["video"]["preload"] == "auto",
               f"{key}: both upcoming Reels buffer media, not just metadata")
        expect(cards["r4"]["video"]["fetches"] == 1
               and cards["r5"]["video"]["fetches"] == 1,
               f"{key}: each upcoming Reel is fetched exactly once")
        expect(cards["r2"]["window"] == "previous"
               and cards["r2"]["video"]["preload"] == "metadata"
               and cards["r2"]["video"]["fetches"] == 0,
               f"{key}: the previous Reel keeps metadata only, at no bandwidth cost")
        expect(cards["r6"]["video"]["preload"] != "auto"
               and cards["r7"]["video"]["preload"] != "auto"
               and cards["r6"]["video"]["fetches"] == 0
               and cards["r7"]["video"]["fetches"] == 0,
               f"{key}: Reels beyond the next two are not downloaded")
        expect(r[key]["fetches"] == 2,
               f"{key}: preparing the window costs exactly two media fetches")
        expect(all(c["video"]["autoplay"] is False for c in r[key]["cards"]),
               f"{key}: no prepared Reel is left autoplaying")
        expect(all(c["video"]["muted"] is True for c in r[key]["cards"]),
               f"{key}: prepared Reels stay muted until they become active")

    # --- rapid scrolling: re-entering the same card costs nothing ----------------
    rs = r["rapid_scroll_idempotent"]
    expect(rs["extra"] == 0 and rs["fetches"] == rs["fetchesAfterFirst"] == 2,
           "rapid scrolling re-runs the window 25x and downloads nothing extra")
    expect(rs["drops"] == rs["dropsAfterFirst"],
           "rapid scrolling does not thrash media teardown either")

    # --- flinging past cards must not download them -----------------------------
    fl = r["fling_skips_cards"]
    expect(fl["afterFling"]["fetches"] - fl["afterStart"]["fetches"] == 2,
           "flinging over nine Reels only downloads the two it lands next to")
    flc = by_id(fl["cards"])
    expect(all(flc[f"r{i}"]["video"]["fetches"] == 0 for i in (3, 4, 5, 6, 7)),
           "Reels flown past are never fetched")
    expect(flc["r10"]["video"]["preload"] == "auto"
           and flc["r11"]["video"]["preload"] == "auto",
           "the window is armed around wherever the fling settles")

    # --- walking the feed: every Reel downloaded at most once --------------------
    sw = r["sequential_walk"]
    expect(sw["perStep"] == [2, 1, 1, 1, 0, 0],
           "walking a 6-Reel feed front to back stays one fetch ahead")
    expect(sw["fetches"] == 5 and all(c["video"]["fetches"] <= 1
                                     for c in sw["cards"]),
           "no Reel is downloaded twice while walking forward")

    # --- short feeds and feed edges: partial windows, never a throw --------------
    expect(r["short_feed_one"]["warmed"] == 0
           and r["short_feed_one"]["fetches"] == 0,
           "a one-Reel feed prepares nothing and does not fail")
    expect(r["short_feed_two"]["fetches"] == 1
           and by_id(r["short_feed_two"]["cards"])["r1"]["window"] == "next1",
           "a two-Reel feed prepares the only neighbour there is")
    eof = by_id(r["end_of_feed"]["cards"])
    expect(r["end_of_feed"]["fetches"] == 0 and eof["r3"]["window"] == "previous",
           "at the end of the feed there is nothing ahead to download")
    pen = by_id(r["penultimate"]["cards"])
    expect(r["penultimate"]["fetches"] == 1 and pen["r4"]["window"] == "next1"
           and pen["r4"]["video"]["preload"] == "auto",
           "one Reel from the end, the single remaining Reel is prepared")
    expect(r["empty_feed"]["warmed"] == 0 and r["empty_feed"]["fetches"] == 0,
           "an empty feed prepares nothing and does not fail")

    # --- teardown and replay ----------------------------------------------------
    rp = r["release_then_replay"]
    warm, jump, back = (by_id(rp["afterWarm"]), by_id(rp["afterJump"]),
                        by_id(rp["afterReturn"]))
    expect(warm["r2"]["video"]["readyState"] > 0
           and warm["r3"]["video"]["readyState"] > 0,
           "replay: the upcoming Reels really do hold buffered data")
    expect(jump["r2"]["window"] == "released" and jump["r3"]["window"] == "released"
           and jump["r2"]["video"]["preload"] == "none"
           and jump["r2"]["video"]["drops"] == 1
           and jump["r2"]["video"]["readyState"] == 0,
           "scrolling away releases the buffered Reels instead of leaking them")
    # This is the regression the old grep could not see: a card that had been warmed
    # and then torn down used to stay at preload='none' forever, so the Reel about to
    # become active buffered nothing at all.
    expect(back["r2"]["window"] == "next1" and back["r3"]["window"] == "next2"
           and back["r2"]["video"]["preload"] == "auto"
           and back["r3"]["video"]["preload"] == "auto",
           "returning to a released Reel re-arms it rather than leaving it dead")
    expect(back["r2"]["video"]["fetches"] == 2
           and back["r3"]["video"]["fetches"] == 2
           and back["r2"]["video"]["readyState"] > 0,
           "a re-armed Reel actually re-downloads the data it gave up")
    expect(back["r0"]["window"] == "previous"
           and back["r0"]["video"]["preload"] == "metadata",
           "the window shape is fully restored on return, not just partly")

    # --- cleanup: released players stop and free their buffers -------------------
    rl = r["release_stops_and_frees"]
    expect(rl["before"]["paused"] is False and rl["before"]["readyState"] > 0,
           "cleanup: the Reel under test was genuinely playing and buffered")
    expect(rl["after"]["paused"] is True and rl["after"]["pauses"] == 1,
           "an offscreen Reel is paused exactly once, not left playing")
    expect(rl["after"]["preload"] == "none" and rl["after"]["drops"] == 1
           and rl["after"]["readyState"] == 0,
           "an offscreen Reel's buffer is dropped so players do not accumulate")
    expect(rl["after"]["autoplay"] is False and rl["stillPlaying"] == [],
           "no Reel keeps playing once it leaves the window")

    # --- failure containment ----------------------------------------------------
    lf = r["load_failure_is_contained"]
    lfc = by_id(lf["cards"])
    expect(lf["threw"] is False and lfc["r3"]["window"] == "next2"
           and lfc["r3"]["video"]["fetches"] == 1,
           "one Reel failing to load does not stop the rest being prepared")
    rf = r["release_failure_is_contained"]
    rfc = by_id(rf["cards"])
    expect(rf["threw"] is False and rfc["r7"]["window"] == "next1"
           and rfc["r7"]["video"]["preload"] == "auto",
           "one Reel failing to release does not stop the window advancing")

    # --- posters are warmed once per card ---------------------------------------
    pw = r["poster_warm_once"]
    expect(pw["images"] == 3 and pw["fetches"] == 2,
           "ten window passes warm each poster once and re-download nothing")


def main() -> None:
    reels_block = BOT[BOT.find("const reelsFeed=document.getElementById('reelsFeed')"):BOT.find("function renderRail(activeLane")]
    status_play = BOT[BOT.find("async function playStatusViewerVideo"):BOT.find("function renderStatusViewer")]
    status_previews = BOT[BOT.find("function hydrateStatusCardVideos"):BOT.find("function renderStatusCard")]

    expect("playReelVideo(v,true)" in reels_block, "active Reels autoplay with sound preference")
    expect("playReelVideo(v,false)" not in BOT, "Reels do not request muted active autoplay")
    check_reels_preload_window()
    expect("canHoverPreview = desktopPointer() && !isReelSurface" in RENDERER, "desktop hover preview no longer gates Reels playback")
    expect("pointerdown" in BOT and "show-reaction-menu" in BOT, "long-press reaction affordance remains wired")
    expect("dblclick" in BOT and "fireReel" in BOT, "double-tap/double-click like remains wired")
    expect("reel-sound-badge is-hidden" in BOT, "Reels do not show persistent sound bubbles")
    expect("window.PulseMediaRenderer?.soundEnabled?.()!==false" in status_play, "active Status viewer follows saved sound policy")
    expect("player.defaultMuted=true" in status_previews and "player.muted=true" in status_previews, "Status rail previews stay muted")
    expect("data-videos-drawer-open" in BOT and "data-videos-mobile-drawer" in BOT, "mobile Videos drawer exists")
    expect("setVideosDrawer" in BOT and "videos-drawer-nav" in BOT, "mobile Videos drawer behavior exists")
    expect("Creator Studio" in BOT and "Marketplace" in BOT, "mobile Videos drawer includes full navigation")
    expect("width: { ideal: 1920 }" in CAMERA and "width: { ideal: 1280 }" in CAMERA, "camera quality profiles include 1080p and 720p fallback")
    expect("safeTrackSettings" in CAMERA and "maskDeviceId" in CAMERA, "camera diagnostics are safe")
    expect("Banuba Active" in CAMERA and "Banuba Failed" in CAMERA and "Using Native Camera" in CAMERA, "Banuba runtime status is explicit")
    print(f"PROTECTION_TESTS_RUN={getattr(expect, 'calls', 0)}")
    print("media playback protection contract ok")


if __name__ == "__main__":
    main()
