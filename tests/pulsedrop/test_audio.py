"""Who is allowed to put music under a machine's post, and how it comes back off.

What this suite is defending
----------------------------
PulseDrop publishes to a public feed, under a verified badge, over listings the
platform earns on. Music under that is a synchronization use in commercial
content — the licence a record label sends a letter about. Nobody here has been
granted one in writing.

That is not a hypothetical reading of the catalogue, it is what the catalogue
says. All 148 rows in ``pulse_audio_tracks`` carry
``license_type='artist rights confirmed upload'``, ``commercial_use_allowed=1``
and ``approved_by_admin=1``; 147 were uploaded by one account; every single
``proof_url`` is the synthesized string ``artist-upload:<uid>:<timestamp>`` and
every ``proof_file`` is empty. Behind all of it is one sentence the uploader
agreed to: "I confirm that I own this music or have the legal right to upload
it." For a member attaching a track to their own Reel that is the correct
posture — they made the claim and they carry it. PulseDrop is not a member.

So the platform's own music filter is treated here as necessary and not
sufficient, and the tests below assert the three properties that follow:

**Nothing plays that a human did not clear.** An empty ``pulsedrop_audio_beds``
means silence, and reaching silence requires no switch, no deploy and no
configuration — it is where a deployment starts.

**A clearance can be taken back, and taking it back reaches the past.** The
track is attached to the Reel row and resolved per request, never muxed into the
MP4. Withdrawing a clearance, taking a track down, or flipping the switch off
silences Reels published months ago on the next scroll. A test that only proved
new Reels went quiet would be testing the easy half.

**Music never costs a publication.** Everything in :mod:`services.pulsedrop.audio`
runs after the Reel exists, and every failure in it is swallowed. A Reel that
could not get a bed is a silent Reel; a Reel that failed to publish because of a
song is a bug.

Why this suite imports the monolith
-----------------------------------
``attach`` writes three production tables and one of them upserts on
``ON CONFLICT(reel_id, audio_track_id)``, which needs the real unique
constraint. A hand-declared fixture table would let that clause pass without a
constraint behind it and quietly prove nothing, so the schema comes from
``init_db()`` via the ``monolith`` fixture, as in ``test_read_path``.
"""

from __future__ import annotations

import json

import pytest

from services.pulsedrop import audio, config, schema

#: A track shaped exactly like production's: admin-approved, commercial use
#: allowed, and "proof" that is a breadcrumb rather than a document. The point
#: of the fixture is that it *passes* every platform rule — if clearance were
#: redundant with those rules, every test below would still be green.
_TRACK_DEFAULTS = {
    "artist": "PulseSoc Music",
    "audio_url": "https://cdn.example/track.mp3",
    "duration_seconds": 0.0,
    "source_type": "artist_upload",
    "source_provider": "original_pulse_sound",
    "license_type": "artist rights confirmed upload",
    "commercial_use_allowed": 1,
    "remix_edit_allowed": 1,
    "attribution_required": 0,
    "proof_url": "artist-upload:15:2026-06-21T00:26:12",
    "approved_by_admin": 1,
    "active": 1,
    "safety_status": "approved",
    "rights_confirmed": 1,
    "lifecycle_state": "ACTIVE",
    "legal_hold": 0,
    "removed_at": "",
    "uploader_user_id": 15,
}


@pytest.fixture()
def db(monolith):
    """A cursor factory over the real schema, wiped between tests."""
    from services import db as platform_db

    def run(sql, params=()):
        conn = platform_db.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()] if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    schema.reset_ready_flag()
    schema.ensure_schema()
    for table in ("pulsedrop_audio_beds", "pulse_reel_audio", "pulse_content_music",
                  "pulse_reels", "pulse_audio_tracks", "pulsedrop_publications"):
        run(f"DELETE FROM {table}")
    return run


def _track(db, track_id: int, title: str = "Shine Tonight", **overrides) -> int:
    fields = {**_TRACK_DEFAULTS, "id": track_id, "title": title, **overrides}
    columns = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    db(f"INSERT INTO pulse_audio_tracks ({columns}) VALUES ({marks})", tuple(fields.values()))
    return track_id


