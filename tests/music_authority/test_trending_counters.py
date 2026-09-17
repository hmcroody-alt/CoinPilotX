"""One writer for a track's popularity, not two.

`pulse_trending_sounds` mirrors two columns that `pulse_audio_tracks` already
has, `trend_score` and `usage_count`, and the sounds picker reads
`COALESCE(ts.trend_score, at.trend_score, 0)` -- the mirror wins when a row
exists. So whether two tracks are comparable in that ORDER BY depends on whether
each has a mirror row.

In production none of the 148 real uploads had one. The only INSERT into
`pulse_trending_sounds` was in the retired `init_db()` seed, so the reel-create
path's `UPDATE ... WHERE audio_track_id=?` matched a seeded placeholder and never
a track a user uploaded. All 1,540 trending rows pointed at placeholders and none
at real music; the UPDATE had never fired for a real track in the product's life.

Removing the seed removed the last way a row could get in, which is what makes
the mirror decidably dead rather than merely unused. The UPDATE went with it.
What is pinned here is the consequence: popularity is counted in exactly one
place, `pulse_music_event`, which counts every surface -- reel, post, video,
status -- rather than reel attachments alone.

The source assertions are deliberate. A behavioural test cannot see the
difference: with the table empty, an ORDER BY that consults the mirror and one
that does not return the same rows in the same order. The defect is reachable
only by putting a row back, so the guard is on the code that would put one there.
"""
import io
import os
import re
import sqlite3
import sys
import tempfile
import tokenize
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="music_trending_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

BOT_SOURCE = open(bot.__file__, encoding="utf-8").read()


def _source_without_comments(text):
    """Comments are not code, and the comment at the removal site quotes the SQL.

    A plain `re.findall` over the file matched the explanation of why the
    statement was removed and reported it as the statement itself -- a guard that
    fails when you document the thing it guards is a guard nobody keeps. Tokenize
    rather than strip `#` by hand: a `#` inside a string literal is not a comment,
    and SQL in this file is all string literals.
    """
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return "\n".join(out)


BOT_CODE = _source_without_comments(BOT_SOURCE)


class TrendingMirrorIsNotWrittenTests(unittest.TestCase):
    def test_nothing_inserts_into_the_trending_mirror(self):
        """The property that makes the removed UPDATE dead rather than rare."""
        inserts = re.findall(
            r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+pulse_trending_sounds", BOT_CODE, re.I
        )
        self.assertEqual(
            inserts,
            [],
            "something inserts into pulse_trending_sounds again -- the picker's "
            "COALESCE(ts.trend_score, at.trend_score) then ranks that track on a "
            "different scale from every track without a mirror row",
        )

    def test_nothing_updates_the_trending_mirrors_counters(self):
        updates = re.findall(r"UPDATE\s+pulse_trending_sounds", BOT_CODE, re.I)
        self.assertEqual(
            updates, [], "the dead trending UPDATE is back; popularity has two writers"
        )

    def test_the_table_still_exists_and_is_still_read(self):
        """Negative control on the two assertions above.

        They would also pass if `pulse_trending_sounds` had been dropped outright,
        which is a different and much larger change -- existing rows in a database
        that was never cleaned would start erroring the picker's join. Keeping the
        table and its read is the intended state, so it is asserted, not assumed.
        """
        self.assertIn("CREATE TABLE IF NOT EXISTS pulse_trending_sounds", BOT_CODE)
        self.assertIn("LEFT JOIN pulse_trending_sounds ts ON ts.audio_track_id=at.id", BOT_CODE)


class SingleCounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()

    def setUp(self):
        self.conn = sqlite3.connect(_DB_PATH)
        self.conn.execute("DELETE FROM pulse_audio_tracks")
        self.conn.execute(
            "INSERT INTO pulse_audio_tracks (id, title, artist, uploader_user_id, audio_url, "
            "usage_count, trend_score, play_count, reel_use_count, video_use_count, created_at) "
            "VALUES (900, 'Counted Once', 'PulseSoc Music', 15, 'https://cdn.example/a.mp3', "
            "0, 0, 0, 0, 0, '2026-09-17T00:00:00')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM pulse_audio_tracks WHERE id=900")
        self.conn.commit()
        self.conn.close()

    def _counters(self):
        return self.conn.execute(
            "SELECT usage_count, trend_score, play_count, reel_use_count, video_use_count "
            "FROM pulse_audio_tracks WHERE id=900"
        ).fetchone()

    def _event(self, event_type):
        cur = self.conn.cursor()
        bot.pulse_music_event(
            cur, track_id=900, user_id=15, event_type=event_type, surface="reels", content_id=1
        )
        self.conn.commit()

    def test_a_reel_use_moves_the_tracks_own_counters(self):
        self._event("use_reel")
        usage, trend, _, reel_uses, _ = self._counters()
        self.assertEqual((usage, trend, reel_uses), (1, 1, 1))

    def test_every_use_surface_counts_toward_the_same_score(self):
        """The reason the mirror was the wrong place to count.

        The removed UPDATE only ran on the reel-create path. A track used in a
        post or a video would have been ranked below an equally popular track
        used in reels, for no reason a user could see.
        """
        for event_type in ("use_reel", "use_post", "use_video"):
            self._event(event_type)
        usage, trend, _, reel_uses, video_uses = self._counters()
        self.assertEqual(usage, 3, "each use surface should count once")
        self.assertEqual(trend, 3)
        self.assertEqual(reel_uses, 1)
        self.assertEqual(video_uses, 2, "post and video share the video_use_count column")

    def test_a_play_is_not_a_use(self):
        """Positive control that the counters are not simply incremented by everything."""
        self._event("play")
        usage, trend, plays, _, _ = self._counters()
        self.assertEqual(plays, 1)
        self.assertEqual((usage, trend), (0, 0), "a play should not inflate the use score")


if __name__ == "__main__":
    unittest.main()
