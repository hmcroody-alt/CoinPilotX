"""A takedown has to actually stop the sound.

The authorization and audit work is worthless if a removed track keeps playing,
and before this change it did: two of the reel read paths joined
``pulse_audio_tracks`` with no state filter at all and handed the ``audio_url``
straight to the player. Everything in this file is about the gap between "the
row says TAKEN_DOWN" and "the phone stops playing it".

Four properties are defended here:

* the sound stops -- on the single-reel payload *and* on the batch feed, which
  build their audio dicts separately and were fixed separately;
* the content survives -- video, caption, media, likes, comments and the
  creator's own ``original_audio_muted`` decision are all untouched;
* both writers are honoured -- the pre-existing admin route sets ``removed_at``
  and ``safety_status`` without knowing ``lifecycle_state`` exists, so a track
  removed the old way must strip too;
* new attachments are refused -- otherwise a creator re-attaches the track a
  minute after the takedown.

Every strip assertion is paired with a positive control on an ACTIVE track, so a
filter that accidentally blanked *everything* would fail rather than pass.
"""

import json
import os
import sqlite3
import tempfile
import unittest

_FD, _DB_PATH = tempfile.mkstemp(suffix="-music-read-paths.db")
os.close(_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import music_authority, music_service  # noqa: E402

AUTHOR_ID = 7701
POST_ID = 47001
REEL_ID = 47002
LIVE_TRACK_ID = 4801
DEAD_TRACK_ID = 4802
LEGACY_TRACK_ID = 4803
MEDIA_ID = 47003
LIVE_AUDIO_URL = "https://cdn.example.test/pulse_music/live-track.mp3"
DEAD_AUDIO_URL = "https://cdn.example.test/pulse_music/dead-track.mp3"


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_once():
    """One author, one post, one reel, three tracks.

    The reel is wired to its track through *both* ``pulse_reel_audio`` (which the
    single-reel payload joins) and ``pulse_reels.audio_track_id`` (which the feed
    batch reads). They are genuinely different lookups, so a fixture that set
    only one of them would leave half of this file testing an audio-less reel.
    """
    bot.init_db()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, 'reel-author')",
        (AUTHOR_ID,),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_posts
        (id, user_id, post_type, title, body, media_ids_json, visibility, status, moderation_status, created_at)
        VALUES (?, ?, 'video', 'Reel post', 'the caption body', ?, 'public', 'published', 'approved', '2026-09-01T00:00:00')
        """,
        (POST_ID, AUTHOR_ID, json.dumps([MEDIA_ID])),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO chat_media_uploads
        (id, context_type, context_id, media_type, mime_type, storage_key, media_url)
        VALUES (?, 'pulse_post', ?, 'video', 'video/mp4', 'pulse_reels/clip.mp4', 'https://cdn.example.test/pulse_reels/clip.mp4')
        """,
        (MEDIA_ID, str(POST_ID)),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_reels
        (id, post_id, user_id, caption, video_url, status, moderation_status, created_at)
        VALUES (?, ?, ?, 'a caption on the reel', 'https://cdn.example.test/pulse_reels/clip.mp4', 'active', 'approved', '2026-09-01T00:00:00')
        """,
        (REEL_ID, POST_ID, AUTHOR_ID),
    )
    for track_id, title, url in (
        (LIVE_TRACK_ID, "Live Song", LIVE_AUDIO_URL),
        (DEAD_TRACK_ID, "Dead Song", DEAD_AUDIO_URL),
        (LEGACY_TRACK_ID, "Legacy Song", DEAD_AUDIO_URL),
    ):
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_audio_tracks
            (id, title, artist, audio_url, cover_art_url, duration_seconds,
             safety_status, approved_by_admin, active, commercial_use_allowed, remix_edit_allowed,
             lifecycle_state, created_at)
            VALUES (?, ?, 'An Artist', ?, '', 30, 'approved', 1, 1, 1, 1, 'ACTIVE', '2026-09-01T00:00:00')
            """,
            (track_id, title, url),
        )
    conn.commit()
    conn.close()