def _reel(db, reel_id: int = 1, post_id: int = 900) -> int:
    db(
        "INSERT INTO pulse_reels (id, post_id, user_id, caption, video_url, status) "
        "VALUES (?, ?, 7, 'A Product', 'https://cdn.example/reel.mp4', 'active')",
        (reel_id, post_id),
    )
    return reel_id


@pytest.fixture()
def music_on(monkeypatch):
    monkeypatch.setenv("PULSEDROP_REEL_AUDIO_ENABLED", "true")
    monkeypatch.setenv("PULSEDROP_REEL_AUDIO_VOLUME_PERCENT", "60")
    config.invalidate_cache()
    yield
    config.invalidate_cache()


class TestNothingPlaysThatNobodyCleared:
    """The platform's music rules are the floor, not the decision."""

    def test_a_track_that_passes_every_platform_rule_is_still_not_selectable(self, db, music_on):
        """The whole suite rests on this: clearance is not a restatement."""
        _track(db, 18493)
        assert audio.cleared_beds() == []
        assert audio.select_bed(26) == {}

    def test_an_empty_clearance_table_is_the_resting_state(self, db, music_on):
        reel_id = _reel(db)
        assert audio.attach(reel_id, 26) == {}
        assert db("SELECT audio_track_id FROM pulse_reels WHERE id=?", (reel_id,))[0][
            "audio_track_id"
        ] in (None, 0)

    def test_clearing_makes_it_selectable(self, db, music_on):
        _track(db, 18493)
        assert audio.clear(18493, admin_user_id=1, note="Owner's own recording")[0] is True
        assert [bed["track_id"] for bed in audio.cleared_beds()] == [18493]

    def test_a_track_the_platform_rejects_cannot_be_cleared_at_all(self, db):
        """No point storing a clearance that will never select."""
        _track(db, 18494, commercial_use_allowed=0)
        ok, message = audio.clear(18494, admin_user_id=1)
        assert ok is False
        assert "not approved" in message
        assert db("SELECT * FROM pulsedrop_audio_beds") == []

    def test_a_track_that_does_not_exist_cannot_be_cleared(self, db):
        assert audio.clear(999999, admin_user_id=1)[0] is False

    def test_seller_and_member_uploads_are_not_swept_in_by_the_switch(self, db, music_on):
        """Turning music on with nothing cleared changes nothing at all."""
        for index in range(5):
            _track(db, 18500 + index, title=f"Track {index}")
        reel_id = _reel(db)
        assert audio.attach(reel_id, 26) == {}


class TestTakingItBackReachesWhatWasAlreadyPublished:
    """Withdrawal has to be retroactive, which is why nothing is muxed in."""

    def test_the_mp4_never_carries_the_audio(self):
        from services.pulsedrop import reel_composer

        assert reel_composer.COMPOSED_HAS_AUDIO is False

    def test_both_attachment_rows_say_the_audio_is_not_baked_in(self, db, music_on):
        """``audio_baked_in`` is the arbiter: set, the feed blanks the URL."""
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        reel_id = _reel(db)
        assert audio.attach(reel_id, 26)["track_id"] == 18493
        assert db("SELECT audio_baked_in FROM pulse_reels WHERE id=?", (reel_id,))[0][
            "audio_baked_in"
        ] in (0, None)
        assert db("SELECT audio_baked_in FROM pulse_reel_audio WHERE reel_id=?", (reel_id,))[0][
            "audio_baked_in"
        ] == 0

    def test_a_withdrawn_clearance_stops_selecting(self, db, music_on):
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        assert audio.cleared_beds()
        assert audio.revoke(18493, admin_user_id=1)[0] is True
        assert audio.cleared_beds() == []

    def test_a_withdrawal_keeps_the_record_of_who_cleared_it(self, db):
        """After a rights complaint, "was this cleared, by whom, why" is the question."""
        _track(db, 18493)
        audio.clear(18493, admin_user_id=42, note="Owner's own recording, invoice 2026-114")
        audio.revoke(18493, admin_user_id=1)
        row = db("SELECT * FROM pulsedrop_audio_beds WHERE audio_track_id=18493")[0]
        assert row["cleared_by"] == 42
        assert "invoice 2026-114" in row["clearance_note"]
        assert row["revoked_at"]

    @pytest.mark.parametrize(
        "column,value",
        [
            ("lifecycle_state", "TAKEN_DOWN"),
            ("legal_hold", 1),
            ("active", 0),
            ("approved_by_admin", 0),
            ("commercial_use_allowed", 0),
            ("safety_status", "rejected"),
            ("audio_url", ""),
        ],
    )
    def test_a_takedown_on_the_track_removes_the_bed_without_touching_the_clearance(
        self, db, music_on, column, value
    ):
        """Nobody has to remember that PulseDrop was also using it."""
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        assert audio.cleared_beds()
        db(f"UPDATE pulse_audio_tracks SET {column}=? WHERE id=18493", (value,))
        assert audio.cleared_beds() == []
        # The clearance itself is untouched — this is the track's state, not a
        # withdrawal, and the two read differently on the ops page.
        assert db("SELECT active FROM pulsedrop_audio_beds")[0]["active"] == 1

    def test_the_switch_alone_silences_everything(self, db, monkeypatch):
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        monkeypatch.setenv("PULSEDROP_REEL_AUDIO_ENABLED", "false")
        config.invalidate_cache()
        try:
            assert audio.attach(_reel(db), 26) == {}
        finally:
            config.invalidate_cache()


