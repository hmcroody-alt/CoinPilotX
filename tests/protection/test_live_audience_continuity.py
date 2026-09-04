"""Stages 29 and 31 — the audience's view of a Live must not notice the stage.

Static architecture guards over ``bot.py``, in the same style as
``test_live_moderation_authority.py``: the source text is read rather than
imported, because importing ``bot`` costs more than the whole rest of the suite.

Both properties here fail silently. A viewer count that drops when a guest is
promoted still renders a number, and a second discovery card for the same
broadcast still opens a working player — so nothing errors, nothing alerts, and
the only signal is a host asking why bringing a guest up costs them their
audience, or a feed showing the same Live twice.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"


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


class TestViewerCountHasOneRule(unittest.TestCase):
    """Stage 29: one server rule, spelled out in one place."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")

    def test_the_presence_predicate_is_written_exactly_once(self):
        # Three hand-written copies of this query used to exist. Two endpoints
        # disagreeing about who counts as present is a viewer number that
        # changes depending on which poll happened to land last.
        reads = re.findall(r"FROM pulse_live_viewers", self.bot_src)
        self.assertEqual(
            len(reads),
            1,
            "the presence query must exist only inside pulse_live_viewer_count",
        )
        # The literal status tuple may still be spelled out to *clear* presence
        # when a Live ends, which is a write and not a count. It may not be
        # spelled out anywhere that reads.
        for line in self.bot_src.splitlines():
            if "status IN ('watching','hosting')" not in line:
                continue
            self.assertIn(
                "UPDATE pulse_live_viewers",
                line,
                "presence must only be read through pulse_live_viewer_count",
            )

    def test_the_single_rule_is_a_named_function(self):
        self.assertIn("def pulse_live_viewer_count(cur, live_id):", self.bot_src)
        self.assertIn("PULSE_LIVE_PRESENT_STATUSES", self.bot_src)

    def test_the_counting_endpoints_all_call_it(self):
        for name in ("api_pulse_live_state", "api_pulse_live_watch"):
            try:
                fn = _extract_function(self.bot_src, name)
            except AssertionError:
                continue
            self.assertIn(
                "pulse_live_viewer_count(",
                fn,
                f"{name} must use the single viewer-count rule",
            )

    def test_the_stored_count_is_not_used_as_a_fallback(self):
        # Falling back to the denormalised column meant an empty presence table
        # reported whatever number the session last saw, so a Live nobody was
        # watching kept displaying its peak audience.
        self.assertNotIn(
            'get("total") or live.get("viewer_count")',
            self.bot_src,
            "the cached viewer_count must not stand in for live presence",
        )


class TestGoingOnStageIsNotLeavingTheAudience(unittest.TestCase):
    """Stage 29: promotion changes a role, not a presence."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.fn = _extract_function(cls.bot_src, "pulse_live_promote_guest_row")

    def test_promotion_does_not_touch_the_presence_table(self):
        # If a promoted viewer's presence row were removed or re-statused, the
        # viewer count would fall by one every time a guest came on stage.
        self.assertNotIn("pulse_live_viewers", self.fn)

    def test_promotion_does_not_touch_the_viewer_count_column(self):
        self.assertNotIn("viewer_count", self.fn)


class TestOneLiveIsOneDiscoveryItem(unittest.TestCase):
    """Stage 31: a multi-guest Live is still one broadcast in the feed."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = BOT.read_text(encoding="utf-8", errors="replace")
        cls.promote = _extract_function(cls.bot_src, "pulse_live_promote_guest_row")

    def test_promotion_creates_no_session_row(self):
        # The architectural rule the whole mission rests on: one Live session,
        # one channel. A guest joining must never mint a second session.
        self.assertNotIn("INSERT INTO pulse_live_sessions", self.promote)

    def test_promotion_creates_no_feed_post(self):
        # Six people on stage is one broadcast with six publishers, not six
        # broadcasts. A second feed post would put the same Live in the feed
        # twice and split its viewers between two cards.
        self.assertNotIn("ensure_live_feed_post(", self.promote)
        self.assertNotIn("live_discovery_service.live_card(", self.promote)

    def test_the_feed_post_is_created_only_where_a_live_begins(self):
        owners = set()
        for match in re.finditer(r"ensure_live_feed_post\(", self.bot_src):
            head = self.bot_src.rfind("\ndef ", 0, match.start())
            name = re.match(r"\ndef (\w+)\(", self.bot_src[head:head + 200])
            owners.add(name.group(1) if name else "<module>")
        self.assertTrue(owners, "expected at least one feed-post creation site")
        for owner in owners:
            self.assertNotIn(
                "guest",
                owner,
                f"{owner} creates a discovery item; no guest path may do that",
            )


if __name__ == "__main__":
    unittest.main()
