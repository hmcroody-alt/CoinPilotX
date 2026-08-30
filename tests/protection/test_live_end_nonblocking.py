"""Zero-delay live end: static architecture guards.

Asserts the /end endpoint stays a fast ack (no inline replay publication,
video indexing, or follower fan-out) and that the durable background path
remains in place. Reads source text; does not import bot (too heavy).

Principle: docs/never_block_the_user.md
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"
MEDIA_WORKER = ROOT / "media_worker.py"


def _extract_function(source: str, name: str) -> str:
    """Return the source of a top-level def by name (indentation-scoped)."""
    match = re.search(rf"^def {name}\(", source, re.M)
    if not match:
        raise AssertionError(f"function {name} not found")
    start = match.start()
    rest = source[match.end():]
    nxt = re.search(r"^(?:def |@|# ={3,})", rest, re.M)
    end = match.end() + (nxt.start() if nxt else len(rest))
    return source[start:end]


class TestLiveEndFastAck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.end_fn = _extract_function(cls.bot_src, "api_pulse_live_end")

    def test_no_inline_replay_publish(self):
        self.assertNotIn(
            "pulse_live_publish_replay_reel(", self.end_fn,
            "api_pulse_live_end must not publish the replay reel inline; "
            "that work belongs to media_worker (see docs/never_block_the_user.md)",
        )

    def test_no_inline_video_indexing(self):
        self.assertNotIn(
            "pulse_video_index_upsert(", self.end_fn,
            "api_pulse_live_end must not index video inline",
        )

    def test_no_inline_follower_fanout(self):
        self.assertNotIn(
            "pulse_notify_followers(", self.end_fn,
            "api_pulse_live_end must not fan out follower notifications inline",
        )

    def test_returns_replay_status(self):
        self.assertIn('"replay_status"', self.end_fn)
        self.assertIn('"ended"', self.end_fn)

    def test_ack_telemetry_present(self):
        self.assertIn("PULSE_LIVE_END_ACK", self.end_fn)

    def test_notify_moved_to_single_fire_publish_point(self):
        publish_fn = _extract_function(self.bot_src, "pulse_live_publish_replay_reel")
        self.assertIn(
            "pulse_notify_followers(", publish_fn,
            "replay-ready follower notification must fire at the idempotent "
            "created:True point in pulse_live_publish_replay_reel",
        )

    def test_media_worker_handles_cdn_replays(self):
        worker_src = MEDIA_WORKER.read_text(encoding="utf-8", errors="replace")
        self.assertIn("mark_live_feed_replay_ready", worker_src)
        self.assertIn("REPLAYS_READY_TO_PUBLISH", worker_src)


if __name__ == "__main__":
    unittest.main()