class TestSelectionIsStableAndSpreads:
    """Two consecutive Reels should not open with the same four bars."""

    @pytest.fixture()
    def four_beds(self, db, music_on):
        for index in range(4):
            track_id = 18500 + index
            _track(db, track_id, title=f"Bed {index}")
            audio.clear(track_id, admin_user_id=1)
        return db

    def test_the_same_product_gets_the_same_bed_twice(self, four_beds):
        """Re-publishing a product must not silently change its music."""
        assert audio.select_bed(26)["track_id"] == audio.select_bed(26)["track_id"]

    def test_different_products_do_not_all_get_the_first_bed(self, four_beds):
        chosen = {audio.select_bed(listing)["track_id"] for listing in range(1, 40)}
        assert len(chosen) > 1

    def test_a_bed_used_recently_is_passed_over(self, four_beds):
        first = audio.select_bed(26)["track_id"]
        assert audio.select_bed(26, recent_track_ids=[first])["track_id"] != first

    def test_one_cleared_bed_keeps_playing_rather_than_falling_silent(self, db, music_on):
        """Clearing exactly one track means music, not music every other Reel."""
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        assert audio.select_bed(26, recent_track_ids=[18493])["track_id"] == 18493

    def test_recency_is_read_from_what_was_actually_published(self, four_beds, db):
        reel_id = _reel(db, reel_id=5, post_id=905)
        db("UPDATE pulse_reels SET audio_track_id=18502 WHERE id=?", (reel_id,))
        db(
            "INSERT INTO pulsedrop_publications (idempotency_key, surface, state, reel_id) "
            "VALUES ('k1', 'reel', 'published', ?)",
            (reel_id,),
        )
        assert audio.recent_bed_track_ids() == [18502]


class TestTheWriteIsCompleteAndRepeatable:
    """Three tables, because the ledger is the thing a dispute is answered from."""

    @pytest.fixture()
    def attached(self, db, music_on):
        _track(db, 18493, title="Shine Tonight")
        audio.clear(18493, admin_user_id=1, note="Owner's own recording")
        reel_id = _reel(db)
        audio.attach(reel_id, 26)
        return db, reel_id

    def test_the_reel_row_names_the_track(self, attached):
        db, reel_id = attached
        row = db("SELECT audio_track_id, sound_title FROM pulse_reels WHERE id=?", (reel_id,))[0]
        assert row["audio_track_id"] == 18493
        assert row["sound_title"] == "Shine Tonight"

    def test_the_feed_join_row_carries_the_volume(self, attached):
        db, reel_id = attached
        row = db("SELECT volume, start_seconds FROM pulse_reel_audio WHERE reel_id=?", (reel_id,))[0]
        assert row["volume"] == pytest.approx(0.6)
        assert row["start_seconds"] == pytest.approx(0.0)

    def test_the_licence_ledger_records_the_grounds_it_was_cleared_on(self, attached):
        db, reel_id = attached
        row = db("SELECT * FROM pulse_content_music WHERE content_type='reel' AND content_id=?",
                 (reel_id,))[0]
        assert row["original_audio_muted"] == 1
        snapshot = json.loads(row["license_snapshot_json"])
        assert snapshot["audio_baked_in"] is False
        assert snapshot["attached_by"] == "pulsedrop"
        assert snapshot["clearance_note"] == "Owner's own recording"

    def test_attaching_twice_does_not_duplicate_the_join_row(self, attached):
        db, reel_id = attached
        audio.attach(reel_id, 26)
        rows = db("SELECT * FROM pulse_reel_audio WHERE reel_id=?", (reel_id,))
        assert len(rows) == 1


