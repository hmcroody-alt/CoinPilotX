"""A takedown must not hand the creator's camera audio back to the world.

The reel read paths (covered in ``test_read_paths.py``) served a removed track's
url and had to be filtered. The *post* music paths had the opposite shape and a
worse failure: they already filtered, dropping the ``pulse_audio_tracks`` row
out of the JOIN entirely on takedown. That looks like the safe answer and is the
exact inverse of it.

The reason is on the client. ``resolveAttachedMusicPolicy`` reads a post with no
``music`` object as "this post never had a song", and answers that by playing
the original media audio unmuted. So removing a song from a video whose creator
had deliberately replaced their camera audio with it would *publish* that camera
audio -- a room, a conversation, whatever was recorded -- to everyone who scrolls
past. Nobody asked for it, and no UI anywhere would show that it had happened.

So these paths now keep the row and blank it, which is a different thing from
dropping it, and this file is about the difference. Two code paths build these
dicts independently and were fixed independently:

* ``services.pulse_feed_engine._music_for_posts`` -- the batch feed hydration;
* ``bot.pulse_video_hydrate_attached_music`` -- the single video/post hydration.

The assertions are on the presence and shape of the ``music`` dict rather than
on any rendering, because that dict is the entire input the client's audio
policy gets. ``original_audio_muted`` is asserted to still be ``True`` in both:
it is the one field that decides whether the creator's audio stays silent, and
it is the field a naive "clear everything about the removed track" fix would
helpfully reset.

Positive controls on an ACTIVE track sit beside every strip assertion. Without
them a change that blanked all music everywhere would pass this file.
"""

import json
import os
import sqlite3
import tempfile
import unittest

_FD, _DB_PATH = tempfile.mkstemp(suffix="-music-post-paths.db")
os.close(_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import music_authority, pulse_feed_engine  # noqa: E402

AUTHOR_ID = 7801
POST_ID = 48001
VIDEO_ID = 48002
MEDIA_ID = 48003
TRACK_ID = 4901
LICENSED_OUT_TRACK_ID = 4902
AUDIO_URL = "https://cdn.example.test/pulse_music/post-track.mp3"
LICENSED_OUT_URL = "https://cdn.example.test/pulse_music/licensed-out.mp3"


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_once():
    """One author, one post with one video media item, one attached track.

    The attachment is written to ``pulse_content_music`` under ``content_type
    ='post'`` for the feed batch and ``content_type='video'`` for the single
    hydration, because those are genuinely separate lookups -- the batch matches
    on ``content_id IN (post ids)`` and the single one walks a candidate list
    starting at ``("video", item["id"])``. Seeding only one would leave half of
    this file asserting about a post with no music at all, which would pass.
    """
    bot.init_db()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, 'post-author')",
        (AUTHOR_ID,),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_posts
        (id, user_id, post_type, title, body, media_ids_json, visibility, status, moderation_status, created_at)
        VALUES (?, ?, 'video', 'Post title', 'the caption body', ?, 'public', 'published', 'approved', '2026-09-01T00:00:00')
        """,
        (POST_ID, AUTHOR_ID, json.dumps([MEDIA_ID])),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO chat_media_uploads
        (id, context_type, context_id, media_type, mime_type, storage_key, media_url)
        VALUES (?, 'pulse_post', ?, 'video', 'video/mp4', 'pulse_posts/clip.mp4', 'https://cdn.example.test/pulse_posts/clip.mp4')
        """,
        (MEDIA_ID, str(POST_ID)),
    )
    for track_id, title, url, commercial in (
        (TRACK_ID, "Post Song", AUDIO_URL, 1),
        (LICENSED_OUT_TRACK_ID, "Unlicensed Song", LICENSED_OUT_URL, 0),
    ):
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_audio_tracks
            (id, title, artist, audio_url, cover_art_url, duration_seconds,
             safety_status, approved_by_admin, active, commercial_use_allowed, remix_edit_allowed,
             lifecycle_state, created_at)
            VALUES (?, ?, 'An Artist', ?, '', 30, 'approved', 1, 1, ?, ?, 'ACTIVE', '2026-09-01T00:00:00')
            """,
            (track_id, title, url, commercial, commercial),
        )
    conn.commit()
    conn.close()


_seed_once()


def _attach(track_id, *, content_type, content_id, snapshot=None):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM pulse_content_music WHERE content_type=? AND content_id=?",
        (content_type, content_id),
    )
    cur.execute(
        """
        INSERT INTO pulse_content_music
        (content_type, content_id, audio_track_id, title, artist, source,
         license_snapshot_json, audio_start_time, audio_volume, original_audio_muted, created_at)
        VALUES (?, ?, ?, 'Snapshot Title', 'Snapshot Artist', 'PulseSoc',
                ?, 0, 1, 1, '2026-09-01T00:00:00')
        """,
        (content_type, content_id, track_id, json.dumps(snapshot or {})),
    )
    conn.commit()
    conn.close()


def _attach_everywhere(track_id, *, snapshot=None):
    _attach(track_id, content_type="post", content_id=POST_ID, snapshot=snapshot)
    _attach(track_id, content_type="video", content_id=VIDEO_ID, snapshot=snapshot)


def _set_state(track_id, state, *, legacy_only=False, lifecycle_only=False):
    """Move a track's state, optionally exercising one writer in isolation.

    Same two single-writer modes as ``test_read_paths.py``. ``legacy_only`` is
    what the pre-existing admin removal route produces -- it predates
    ``lifecycle_state`` and will never set it. ``lifecycle_only`` is the mirror,
    and it is the mode that catches a path still reading only the legacy trio.
    """
    columns = music_authority.legacy_columns_for_state(state, now="2026-09-02T00:00:00", actor_admin_id=1)
    if lifecycle_only:
        columns = {}
    if not legacy_only:
        columns["lifecycle_state"] = state
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = _connect()
    conn.execute(
        f"UPDATE pulse_audio_tracks SET {assignments} WHERE id=?",
        (*columns.values(), track_id),
    )
    conn.commit()
    conn.close()


def _batch_music(post_id=POST_ID):
    return (pulse_feed_engine._music_for_posts([post_id]) or {}).get(post_id)


def _hydrated_video():
    """Run the single-video hydration against a minimal video payload.

    ``pulse_video_hydrate_attached_music`` takes a live cursor, so this hands it
    a real one rather than a stub: the query it runs is half of what is under
    test here, and a stub cursor would assert about a query string instead of a
    result.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        hydrated = bot.pulse_video_hydrate_attached_music(
            cur, [{"id": VIDEO_ID, "source_type": "feed_video", "source_id": POST_ID}]
        )
    finally:
        conn.close()
    return hydrated[0]


