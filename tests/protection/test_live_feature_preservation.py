"""Stages 34 and 35 — what the multi-guest work was not allowed to cost.

Adding guests to a Live touches the same engine, the same audio module and the
same host toolbar as everything else the host already had. This suite exists so
that "we added multi-guest" can never quietly mean "and screen share and the
music mixer stopped working", which is the ordinary way a broadcast feature
regresses: not by breaking loudly, but by being removed from a toolbar during a
refactor and noticed a release later.

Stage 34 is reported honestly rather than asserted optimistically. Screen share
is **not implemented** as a Live capture path on native — the host toolbar
carries a "coming soon" tile, ``services/live_scene_engine.py`` declares a
``screen_share`` scene descriptor, and the only real screen-share signalling in
the repo belongs to calls, not Live. The guards below therefore assert that
this mission left all three exactly as it found them, which is the strongest
true statement available.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_SCREEN = ROOT / "mobile-native" / "src" / "screens" / "LiveHostSessionScreen.tsx"
HOOK = ROOT / "mobile-native" / "src" / "live" / "useAgoraLiveBroadcastRoom.ts"
SCENES = ROOT / "services" / "live_scene_engine.py"


class TestScreenShareIsUnchanged(unittest.TestCase):
    """Stage 34: preserved means preserved, including preserved as unbuilt."""

    def test_the_scene_descriptor_still_exists(self):
        source = SCENES.read_text(encoding="utf-8", errors="replace")
        self.assertIn('"key": "screen_share"', source)

    def test_the_host_toolbar_still_offers_the_affordance(self):
        source = HOST_SCREEN.read_text(encoding="utf-8", errors="replace")
        self.assertIn('label="Screen share"', source)

    def test_no_live_screen_capture_path_was_introduced(self):
        # Introducing one would be a second publication path into the Live
        # channel, which the audio foundation rules forbid outright. If screen
        # share is ever built it goes through the one engine and the one
        # publisher, and this assertion is where that conversation starts.
        source = HOOK.read_text(encoding="utf-8", errors="replace")
        for forbidden in ("startScreenCapture", "getDisplayMedia", "ScreenCaptureParameters"):
            self.assertNotIn(forbidden, source)


class TestMusicMixSurvivesTheStage(unittest.TestCase):
    """Stage 35: a guest arriving must not silence the host's music."""

    @classmethod
    def setUpClass(cls):
        cls.hook = HOOK.read_text(encoding="utf-8", errors="replace")

    def test_the_mixing_controls_are_still_wired(self):
        for api in (
            "startAudioMixing(",
            "pauseAudioMixing(",
            "resumeAudioMixing(",
            "stopAudioMixing(",
            "adjustAudioMixingPublishVolume(",
            "adjustAudioMixingPlayoutVolume(",
        ):
            self.assertIn(api, self.hook)

    def test_the_scenario_change_restores_the_mix(self):
        # setAudioScenario reconfigures Agora's audio module underneath any
        # mixing in flight, and it fires exactly when the first guest arrives.
        # Without the restoration the music stops the instant a host brings
        # someone up, with no error anywhere to explain it.
        block = re.search(
            r"const setStagePublisherCount[\s\S]*?\n  \}, \[\]\);",
            self.hook,
        )
        self.assertIsNotNone(block, "setStagePublisherCount not found")
        body = block.group(0)
        self.assertIn("setAudioScenario(", body)
        self.assertIn("musicRestorationAfterAudioChange(", body)

    def test_the_restoration_decision_lives_in_a_pure_module(self):
        # The hook cannot be unit-tested (its Agora import is a dynamic import
        # Jest will not evaluate), so the decision is extracted where it can be.
        module = (ROOT / "mobile-native" / "src" / "live" / "liveMusicMixing.ts").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("export function musicRestorationAfterAudioChange(", module)

    def test_music_is_still_publisher_only(self):
        # Stage 18/25: an audience member initialises nothing, and that includes
        # not being able to push audio into the channel through the mixer.
        self.assertIn("canPublish", self.hook)


if __name__ == "__main__":
    unittest.main()
