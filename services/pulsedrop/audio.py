"""Music under a composed Reel — chosen here, played by the client, never baked.

## The decision this module encodes

A composed Reel is eight seconds of a product photograph with a slow push-in.
Silent, that is a competent product shot. With a bed under it, it is a Reel. So
the question was never whether music helps; it was where the music lives.

**It is not in the pixels.** :data:`reel_composer.COMPOSED_HAS_AUDIO` stays
False and the MP4 keeps its ``-an``. The platform already has a playback-time
overlay: a Reel carries ``audio_track_id``, the feed serialises the track's URL
into ``audio.attached_audio_url`` and the clients play it against the video's
own clock — ``ReelPlayerCard`` loads it as a separate ``Audio.Sound`` and mutes
the video's intrinsic track, and the web renderer does the equivalent. Riding
that instead of the encoder buys three things a muxed file cannot:

*A takedown is instant and total.* ``attached_audio_url`` is computed per
request from the live track row. A track that goes ``TAKEN_DOWN`` stops playing
everywhere on the next request, including under Reels published months ago. The
same event against a baked file means re-encoding every Reel that used it, or
leaving infringing audio in objects already on the CDN.

*A track swap costs nothing.* The fingerprint that keys a render covers the
images, the geometry and the length — deliberately not the music, because the
music is not in the render. Changing the bed changes one row.

*The two can never stack.* The payload blanks ``attached_audio_url`` whenever
``audio_baked_in`` is set, so the flag is the arbiter and this module writes it
0. Burning audio in *and* attaching a track is the one way to get two songs at
once, and the schema makes it unrepresentable rather than merely discouraged.

## Why a bed has to be cleared by hand

There are 148 tracks in ``pulse_audio_tracks``. 142 pass ``music_service``'s
commercial filter, all of them carry ``commercial_use_allowed=1`` and
``approved_by_admin=1``, and every single one of them is backed by
``proof_url='artist-upload:15:<timestamp>'`` with ``proof_file`` empty — a
breadcrumb saying the uploader ticked "I confirm that I own this music or have
the legal right to upload it". There is no receipt behind any of them, and the
database cannot tell the owner's own recordings from a track somebody uploaded
with the box ticked.

For a member's Reel that is the right posture: the uploader made the claim and
carries it. PulseDrop is not a member. It posts under a verified badge, on the
platform's own account, over listings the platform earns on — synchronisation
use in commercial content, which is the licence nobody here has been granted in
writing. So the platform's own filter is treated as necessary and not
sufficient, and :func:`cleared_beds` intersects it with an explicit human act
recorded in ``pulsedrop_audio_beds``.

The resting state is an empty table, which means silence, which is correct: a
deployment that has never thought about music should not have PulseDrop
publishing it.

## What happens when this fails

Nothing that matters. :func:`attach` returns a reason and the Reel publishes
silent. Music is a garnish on a post whose job is to show a product; a bed that
could not be selected is not a reason to abandon a publication that already has
a rendered video, a claimed idempotency key and a live post row.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from . import config, schema

log = logging.getLogger(__name__)

#: How many recent Reels to look back over when avoiding a repeat. Small on
#: purpose: the point is that two consecutive Reels do not open with the same
#: four bars, not that a bed is retired after one use. With a handful of cleared
#: beds a longer window would exhaust the set and force a repeat anyway, only
#: less predictably.
RECENT_BED_WINDOW = 3

#: Where a bed starts. Zero, and that is a decision: a cleared bed is a short
#: loop chosen to work from its first beat, and seeking into one to find a
#: "better" entry point is an edit the platform has no remix right to make on
#: anybody's behalf. ``remix_edit_allowed`` being true in the catalogue does not
#: change that, because it is the same self-attested claim as everything else.
BED_START_SECONDS = 0.0


# ---------------------------------------------------------------------------
# The cleared set
# ---------------------------------------------------------------------------


def cleared_beds() -> list[dict]:
    """Tracks a human cleared *and* the platform's own filter still accepts.

    Both halves are re-evaluated on every call rather than cached at clearance
    time. The clearance is a statement about rights, which does not go stale on
    its own; the track's state is a statement about the file, which does — it
    can be taken down, deactivated, put under legal hold or have its admin
    approval pulled, and each of those has to remove the bed without an operator
    remembering that PulseDrop was also using it.
    """
    rows = _rows(
        """
        SELECT b.audio_track_id AS track_id, b.clearance_note, b.cleared_at,
               t.title, t.artist, t.audio_url, t.duration_seconds,
               t.attribution_required
        FROM pulsedrop_audio_beds b
        JOIN pulse_audio_tracks t ON t.id = b.audio_track_id
        WHERE COALESCE(b.active, 0) = 1
          AND COALESCE(t.lifecycle_state, 'ACTIVE') = 'ACTIVE'
          AND COALESCE(t.removed_at, '') = ''
          AND COALESCE(t.legal_hold, 0) = 0
          AND COALESCE(t.active, 1) = 1
          AND COALESCE(t.approved_by_admin, 0) = 1
          AND COALESCE(t.commercial_use_allowed, 0) = 1
          AND COALESCE(t.safety_status, 'approved') = 'approved'
          AND COALESCE(t.audio_url, '') <> ''
        ORDER BY b.audio_track_id
        """
    )
    return [row for row in rows if _music_service_agrees(row.get("track_id"))]


def _music_service_agrees(track_id) -> bool:
    """Second opinion from the filter every other surface attaches through.

    The SQL above and ``music_service._safe_track`` overlap heavily and are not
    identical, and where they disagree the answer has to be no. Consulting both
    also means a future tightening of the platform's rules reaches PulseDrop
    without anybody editing this query.
    """
    try:
        from services import music_service

        return bool(music_service.attach_music_payload(str(track_id or "")).get("is_creator_safe"))
    except Exception:
        log.debug("pulsedrop_bed_safety_probe_failed track_id=%s", track_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_bed(listing_id: int, *, beds: list[dict] | None = None,
               recent_track_ids: list[int] | None = None) -> dict:
    """Pick one bed for a listing, or ``{}`` when there is nothing to pick.

    Deterministic in the listing id, so re-publishing the same product does not
    quietly give it different music, and so a test can assert an outcome rather
    than a distribution. Not random: with a small cleared set, randomness
    repeats more visibly than a hash does, and it makes the failure "why did
    this Reel get that track" unanswerable.

    The recency exclusion is applied *after* the hash and only if it leaves
    something — a set of one bed keeps publishing that bed rather than falling
    silent, because the alternative reading is that clearing exactly one track
    means music every other Reel, which nobody would intend.
    """
    pool = list(beds if beds is not None else cleared_beds())
    if not pool:
        return {}
    recent = {int(value) for value in (recent_track_ids or []) if value}
    fresh = [bed for bed in pool if int(bed.get("track_id") or 0) not in recent] or pool
    digest = hashlib.sha1(f"pulsedrop-bed:{int(listing_id or 0)}".encode()).hexdigest()
    return dict(fresh[int(digest[:8], 16) % len(fresh)])


def recent_bed_track_ids(limit: int = RECENT_BED_WINDOW) -> list[int]:
    """Track ids under PulseDrop's last few Reels, newest first."""
    rows = _rows(
        """
        SELECT r.audio_track_id AS track_id
        FROM pulsedrop_publications p
        JOIN pulse_reels r ON r.id = p.reel_id
        WHERE p.surface = 'reel'
          AND p.state = 'published'
          AND COALESCE(r.audio_track_id, 0) > 0
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    return [int(row.get("track_id") or 0) for row in rows if row.get("track_id")]


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


def attach(reel_id: int, listing_id: int, *, now: datetime | None = None) -> dict:
    """Put a bed under a published Reel. Returns the bed, or ``{}``.

    Called after the Reel row exists and never before: a Reel with music and no
    video is not a thing, and ordering it this way means the failure mode of
    everything in this module is a silent Reel rather than a missing one.
    """
    if not config.reel_audio_enabled():
        return {}
    reel_id = int(reel_id or 0)
    if not reel_id:
        return {}
    try:
        schema.ensure_schema()
        bed = select_bed(listing_id, recent_track_ids=recent_bed_track_ids())
        if not bed:
            return {}
        _write(reel_id, bed, now=now)
        log.info(
            "pulsedrop_bed_attached reel_id=%s listing_id=%s track_id=%s title=%s",
            reel_id, listing_id, bed.get("track_id"), bed.get("title"),
        )
        return bed
    except Exception:
        log.exception("pulsedrop_bed_attach_failed reel_id=%s listing_id=%s", reel_id, listing_id)
        return {}


def _write(reel_id: int, bed: dict, *, now: datetime | None = None) -> None:
    """The three writes, in one transaction.

    ``pulse_reels`` is what the Reels surface reads, ``pulse_reel_audio`` is what
    the feed payload joins for the start offset and volume, and
    ``pulse_content_music`` is the licence snapshot the rest of the platform
    keeps. Writing the first two and not the third would give PulseDrop's Reels
    music that the music ledger has no record of, which is precisely the
    condition a rights dispute needs to be unanswerable.
    """
    from services import db as db_service

    track_id = int(bed.get("track_id") or 0)
    stamp = (now or datetime.utcnow()).isoformat(sep=" ", timespec="seconds")
    volume = config.reel_audio_volume()
    end = float(bed.get("duration_seconds") or 0) or float(config.reel_target_seconds())
    snapshot = json.dumps(
        {
            "track_id": track_id,
            "title": bed.get("title") or "",
            "artist": bed.get("artist") or "",
            "audio_baked_in": False,
            "original_audio_muted": True,
            "audio_start_time": BED_START_SECONDS,
            "audio_volume": volume,
            "attached_by": "pulsedrop",
            "clearance_note": bed.get("clearance_note") or "",
            "cleared_at": bed.get("cleared_at") or "",
        },
        default=str,
    )[:4000]

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulse_reels
            SET audio_track_id=?, sound_title=?, sound_start_seconds=?,
                sound_end_seconds=?, audio_baked_in=0
            WHERE id=?
            """,
            (track_id, str(bed.get("title") or "")[:180], BED_START_SECONDS, end, reel_id),
        )
        cur.execute(
            """
            INSERT INTO pulse_reel_audio
                (reel_id, audio_track_id, start_seconds, end_seconds, volume,
                 created_at, audio_baked_in)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(reel_id, audio_track_id) DO UPDATE SET
                start_seconds=excluded.start_seconds,
                end_seconds=excluded.end_seconds,
                volume=excluded.volume,
                audio_baked_in=0
            """,
            (reel_id, track_id, BED_START_SECONDS, end, volume, stamp),
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_content_music
                (content_type, content_id, audio_track_id, title, artist, source,
                 license_snapshot_json, attached_by_user_id, created_at,
                 original_audio_muted, audio_start_time, audio_volume)
            VALUES ('reel', ?, ?, ?, ?, 'pulsedrop_cleared_bed', ?, ?, ?, 1, ?, ?)
            """,
            (
                reel_id,
                str(track_id),
                str(bed.get("title") or "")[:180],
                str(bed.get("artist") or "")[:180],
                snapshot,
                _account_user_id(),
                stamp,
                BED_START_SECONDS,
                volume,
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _account_user_id() -> int:
    from . import account

    try:
        return int(account.account_user_id() or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Operator actions, called by the ops page
# ---------------------------------------------------------------------------


def clear(track_id: int, *, admin_user_id: int = 0, note: str = "",
          now: datetime | None = None) -> tuple[bool, str]:
    """Record that a human has cleared one track for PulseDrop's use.

    Refuses a track the platform's own filter already rejects, so the clearance
    table cannot accumulate rows that will never select. It deliberately does
    *not* refuse on the grounds that the proof is a checkbox — that is the whole
    judgement being delegated to the person clicking, and a gate that second
    guesses it would make the feature unreachable.
    """
    track_id = int(track_id or 0)
    if not track_id:
        return False, "Choose a track."
    if not _music_service_agrees(track_id):
        return False, f"Track {track_id} is not approved for PulseSoc use."
    stamp = (now or datetime.utcnow()).isoformat(sep=" ", timespec="seconds")
    _execute(
        """
        INSERT INTO pulsedrop_audio_beds
            (audio_track_id, active, cleared_by, clearance_note, cleared_at,
             revoked_at, created_at, updated_at)
        VALUES (?, 1, ?, ?, ?, '', ?, ?)
        ON CONFLICT(audio_track_id) DO UPDATE SET
            active=1, cleared_by=excluded.cleared_by,
            clearance_note=excluded.clearance_note,
            cleared_at=excluded.cleared_at, revoked_at='',
            updated_at=excluded.updated_at
        """,
        (track_id, int(admin_user_id or 0), str(note or "")[:500], stamp, stamp, stamp),
    )
    log.info("pulsedrop_bed_cleared track_id=%s admin=%s", track_id, admin_user_id)
    return True, f"Track {track_id} cleared for PulseDrop Reels."


#: A bulk clearance must say what it is founded on. One track cleared with an
#: empty note is a thin record; 142 cleared with an empty note is no record at
#: all, and it is the case where somebody will later need to reconstruct what
#: was decided and on whose say-so.
BULK_NOTE_MIN = 12


def candidate_count() -> int:
    """How many tracks are clearable right now.

    The page lists twelve of them, which is the right number to look at and the
    wrong number to make a decision on: "Clear all" over a list showing 12 rows
    needs to say 142 next to it or the operator does not know what they pressed.
    """
    rows = _rows("SELECT COUNT(*) AS n" + _CANDIDATE_FROM)
    return int(rows[0].get("n") or 0) if rows else 0


def clear_all(*, admin_user_id: int = 0, note: str = "",
              now: datetime | None = None) -> tuple[bool, str]:
    """Clear every track the platform already considers usable.

    Exists because the catalogue is 142 tracks and the alternative to this is a
    one-off script, which is worse in every way that matters: it runs from
    somebody's laptop, against whatever database their environment points at,
    with no audit row and no note. This is the same
    :func:`clear` in a loop, so nothing here can clear a track the single-track
    path would refuse — it does not widen the gate, it just stops the operator
    pressing the same button 142 times.

    Requires a note. Clearing is a rights decision and the note is the only
    place the grounds are recorded; a bulk one with no grounds is the exact
    artefact that makes a future takedown request unanswerable.
    """
    note = str(note or "").strip()
    if len(note) < BULK_NOTE_MIN:
        return False, (
            "Write down where the rights come from before clearing in bulk — "
            "it is the only record of why these tracks are usable."
        )
    pending = candidates(limit=10_000)
    if not pending:
        # Not necessarily "all cleared": a withdrawn track is also absent from
        # the candidate list, and saying "already cleared" next to a row the
        # page is showing as withdrawn would read as a bug.
        return False, "Nothing left to clear — every usable track is already cleared or withdrawn."
    cleared = 0
    for row in pending:
        ok, _ = clear(int(row.get("track_id") or 0), admin_user_id=admin_user_id,
                      note=note, now=now)
        cleared += 1 if ok else 0
    refused = len(pending) - cleared
    log.info("pulsedrop_beds_cleared_bulk cleared=%s refused=%s admin=%s",
             cleared, refused, admin_user_id)
    if not cleared:
        return False, "No track could be cleared; all were refused by the platform filter."
    tail = f" {refused} were refused by the platform filter." if refused else ""
    return True, f"Cleared {cleared} tracks for PulseDrop Reels.{tail}"


def revoke(track_id: int, *, admin_user_id: int = 0,
           now: datetime | None = None) -> tuple[bool, str]:
    """Withdraw a clearance.

    Deactivates rather than deletes. The question after a rights complaint is
    "was this ever cleared, by whom, and on what grounds" — a deleted row
    answers none of it, and the row is four columns.
    """
    track_id = int(track_id or 0)
    if not track_id:
        return False, "Choose a track."
    stamp = (now or datetime.utcnow()).isoformat(sep=" ", timespec="seconds")
    _execute(
        "UPDATE pulsedrop_audio_beds SET active=0, revoked_at=?, updated_at=? WHERE audio_track_id=?",
        (stamp, stamp, track_id),
    )
    log.info("pulsedrop_bed_revoked track_id=%s admin=%s", track_id, admin_user_id)
    return True, f"Track {track_id} withdrawn from PulseDrop Reels."


def bed_view() -> list[dict]:
    """Every clearance ever granted, with why it is or is not selectable now.

    Revoked and blocked rows stay visible. An operator asking "why is there no
    music" is best served by seeing that four tracks were cleared and all four
    were taken down, rather than by an empty list that reads the same as never
    having cleared anything.
    """
    rows = _rows(
        """
        SELECT b.audio_track_id AS track_id, b.active, b.clearance_note,
               b.cleared_by, b.cleared_at, b.revoked_at,
               t.title, t.artist, t.lifecycle_state, t.legal_hold,
               t.active AS track_active, t.approved_by_admin,
               t.commercial_use_allowed, t.audio_url
        FROM pulsedrop_audio_beds b
        LEFT JOIN pulse_audio_tracks t ON t.id = b.audio_track_id
        ORDER BY b.active DESC, b.audio_track_id
        """
    )
    selectable = {int(bed.get("track_id") or 0) for bed in cleared_beds()}
    for row in rows:
        row["selectable"] = int(row.get("track_id") or 0) in selectable
        row["state"] = _bed_state(row)
    return rows


#: Which tracks are clearable at all. Held in one string because two callers
#: need it and they must not disagree: the picker shows a page of them and
#: :func:`candidate_count` puts a number beside "Clear all". A second copy of
#: this clause is precisely the bug where the button offers 142 and clears 138.
_CANDIDATE_FROM = """
        FROM pulse_audio_tracks t
        LEFT JOIN pulsedrop_audio_beds b ON b.audio_track_id = t.id
        WHERE b.id IS NULL
          AND COALESCE(t.lifecycle_state, 'ACTIVE') = 'ACTIVE'
          AND COALESCE(t.removed_at, '') = ''
          AND COALESCE(t.legal_hold, 0) = 0
          AND COALESCE(t.active, 1) = 1
          AND COALESCE(t.approved_by_admin, 0) = 1
          AND COALESCE(t.commercial_use_allowed, 0) = 1
          AND COALESCE(t.audio_url, '') <> ''
"""


def candidates(limit: int = 40) -> list[dict]:
    """Tracks an operator could clear, most-used first.

    Ordered by how much the catalogue itself has leaned on a track, which is the
    only signal available here and a weak one — but it puts the recognisable
    beds at the top of a list of 142, which is the difference between a usable
    picker and a track-id field. The rights columns come along so the operator
    is clearing something they can see, not an id.
    """
    return _rows(
        """
        SELECT t.id AS track_id, t.title, t.artist, t.duration_seconds,
               t.license_type, t.proof_url, t.rights_statement,
               t.uploader_user_id, t.reel_use_count, t.usage_count
        """
        + _CANDIDATE_FROM
        + """
        ORDER BY COALESCE(t.reel_use_count, 0) DESC, COALESCE(t.usage_count, 0) DESC, t.id
        LIMIT ?
        """,
        (int(limit),),
    )


def _bed_state(row: dict) -> str:
    if row.get("selectable"):
        return "in rotation"
    if not row.get("active"):
        return "withdrawn"
    if not row.get("title") and not row.get("audio_url"):
        return "track missing"
    if str(row.get("lifecycle_state") or "ACTIVE") != "ACTIVE":
        return str(row.get("lifecycle_state") or "").lower().replace("_", " ")
    if row.get("legal_hold"):
        return "legal hold"
    return "blocked by track rules"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    """Read, degrading to nothing.

    Same contract as ``ops._rows``: a deployment that has never run PulseDrop
    has no ``pulsedrop_audio_beds``, and the caller for all of these is either a
    page that must still render or a publication that must still go out.
    """
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    except Exception:
        log.debug("pulsedrop_audio_read_failed", exc_info=True)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _execute(sql: str, params: tuple = ()) -> None:
    from services import db as db_service

    schema.ensure_schema()
    conn = db_service.connect()
    try:
        conn.cursor().execute(sql, params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