class FeedBatchHydrationTests(unittest.TestCase):
    """``_music_for_posts`` -- what the scrolling feed gets."""

    def setUp(self):
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        _attach_everywhere(TRACK_ID)

    def test_active_track_is_served(self):
        """Positive control. Every strip assertion below depends on it."""
        music = _batch_music()
        self.assertIsNotNone(music)
        self.assertEqual(music["audio_url"], AUDIO_URL)
        self.assertEqual(music["attached_audio_url"], AUDIO_URL)
        self.assertFalse(music["audio_unavailable"])

    def test_a_taken_down_track_still_produces_a_music_object(self):
        """The whole point: dropping the row is what unmutes the camera audio.

        If this returns ``None`` the client sees a post with no attached music
        and plays the original audio, which is the failure this file exists for.
        """
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertIsNotNone(music, "a removed track must leave a music object behind")
        self.assertTrue(music["audio_unavailable"])

    def test_a_taken_down_track_has_no_url_on_any_field(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertEqual(music["audio_url"], "")
        self.assertEqual(music["attached_audio_url"], "")
        self.assertEqual(music["preview_url"], "")

    def test_a_taken_down_track_loses_its_credit(self):
        """Title and artist go with the url -- a copyright takedown removes both."""
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertEqual(music["title"], "")
        self.assertEqual(music["artist"], "")

    def test_the_snapshot_title_does_not_leak_back_in(self):
        """``pulse_content_music`` keeps its own copy of the metadata.

        The attach-time snapshot is a denormalised row that a takedown never
        rewrites, so a blanking that only cleared the joined columns would still
        serve the song's name from here.
        """
        _attach_everywhere(
            TRACK_ID,
            snapshot={"title": "Snapshot Song", "artist": "Snapshot Artist", "audio_url": AUDIO_URL},
        )
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertEqual(music["title"], "")
        self.assertEqual(music["artist"], "")
        self.assertEqual(music["audio_url"], "", "the snapshot's own url must not be served either")

    def test_the_client_is_told_which_state_it_is(self):
        _set_state(TRACK_ID, music_authority.STATE_QUARANTINED)
        music = _batch_music()
        self.assertEqual(music["audio_unavailable_state"], music_authority.STATE_QUARANTINED)

    def test_the_creators_mute_decision_survives(self):
        """The field that decides whether the camera audio stays silent."""
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertTrue(music["original_audio_muted"])

    def test_no_replacement_track_is_substituted(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        music = _batch_music()
        self.assertEqual(str(music["track_id"]), str(TRACK_ID))
        self.assertEqual(music["audio_url"], "")

    def test_a_legacy_removal_strips_too(self):
        """The old admin route sets ``removed_at``/``safety_status`` and nothing else."""
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN, legacy_only=True)
        music = _batch_music()
        self.assertIsNotNone(music)
        self.assertTrue(music["audio_unavailable"])
        self.assertEqual(music["audio_url"], "")

    def test_lifecycle_state_alone_strips_too(self):
        """The mirror: the new column with no help from the legacy trio."""
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN, lifecycle_only=True)
        music = _batch_music()
        self.assertIsNotNone(music)
        self.assertTrue(music["audio_unavailable"])
        self.assertEqual(music["audio_url"], "")

    def test_an_inactive_track_strips_even_with_no_lifecycle_signal(self):
        """``active``/``approved_by_admin`` were dropped from the WHERE clause.

        They used to filter the row out, so if the Python check that replaced
        them were wrong the track would start being *served* rather than merely
        stripped -- a regression in the opposite direction from a takedown.
        """
        conn = _connect()
        conn.execute("UPDATE pulse_audio_tracks SET active=0 WHERE id=?", (TRACK_ID,))
        conn.commit()
        conn.close()
        music = _batch_music()
        self.assertIsNotNone(music)
        self.assertTrue(music["audio_unavailable"])
        self.assertEqual(music["audio_url"], "")

    def test_an_unapproved_track_strips(self):
        conn = _connect()
        conn.execute("UPDATE pulse_audio_tracks SET approved_by_admin=0 WHERE id=?", (TRACK_ID,))
        conn.commit()
        conn.close()
        music = _batch_music()
        self.assertIsNotNone(music)
        self.assertTrue(music["audio_unavailable"])

    def test_restoring_brings_the_song_back(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertTrue(_batch_music()["audio_unavailable"])
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        music = _batch_music()
        self.assertFalse(music["audio_unavailable"])
        self.assertEqual(music["audio_url"], AUDIO_URL)
        self.assertNotEqual(music["title"], "")

    def test_a_licensing_failure_still_drops_the_row(self):
        """Deliberately unchanged, and worth pinning so nobody "fixes" it.

        ``commercial_use_allowed``/``remix_edit_allowed`` answer an attach-time
        question that predates this work. A track that was never licensed for
        this use is not a track the owner took down, and turning it into an
        "Audio unavailable" notice would tell viewers a removal happened that
        did not.
        """
        _attach_everywhere(LICENSED_OUT_TRACK_ID)
        self.assertIsNone(_batch_music())


class MediaStampingTests(unittest.TestCase):
    """``_media_with_attached_music`` -- the per-media-item copy of the same data.

    Surfaces that read the media record instead of the post (the fullscreen
    viewer among them) get their url from here, so a blanking that stopped at
    the post's ``music`` dict would leave a live url one level down.
    """

    def _media(self):
        return [{"id": MEDIA_ID, "media_type": "video", "url": "https://cdn.example.test/pulse_posts/clip.mp4"}]

    def test_an_active_track_is_stamped_onto_the_media(self):
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        _attach_everywhere(TRACK_ID)
        stamped = pulse_feed_engine._media_with_attached_music(self._media(), _batch_music())
        self.assertEqual(stamped[0]["attached_audio_url"], AUDIO_URL)
        self.assertFalse(stamped[0]["audio_unavailable"])

    def test_a_removed_track_is_still_stamped_so_the_record_is_blanked(self):
        """The url check used to skip writing entirely when every url was blank.

        Skipping leaves whatever the media item already carried, which for a
        record hydrated elsewhere can be the live url the takedown just removed.
        """
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        _attach_everywhere(TRACK_ID)
        stale = self._media()
        stale[0]["attached_audio_url"] = AUDIO_URL
        stale[0]["audio_title"] = "Post Song"
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        stamped = pulse_feed_engine._media_with_attached_music(stale, _batch_music())
        self.assertEqual(stamped[0]["attached_audio_url"], "")
        self.assertEqual(stamped[0]["audio_title"], "")
        self.assertEqual(stamped[0]["audio_artist"], "")
        self.assertTrue(stamped[0]["audio_unavailable"])

    def test_the_media_records_mute_decision_survives(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        _attach_everywhere(TRACK_ID)
        stamped = pulse_feed_engine._media_with_attached_music(self._media(), _batch_music())
        self.assertTrue(stamped[0]["original_audio_muted"])

    def test_a_post_that_never_had_music_is_untouched(self):
        """The other half of the ``if not music`` branch, and the anti-vacuity pair.

        "No music" and "music that was removed" must not converge: this one has
        to come back with no audio keys at all, or the blanking above would be
        indistinguishable from the ordinary case and could be implemented by
        stamping every post on the platform.
        """
        stamped = pulse_feed_engine._media_with_attached_music(self._media(), None)
        self.assertEqual(len(stamped), 1)
        self.assertNotIn("attached_audio_url", stamped[0])
        self.assertNotIn("audio_unavailable", stamped[0])


class SingleVideoHydrationTests(unittest.TestCase):
    """``bot.pulse_video_hydrate_attached_music`` -- the single-item read path."""

    def setUp(self):
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        _attach_everywhere(TRACK_ID)

    def test_active_track_is_served(self):
        item = _hydrated_video()
        self.assertEqual(item["music"]["audio_url"], AUDIO_URL)
        self.assertEqual(item["attached_audio_url"], AUDIO_URL)
        self.assertFalse(item["audio_unavailable"])

    def test_a_taken_down_track_still_produces_a_music_object(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        item = _hydrated_video()
        self.assertIn("music", item, "a removed track must leave a music object behind")
        self.assertTrue(item["music"]["audio_unavailable"])
        self.assertTrue(item["audio_unavailable"])

    def test_a_taken_down_track_has_no_url_on_any_field(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        item = _hydrated_video()
        self.assertEqual(item["music"]["audio_url"], "")
        self.assertEqual(item["music"]["attached_audio_url"], "")
        self.assertEqual(item["music"]["preview_url"], "")
        self.assertEqual(item["attached_audio_url"], "")

    def test_a_taken_down_track_loses_its_credit(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        item = _hydrated_video()
        self.assertEqual(item["music"]["title"], "")
        self.assertEqual(item["music"]["artist"], "")
        self.assertEqual(item["audio_title"], "")
        self.assertEqual(item["audio_artist"], "")

    def test_the_client_is_told_which_state_it_is(self):
        _set_state(TRACK_ID, music_authority.STATE_PURGED)
        item = _hydrated_video()
        self.assertEqual(item["music"]["audio_unavailable_state"], music_authority.STATE_PURGED)

    def test_the_creators_mute_decision_survives(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        item = _hydrated_video()
        self.assertTrue(item["music"]["original_audio_muted"])
        self.assertTrue(item["original_audio_muted"])

    def test_the_video_itself_survives(self):
        """The visual is untouched -- only the sound is removed."""
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        item = _hydrated_video()
        self.assertEqual(item["id"], VIDEO_ID)
        self.assertEqual(item["source_id"], POST_ID)

    def test_a_legacy_removal_strips_too(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN, legacy_only=True)
        item = _hydrated_video()
        self.assertTrue(item["audio_unavailable"])
        self.assertEqual(item["music"]["audio_url"], "")

    def test_lifecycle_state_alone_strips_too(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN, lifecycle_only=True)
        item = _hydrated_video()
        self.assertTrue(item["audio_unavailable"])
        self.assertEqual(item["music"]["audio_url"], "")

    def test_restoring_brings_the_song_back(self):
        _set_state(TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertTrue(_hydrated_video()["audio_unavailable"])
        _set_state(TRACK_ID, music_authority.STATE_ACTIVE)
        item = _hydrated_video()
        self.assertFalse(item["audio_unavailable"])
        self.assertEqual(item["music"]["audio_url"], AUDIO_URL)


if __name__ == "__main__":
    unittest.main()