class TestMusicNeverCostsAPublication:
    """Every failure here is a silent Reel, never a missing one."""

    def test_a_database_failure_during_attach_is_swallowed(self, db, music_on, monkeypatch):
        from services import db as platform_db

        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        reel_id = _reel(db)

        def explode(*args, **kwargs):
            raise RuntimeError("no connection")

        monkeypatch.setattr(platform_db, "connect", explode)
        assert audio.attach(reel_id, 26) == {}

    def test_a_reel_id_of_zero_is_not_an_error(self, db, music_on):
        assert audio.attach(0, 26) == {}

    def test_the_publisher_calls_attach_after_the_reel_row_exists(self):
        """Ordering is the guarantee: music cannot break a Reel it follows.

        Read from the AST rather than the source text. A literal match on
        the exact indentation of the ``_settle`` call passes for one layout
        and fails the next time someone reflows it, which would read as "the
        publisher stopped attaching music" when the behaviour never changed.
        """
        import ast
        import inspect
        import textwrap

        from services.pulsedrop import publisher

        tree = ast.parse(textwrap.dedent(inspect.getsource(publisher.publish_reel)))

        def named(node, name):
            func = node.func
            if isinstance(func, ast.Attribute):
                return func.attr == name
            return isinstance(func, ast.Name) and func.id == name

        def line_of(predicate, what):
            hits = [n.lineno for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and predicate(n)]
            assert len(hits) == 1, f"expected exactly one {what} call, found {len(hits)}"
            return hits[0]

        attach_reel = line_of(lambda n: named(n, "_attach_reel"), "_attach_reel")
        attach_bed = line_of(
            lambda n: isinstance(n.func, ast.Attribute)
            and n.func.attr == "attach"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "audio",
            "audio.attach",
        )
        published = line_of(
            lambda n: named(n, "_settle")
            and any(isinstance(a, ast.Name) and a.id == "PUBLISHED" for a in n.args),
            "_settle(..., PUBLISHED)",
        )

        # The Reel row exists before a bed is chosen for it...
        assert attach_reel < attach_bed
        # ...and the bed is attached before the publication settles.
        assert attach_bed < published

        # Its result is never branched on. A bed that fails to attach must not
        # be able to divert the publication away from PUBLISHED.
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.Assert, ast.While)):
                if "attr='attach'" in ast.dump(node.test):
                    raise AssertionError(
                        "publish_reel branches on the result of audio.attach; "
                        "music must never be able to fail a Reel"
                    )


class TestTheOpsPageCanExplainSilence:
    """"Nothing is cleared" and "all four were taken down" are different incidents."""

    def test_a_blocked_clearance_stays_visible_with_its_reason(self, db, music_on):
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        db("UPDATE pulse_audio_tracks SET lifecycle_state='TAKEN_DOWN' WHERE id=18493")
        view = audio.bed_view()
        assert len(view) == 1
        assert view[0]["selectable"] is False
        assert view[0]["state"] == "taken down"

    def test_a_withdrawn_clearance_reads_as_withdrawn(self, db, music_on):
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        audio.revoke(18493, admin_user_id=1)
        assert audio.bed_view()[0]["state"] == "withdrawn"

    def test_a_selectable_bed_says_so(self, db, music_on):
        _track(db, 18493)
        audio.clear(18493, admin_user_id=1)
        assert audio.bed_view()[0]["state"] == "in rotation"

    def test_candidates_exclude_what_is_already_cleared(self, db, music_on):
        _track(db, 18493)
        _track(db, 18494, title="Another")
        audio.clear(18493, admin_user_id=1)
        assert [c["track_id"] for c in audio.candidates()] == [18494]

    def test_candidates_show_the_uploader_proof_for_what_it_is(self, db, music_on):
        """An operator clearing a track should see the checkbox they are overriding."""
        _track(db, 18493)
        assert audio.candidates()[0]["proof_url"].startswith("artist-upload:")
