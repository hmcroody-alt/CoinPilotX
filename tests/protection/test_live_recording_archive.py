"""Stages 32, 33 and 24 — what a multi-guest Live leaves behind, and what it costs.

The audit these guards came from found something the mission had assumed was
missing: composite recording is not a thing to build, it is a thing that has
been running in production the whole time. ``services/agora_cloud_recording_service.py``
starts every Live in Agora's ``mix`` mode with no per-UID subscription filter, so
the recorder already composites the host and every approved guest into a single
9:16 file. The replay a viewer sees after a six-person Live is the six people,
not the host alone. There was nothing to enable and no approval to ask for.

What the audit did surface is a cost coupling that is invisible from the code
that causes it. Agora bills cloud recording on the *aggregate* resolution — the
sum of the resolutions of every stream the recorder subscribes to — and the
recorder takes the high stream of every publisher (``videoStreamType: 0``). The
publish-side encoder ladder is therefore also the recording bill:

    six publishers at 720x1280   ->  5,529,600  ->  2K+      ($53.99/1k min)
    six publishers on the ladder ->  1,382,400  ->  Full HD  ($13.49/1k min)

A future author who "simplifies" the encoder back to a fixed 720p would see no
test fail, no frame drop on their own device, and a quadrupled recording bill
they would have no reason to connect to the change. That is what the last class
here exists to prevent.

Static guards: the hook cannot be imported under Jest (its Agora import is a
dynamic import Jest will not evaluate), so its wiring is asserted as source text
in the same style as the rest of this suite.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDING = ROOT / "services" / "agora_cloud_recording_service.py"
HOOK = ROOT / "mobile-native" / "src" / "live" / "useAgoraLiveBroadcastRoom.ts"
QUALITY = ROOT / "mobile-native" / "src" / "live" / "liveStreamQuality.ts"
BOT = ROOT / "bot.py"


class TestTheArchiveIsCompositeAndIncludesGuests(unittest.TestCase):
    """Stage 32: one Live, one recording, everyone who was on stage in it."""

    @classmethod
    def setUpClass(cls):
        cls.src = RECORDING.read_text(encoding="utf-8", errors="replace")

    def test_the_recording_mode_is_mix_not_individual(self):
        # Individual mode writes one file per UID and would require a decision
        # this codebase has never made: which of six files is "the replay".
        self.assertIn('MODE = "mix"', self.src)

    def test_the_recorder_subscribes_to_every_publisher(self):
        # No subscribeVideoUids/subscribeAudioUids filter means every publisher
        # is recorded. Adding one would silently drop guests from the replay
        # while leaving them perfectly visible in the live broadcast, which is
        # the sort of divergence nobody notices until a guest asks where they
        # went.
        self.assertNotIn("subscribeVideoUids", self.src)
        self.assertNotIn("subscribeAudioUids", self.src)

    def test_the_composite_layout_is_best_fit(self):
        # mixedVideoLayout 1 is best-fit: it fills the 9:16 canvas for a solo
        # host and tiles evenly once co-hosts publish. Layout 2 (vertical)
        # requires maxResolutionUid, and without it the main pane renders black.
        self.assertIn('"mixedVideoLayout": 1', self.src)
        # As a payload key, not as the comment that explains why it is absent.
        self.assertNotIn('"maxResolutionUid"', self.src)


class TestTheReplayIsOneReel(unittest.TestCase):
    """Stage 31/32: a multi-guest Live archives to a single feed item."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")

    def test_the_reel_claim_is_idempotent(self):
        # The claim is a guarded UPDATE on an empty replay_reel_id, so a retried
        # finalize job cannot publish the same Live to the feed twice.
        self.assertIn(
            "UPDATE pulse_live_sessions SET replay_reel_id=?, updated_at=? WHERE id=? AND COALESCE(replay_reel_id,0)=0",
            self.bot_src,
        )

    def test_the_recording_starts_at_live_start_not_at_publish(self):
        # Recording used to begin only when the client reached /native-publish,
        # so a crash before that produced a Live with no replay source at all.
        self.assertIn("def pulse_live_bootstrap_recording(cur, live, *, trace_id=\"\"):", self.bot_src)
        self.assertIn("pulse_live_bootstrap_recording_async(live_id, trace_id=trace_id)", self.bot_src)


class TestThePublishLadderIsWired(unittest.TestCase):
    """Stage 24: the encoder ladder is applied, not merely defined.

    A pure module with passing tests and no call site is the most convincing
    kind of dead code: it reviews well and does nothing.
    """

    @classmethod
    def setUpClass(cls):
        cls.hook = HOOK.read_text(encoding="utf-8", errors="replace")
        cls.quality = QUALITY.read_text(encoding="utf-8", errors="replace")

    def test_the_hook_imports_and_calls_the_ladder(self):
        self.assertIn('from "./liveStreamQuality"', self.hook)
        self.assertIn("publisherVideoProfile(", self.hook)

    def test_no_fixed_720p_encoder_remains(self):
        # The literal the ladder replaced. Its return anywhere in this file
        # means some path publishes solo resolution onto a full stage.
        self.assertNotIn("{ width: 720, height: 1280 }", self.hook)

    def test_the_ladder_is_reapplied_as_the_stage_grows(self):
        # And specifically *before* the echo-scenario early return: the audio
        # scenario moves once, at two publishers, while the ladder steps again
        # at three and at five. Ordering this after the return would pin every
        # stage larger than two to the two-publisher profile.
        block = re.search(r"const setStagePublisherCount[\s\S]*?const target = nextEchoScenario", self.hook)
        self.assertIsNotNone(block, "setStagePublisherCount not found")
        self.assertIn("applyPublisherEncoder(count)", block.group(0))

    def test_an_audience_member_configures_no_encoder(self):
        # Stage 25 at the wiring level rather than the decision level.
        self.assertIn("if (!publishingVideoRef.current) return;", self.hook)

    def test_the_ladder_keeps_a_full_stage_out_of_the_2k_plus_tier(self):
        # The cost property, asserted as arithmetic rather than as a comment.
        # Agora bills on the sum of the resolutions the recorder subscribes to;
        # the Full HD ceiling is 2,073,600 and the 2K ceiling is 3,686,400.
        ladder = {}
        for count, width, height in re.findall(
            r"count <= (\d+)\) return \{ width: (\d+), height: (\d+)", self.quality
        ):
            ladder[int(count)] = (int(width), int(height))
        self.assertIn(1, ladder, "the solo rung must exist")
        tail = re.search(r"return \{ width: (\d+), height: (\d+), frameRate: \d+ \};\s*\}", self.quality)
        self.assertIsNotNone(tail, "the ladder must have a floor")
        floor = (int(tail.group(1)), int(tail.group(2)))

        def profile(count):
            for rung in sorted(ladder):
                if count <= rung:
                    return ladder[rung]
            return floor

        for publishers, ceiling in ((6, 2_073_600), (13, 3_686_400)):
            width, height = profile(publishers)
            aggregate = publishers * width * height
            self.assertLessEqual(
                aggregate,
                ceiling,
                f"{publishers} publishers at {width}x{height} aggregate to {aggregate}, "
                f"which bills above the intended tier ({ceiling})",
            )


if __name__ == "__main__":
    unittest.main()