_seed_once()


def _attach(track_id, *, baked_in=0):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_reel_audio WHERE reel_id=?", (REEL_ID,))
    cur.execute(
        """
        INSERT INTO pulse_reel_audio (reel_id, audio_track_id, start_seconds, end_seconds, volume, audio_baked_in, created_at)
        VALUES (?, ?, 0, 30, 1, ?, '2026-09-01T00:00:00')
        """,
        (REEL_ID, track_id, baked_in),
    )
    cur.execute(
        "UPDATE pulse_reels SET audio_track_id=?, sound_title='Live Song', audio_baked_in=? WHERE id=?",
        (track_id, baked_in, REEL_ID),
    )
    conn.commit()
    conn.close()


def _set_state(track_id, state, *, legacy_only=False, lifecycle_only=False):
    """Move a track's state, optionally writing only one of the two writers.

    A real transition writes both: the new ``lifecycle_state`` and the legacy
    ``active``/``safety_status``/``removed_at`` trio. The two single-writer modes
    exist because each one, alone, is a real shape the code must survive:

    * ``legacy_only`` -- what the pre-existing admin removal route produces. It
      predates ``lifecycle_state`` and will never set it.
    * ``lifecycle_only`` -- the inverse. Without this mode the catalog tests pass
      on the legacy trio alone and prove nothing about the new column, which is
      how `_load_db_tracks` kept serving removed tracks while looking covered.
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


def _reset_track(track_id):
    _set_state(track_id, music_authority.STATE_ACTIVE)


def _feed_reel():
    feed = bot.pulse_reel_feed_payload(viewer_user_id=AUTHOR_ID, limit=20)
    for reel in feed.get("reels") or []:
        if int(reel.get("reel_id") or 0) == REEL_ID:
            return reel
    return None


class ReelPayloadStripTests(unittest.TestCase):
    """`pulse_reel_payload` -- the single-reel read path."""

    def setUp(self):
        _reset_track(DEAD_TRACK_ID)
        _reset_track(LEGACY_TRACK_ID)
        _attach(DEAD_TRACK_ID)

    def test_active_track_is_served(self):
        """Positive control: without it, every strip assertion below is vacuous."""
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertEqual(payload["audio"]["audio_url"], DEAD_AUDIO_URL)
        self.assertEqual(payload["attached_audio_url"], DEAD_AUDIO_URL)
        self.assertFalse(payload.get("audio_unavailable"))

    def test_taken_down_track_loses_every_url(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        audio = payload["audio"]
        self.assertEqual(audio["audio_url"], "")
        self.assertEqual(audio["attached_audio_url"], "")
        self.assertEqual(audio["preview_url"], "")
        self.assertEqual(payload["attached_audio_url"], "")

    def test_taken_down_track_loses_its_metadata(self):
        """Title and artist go too -- a copyright takedown removes the credit."""
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertEqual(payload["audio"]["title"], "")
        self.assertEqual(payload["audio"]["artist"], "")
        self.assertEqual(payload["audio_title"], "")
        self.assertEqual(payload["audio_artist"], "")
        self.assertEqual(payload["audio"]["track_id"], 0)

    def test_client_is_told_why_it_is_silent(self):
        """Without this flag the player retries an empty url forever."""
        _set_state(DEAD_TRACK_ID, music_authority.STATE_QUARANTINED)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertTrue(payload["audio_unavailable"])
        self.assertTrue(payload["audio"]["audio_unavailable"])
        self.assertEqual(payload["audio_unavailable_state"], music_authority.STATE_QUARANTINED)

    def test_every_media_item_is_stripped_too(self):
        """The url is copied onto each media item; blanking only the audio dict leaks it."""
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertTrue(payload["media"])
        for item in payload["media"]:
            self.assertEqual(item.get("attached_audio_url", ""), "")
            self.assertEqual(item.get("audio_title", ""), "")
            self.assertTrue(item.get("audio_unavailable"))

    def test_legacy_removal_without_lifecycle_state_also_strips(self):
        """The old admin route writes `removed_at`/`safety_status` and nothing else."""
        _attach(LEGACY_TRACK_ID)
        _set_state(LEGACY_TRACK_ID, music_authority.STATE_TAKEN_DOWN, legacy_only=True)
        row = _track_row(LEGACY_TRACK_ID)
        self.assertEqual(row["lifecycle_state"], "ACTIVE")  # the legacy writer never touches it
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertTrue(payload["audio_unavailable"])
        self.assertEqual(payload["audio"]["audio_url"], "")

    def test_lifecycle_state_alone_strips_the_reel_audio(self):
        """The mirror of the legacy case: the new column with no legacy help."""
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN, lifecycle_only=True)
        row = _track_row(DEAD_TRACK_ID)
        self.assertEqual(row["safety_status"], "approved")
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertTrue(payload["audio_unavailable"])
        self.assertEqual(payload["audio"]["audio_url"], "")

    def test_purged_track_strips(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_PURGED)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertTrue(payload["audio_unavailable"])

    def test_restore_brings_the_sound_back(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertTrue(bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)["audio_unavailable"])
        _set_state(DEAD_TRACK_ID, music_authority.STATE_ACTIVE)
        payload = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        self.assertFalse(payload.get("audio_unavailable"))
        self.assertEqual(payload["audio"]["audio_url"], DEAD_AUDIO_URL)


class ContentSurvivesTests(unittest.TestCase):
    """The mission's hard line: remove the song, keep the post."""

    def setUp(self):
        _reset_track(DEAD_TRACK_ID)
        _attach(DEAD_TRACK_ID)
        self.before = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.after = bot.pulse_reel_payload(reel_id=REEL_ID, viewer_user_id=AUTHOR_ID)

    def test_the_reel_still_exists(self):
        self.assertIsNotNone(self.after)
        self.assertEqual(self.after["reel_id"], REEL_ID)
        self.assertEqual(self.after["post_id"], POST_ID)

    def test_the_caption_survives(self):
        self.assertEqual(self.after["caption"], self.before["caption"])
        self.assertEqual(self.after["caption"], "a caption on the reel")

    def test_the_video_survives(self):
        self.assertEqual(len(self.after["media"]), len(self.before["media"]))
        self.assertEqual(
            [item.get("media_url") for item in self.after["media"]],
            [item.get("media_url") for item in self.before["media"]],
        )
        self.assertTrue(self.after["media"][0]["media_url"])

    def test_engagement_survives(self):
        for field in ("reactions_count", "comments_count", "views_count"):
            if field in self.before:
                self.assertEqual(self.after.get(field), self.before.get(field), field)

    def test_the_creators_mute_decision_is_not_flipped(self):
        """Unmuting would publish camera audio the creator chose to silence."""
        self.assertTrue(self.before["audio"]["original_audio_muted"])
        self.assertTrue(self.after["audio"]["original_audio_muted"])
        self.assertTrue(self.after["original_audio_muted"])
        for item in self.after["media"]:
            self.assertTrue(item["original_audio_muted"])

    def test_no_replacement_track_is_substituted(self):
        """An auto-substituted track would be a second unlicensed use, not a fix."""
        self.assertEqual(self.after["audio"]["track_id"], 0)
        self.assertEqual(self.after["audio"]["title"], "")
        self.assertNotEqual(self.after["audio"].get("audio_url"), LIVE_AUDIO_URL)

    def test_the_row_is_not_deleted(self):
        conn = _connect()
        rows = conn.execute("SELECT COUNT(*) AS n FROM pulse_reels WHERE id=?", (REEL_ID,)).fetchone()["n"]
        posts = conn.execute("SELECT COUNT(*) AS n FROM pulse_posts WHERE id=?", (POST_ID,)).fetchone()["n"]
        conn.close()
        self.assertEqual(rows, 1)
        self.assertEqual(posts, 1)


