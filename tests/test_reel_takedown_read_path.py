"""That a blocked Reel stops being served, not just stops being approved.

The write half of this lives in `tests/test_measured_duration_wiring.py`: an
over-long video blocks its upload *and* marks `pulse_reels.moderation_status`.
That mark is worth nothing on its own -- the bug it exists to close was a read
path, and a read path that ignores the column is exactly the state the codebase
was already in for `chat_media_uploads.is_available`.

`pulse_reels.video_url` is a denormalized copy of the playback URL. On read it
overrides the post's withheld media, because `pulse_reel_payload` merges
reel-row values over post values, and `reel_has_playable_video` applies an
`is_available` guard to media items and then appends `video_url` with no guard at
all. So the video survived its own takedown on the one surface long video is for.

Two things are asserted here. That the chokepoint every reels lane already calls
refuses a blocked Reel -- the check that does not depend on a query remembering
anything. And that the three queries which load reel rows all carry the filter,
because "three places need the same condition" is the shape of bug where one of
them is added later without it.
"""

from __future__ import annotations

import os
import re
import tempfile

_bootstrap = tempfile.mktemp(prefix="reel-takedown-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap}"

from tests.test_live_replay_worker import media_worker  # noqa: E402

bot = media_worker.bot

PLAYABLE = "https://stream.mux.com/vod.m3u8"
BLOCKED_FILTER = "COALESCE(moderation_status,'approved')!='blocked'"


class TestTheChokepointRefusesABlockedReel:
    """`reel_has_playable_video` gates both reels lanes and the supplement pass."""

    def test_a_blocked_reel_is_not_playable_even_with_a_video_url(self):
        # The denormalized URL is still there and still resolvable -- that is the
        # whole problem. Being blocked has to be enough on its own.
        assert bot.reel_has_playable_video({"video_url": PLAYABLE}) is True
        assert bot.reel_has_playable_video({"video_url": PLAYABLE, "moderation_status": "blocked"}) is False

    def test_a_blocked_reel_is_not_rescued_by_its_post_media(self):
        # A Reel whose upload is blocked normally loses its media list to the feed's
        # own filter. If some path hands the media over anyway, blocked still wins.
        reel = {
            "moderation_status": "blocked",
            "media": [{"media_type": "video", "playback_url": PLAYABLE, "is_available": True}],
        }
        assert bot.reel_has_playable_video(reel) is False

    def test_an_ordinary_reel_is_unaffected(self):
        # The guard reads a column that is 'approved' by default and absent on the
        # legacy rows, so neither may be read as blocked. A reel with a URL and no
        # media row is the common legacy shape and must keep playing.
        assert bot.reel_has_playable_video({"video_url": PLAYABLE, "moderation_status": "approved"}) is True
        assert bot.reel_has_playable_video({"video_url": PLAYABLE, "moderation_status": None}) is True
        assert bot.reel_has_playable_video({"video_url": PLAYABLE, "moderation_status": "BLOCKED"}) is False

    def test_a_reel_with_no_video_at_all_is_still_not_playable(self):
        assert bot.reel_has_playable_video({}) is False
        assert bot.reel_has_playable_video({"video_url": "/static/missing.mp4"}) is False


def _function_source(name):
    """Slice a function out of bot.py by text, not by AST line index.

    `ast` and `str.splitlines()` disagree about U+2028/U+2029, and bot.py has
    contained a raw one: every AST-to-source line index after it is shifted, so a
    test like this one silently reads the wrong function and passes.
    """
    source = open(bot.__file__, encoding="utf-8").read()
    start = source.index(f"\ndef {name}(")
    end = source.find("\ndef ", start + 1)
    return source[start:end if end > 0 else len(source)]


class TestEveryReelReadCarriesTheFilter:
    def test_the_reel_loaders_filter_blocked_rows(self):
        # pulse_reel_payload serves the single-Reel page and the supplement pass;
        # pulse_reel_feed_payload serves the lanes. Between them they read
        # pulse_reels four times, and a Reel is republished by whichever one forgets.
        for name in ("pulse_reel_payload", "pulse_reel_feed_payload"):
            source = _function_source(name)
            reads = len(re.findall(r"FROM pulse_reels", source))
            assert reads, f"{name} no longer reads pulse_reels -- this test is measuring nothing"
            filtered = source.count(BLOCKED_FILTER)
            assert filtered >= reads, (
                f"{name} reads pulse_reels {reads} time(s) but filters blocked rows "
                f"{filtered} time(s). An unfiltered read republishes a Reel whose "
                "video was taken down for breaking the duration ceiling."
            )
