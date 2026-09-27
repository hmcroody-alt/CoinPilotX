#!/usr/bin/env python3
"""A post's attached music must behave on the web the way it behaves in the app.

THE RULE
--------
``mobile-native/src/core/attachedMusicAudioPolicy.ts`` is the product's single
source of truth: when a post has attached music, the original media audio is
muted and the music plays. ``resolveViewerAudioPlan`` returns
``shouldPlayMusic`` from the post's metadata alone -- there is no gesture term,
no per-post permission, nothing the reader has to find and press. Sound-on is a
preference, and once it is on the next Reel plays its music.

The web reached the opposite behaviour through a flag with no counterpart in the
app. ``playAttachedAudio`` required ``attachedAudioUserUnlocked``, which starts
false on every page load and is set only by an explicit tap on that post's own
control. Two things followed, both web-only:

  * A post with attached music never started with sound, even for a reader whose
    saved preference said sound-on -- while a post whose video carried its own
    audio track unmuted itself from that very same stored preference. The
    asymmetry was inside the web, not just against the app.
  * Because the flag is page scope and the preference it guarded is persisted,
    the tap did not stick. Every navigation asked again.

Browsers genuinely do gate unmuted playback on user activation, so the web
cannot be identical. What it can be, and now is, is the same shape: try, fall
back to muted-but-playing rather than silent, and take the first gesture
anywhere on the page as the permission it actually is.

WHY THIS IS BEHAVIOURAL
-----------------------
A source-presence check here would be close to worthless. The defect was not a
missing identifier -- every identifier involved was present and spelled
correctly -- it was one ``&&`` term in a boolean. ``"attachedAudioUserUnlocked"
in RENDERER`` is true before the fix and true after it. So this stands the
renderer up against a DOM stub and drives the real exported functions, in two
scenarios, and reads the verdict off observed state.

WHY THE MUTANTS
---------------
The stub is hand-rolled, which makes "the harness passed" a weaker claim than it
looks: a stub too shallow to reach the logic passes everything. So the same
harness is also run against two mutants that restore the pre-fix semantics, and
each is REQUIRED to fail. If a future edit leaves the harness unable to tell the
old behaviour from the new, these turn red rather than going quietly green.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HARNESS = HERE / "attached_audio_autoplay_harness.mjs"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"

SCENARIOS = ("strict", "permissive")

# The two edits that, together, put the pre-fix behaviour back. Each is
# (description, find, replace). A find string that no longer occurs produces a
# mutant identical to the original, which then PASSES -- and the assertion below
# reads that as the failure it is, rather than silently testing nothing.
MUTATIONS = (
    (
        "restore the attachedAudioUserUnlocked term in wantsSound",
        "const wantsSound = forceSound || (preferSound && soundEnabled());",
        "const wantsSound = forceSound || (preferSound && soundEnabled() && attachedAudioUserUnlocked);",
    ),
    (
        "neuter the first-gesture unlock",
        "if (attachedAudioUserUnlocked || !soundEnabled()) return;",
        "if (true) return;",
    ),
)


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # Same stance as reels_preload_runner: a verifier that cannot run is a
        # failure to verify. Skipping would leave the rule unprotected while
        # still reporting a green suite.
        raise AssertionError(
            "node is required to verify attached-music autoplay behaviourally")
    return node


def _run(source_path: Path, scenario: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_node(), str(HARNESS), str(source_path), scenario],
        capture_output=True, text=True, timeout=120,
    )


def test_attached_music_matches_app_behaviour() -> None:
    """The shipping renderer passes both scenarios."""
    for scenario in SCENARIOS:
        proc = _run(RENDERER, scenario)
        assert proc.returncode == 0, (
            f"attached-music autoplay harness failed in the {scenario!r} scenario "
            f"(rc={proc.returncode}).\n"
            f"--- stdout ---\n{proc.stdout[-3000:]}\n"
            f"--- stderr ---\n{proc.stderr[-2000:]}"
        )
        assert "PASSED" in proc.stdout, (
            f"{scenario!r} scenario exited 0 without reporting PASSED, which means "
            f"the harness did not reach its assertions:\n{proc.stdout[-3000:]}"
        )


def test_harness_can_still_detect_the_old_behaviour() -> None:
    """Each mutant restoring the pre-fix semantics must be caught."""
    original = RENDERER.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="attachedaudio_") as tmp:
        for label, find, replace in MUTATIONS:
            assert find in original, (
                f"mutation {label!r} no longer applies: the renderer does not "
                f"contain {find!r}. The guard below cannot fail until this is "
                "re-pointed at the current source, so fix the mutation rather "
                "than deleting it."
            )
            mutant = Path(tmp) / "mutant.js"
            mutant.write_text(original.replace(find, replace), encoding="utf-8")

            caught = []
            for scenario in SCENARIOS:
                proc = _run(mutant, scenario)
                if proc.returncode != 0:
                    caught.append(scenario)
            assert caught, (
                f"mutation {label!r} restored the pre-fix behaviour and the "
                "harness passed anyway, in both scenarios. The harness is not "
                "exercising the rule it claims to protect."
            )


def test_the_feed_music_button_listens_for_the_state_event() -> None:
    """The renderer's state event must have a listener on the other end.

    Deliberately a source-presence check, and honest about it: the harness can
    prove the event is dispatched with the right detail, but the feed's ▶ control
    is built by a minified template literal inside bot.py that cannot be stood up
    headlessly. What this catches is the failure that actually threatens the fix
    -- the renderer dispatching into the void because the listener was dropped,
    leaving a button that reads "play" while the song is audible.
    """
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    event = "pulse:attached-audio-state"
    assert event in renderer, f"the renderer no longer dispatches {event!r}"
    assert f"addEventListener('{event}'" in bot, (
        f"bot.py does not listen for {event!r}, so the feed's attached-music "
        "button cannot learn that playback started on its own. Either restore "
        "the listener or stop dispatching the event."
    )
    assert "data-toggle-post-music" in bot, (
        "the feed's attached-music toggle is gone; the listener above is now "
        "pointing at nothing and should be removed with it."
    )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