class FeedBatchStripTests(unittest.TestCase):
    """`pulse_reel_feed_payload` -- a separate build of the same payload.

    This is the path the Reels tab actually calls. It hydrates tracks with
    ``SELECT * FROM pulse_audio_tracks WHERE id IN (...)`` off
    ``pulse_reels.audio_track_id``, which is a different lookup from the
    single-reel JOIN; fixing one and not the other is how the leak survived.
    """

    def setUp(self):
        _reset_track(DEAD_TRACK_ID)
        _attach(DEAD_TRACK_ID)

    def test_active_track_is_served_in_the_feed(self):
        reel = _feed_reel()
        self.assertIsNotNone(reel, "fixture reel must reach the feed or every assertion below is vacuous")
        self.assertEqual(reel["audio"]["audio_url"], DEAD_AUDIO_URL)
        self.assertEqual(reel["attached_audio_url"], DEAD_AUDIO_URL)

    def test_taken_down_track_is_stripped_in_the_feed(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        reel = _feed_reel()
        self.assertIsNotNone(reel)
        self.assertTrue(reel["audio_unavailable"])
        self.assertEqual(reel["audio"]["audio_url"], "")
        self.assertEqual(reel["attached_audio_url"], "")
        self.assertEqual(reel["audio"]["title"], "")

    def test_the_feed_still_returns_the_reel(self):
        """A filtered-out track must not filter out the video."""
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        reel = _feed_reel()
        self.assertIsNotNone(reel, "the reel must still be in the feed after its track is removed")
        self.assertEqual(reel["caption"], "a caption on the reel")
        self.assertTrue(reel.get("media"))

    def test_feed_media_items_are_stripped(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        reel = _feed_reel()
        for item in reel["media"]:
            self.assertEqual(item.get("attached_audio_url", ""), "")
            self.assertTrue(item.get("audio_unavailable"))

    def test_feed_keeps_the_mute_decision(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        reel = _feed_reel()
        self.assertTrue(reel["original_audio_muted"])

    def test_legacy_removal_strips_in_the_feed_too(self):
        _attach(LEGACY_TRACK_ID)
        _set_state(LEGACY_TRACK_ID, music_authority.STATE_TAKEN_DOWN, legacy_only=True)
        reel = _feed_reel()
        self.assertIsNotNone(reel)
        self.assertTrue(reel["audio_unavailable"])


class CatalogTests(unittest.TestCase):
    """Search, radio, suggest and the artist page all read `music_service`.

    The catalog is defended twice over: a SQL filter on each query and the
    `is_servable` guard in `public_visibility_reasons`. Either one alone hides a
    removed track, so mutating just one of them will NOT turn these tests red --
    that is redundancy, not vacuity. Disabling both does fail
    `test_lifecycle_state_alone_removes_a_track_from_search`, which is the test
    that proves the pair is load-bearing.
    """

    def setUp(self):
        _reset_track(DEAD_TRACK_ID)

    def _titles(self, tracks):
        return {str(track.get("title") or "") for track in tracks}

    def test_active_track_is_searchable(self):
        self.assertIn("Dead Song", self._titles(music_service.search_tracks("Dead", limit=50)))

    def test_taken_down_track_leaves_search(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertNotIn("Dead Song", self._titles(music_service.search_tracks("Dead", limit=50)))

    def test_taken_down_track_leaves_radio(self):
        self.assertIn("Dead Song", self._titles(music_service.radio_tracks(limit=300)))
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertNotIn("Dead Song", self._titles(music_service.radio_tracks(limit=300)))

    def test_taken_down_track_leaves_trending(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertNotIn("Dead Song", self._titles(music_service.trending_tracks(limit=100)))

    def test_public_track_lookup_by_id_is_empty(self):
        self.assertTrue(music_service.public_track(str(DEAD_TRACK_ID)))
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        self.assertFalse(music_service.public_track(str(DEAD_TRACK_ID)))

    def test_legacy_removal_leaves_search_too(self):
        _set_state(LEGACY_TRACK_ID, music_authority.STATE_TAKEN_DOWN, legacy_only=True)
        self.assertNotIn("Legacy Song", self._titles(music_service.search_tracks("Legacy", limit=50)))
        _reset_track(LEGACY_TRACK_ID)

    def test_lifecycle_state_alone_removes_a_track_from_search(self):
        """The new column has to be load-bearing on its own.

        Every other assertion in this class moves the legacy trio too, and the
        legacy trio was already filtered before any of this work -- so without
        this test the catalog is "covered" by a filter that predates the feature.
        """
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN, lifecycle_only=True)
        row = _track_row(DEAD_TRACK_ID)
        self.assertEqual(row["safety_status"], "approved")  # the legacy trio is untouched
        self.assertEqual(int(row["active"] or 0), 1)
        self.assertNotIn("Dead Song", self._titles(music_service.search_tracks("Dead", limit=50)))
        self.assertNotIn("Dead Song", self._titles(music_service.radio_tracks(limit=300)))
        self.assertFalse(music_service.public_track(str(DEAD_TRACK_ID)))

    def test_lifecycle_state_alone_refuses_a_new_attachment(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN, lifecycle_only=True)
        self.assertFalse(music_service.attach_music_payload(str(DEAD_TRACK_ID)).get("is_creator_safe"))


class AttachmentRefusalTests(unittest.TestCase):
    """A creator must not be able to re-attach the track after the takedown."""

    def setUp(self):
        _reset_track(DEAD_TRACK_ID)

    def test_active_track_is_creator_safe(self):
        payload = music_service.attach_music_payload(str(DEAD_TRACK_ID))
        self.assertTrue(payload.get("is_creator_safe"), payload.get("message"))

    def test_taken_down_track_is_refused(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_TAKEN_DOWN)
        payload = music_service.attach_music_payload(str(DEAD_TRACK_ID))
        self.assertFalse(payload.get("is_creator_safe"))

    def test_quarantined_track_is_refused(self):
        _set_state(DEAD_TRACK_ID, music_authority.STATE_QUARANTINED)
        self.assertFalse(music_service.attach_music_payload(str(DEAD_TRACK_ID)).get("is_creator_safe"))

    def test_the_visibility_reason_names_the_owner_action(self):
        """`_db_track` dropped the lifecycle columns once; then this guard read
        nothing and answered "servable" for every track."""
        reasons = music_service.public_visibility_reasons(
            {"lifecycle_state": "TAKEN_DOWN", "audio_url": DEAD_AUDIO_URL, "moderation_status": "approved"}
        )
        self.assertTrue(any("removed" in reason for reason in reasons), reasons)

    def test_every_creator_safe_lookup_filters_on_lifecycle_state(self):
        """The three "can this creator use this sound" queries all gate on the new column.

        Asserted against the source rather than through the routes because each
        of the three is reached by a different authenticated flow, and a fixture
        that only exercised one would let the other two keep serving a removed
        track. `COALESCE(commercial_use_allowed` is the marker for a
        creator-facing lookup -- the admin review queries deliberately have no
        such filter, because an owner must still be able to see what they removed.
        """
        source = open(bot.__file__, encoding="utf-8").read()
        chunks = [
            chunk[:800] for chunk in source.split("FROM pulse_audio_tracks")[1:]
            if "COALESCE(commercial_use_allowed" in chunk[:800]
        ]
        self.assertEqual(len(chunks), 3, "expected reel-create attach, reel-edit attach and save-sound")
        for chunk in chunks:
            self.assertIn("COALESCE(lifecycle_state,'ACTIVE')='ACTIVE'", chunk)
            self.assertIn("COALESCE(removed_at,'')=''", chunk)


def _track_row(track_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM pulse_audio_tracks WHERE id=?", (track_id,)).fetchone()
    conn.close()
    return dict(row or {})


if __name__ == "__main__":
    unittest.main()
