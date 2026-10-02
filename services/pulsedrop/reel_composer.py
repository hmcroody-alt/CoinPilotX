"""Turning a product into nine-by-sixteen video — and deliberately nothing else.

## The one decision that shapes this whole module: no text in the pixels

The brief asks the composition for a title animation, a price reveal, a badge
and a CTA reveal. It also states, as a critical architectural requirement, that
the video must be separable from live product data so that price, stock, seller
and availability can change without re-rendering. Those two are the same
sentence read twice, and only one of them can be built.

Text burned into a video file is frozen at encode time. A Reel lives for months;
the seller re-prices on Tuesday. A rendered "$89.00" then becomes a number
PulseSoc is publishing, under a verified badge, that disagrees with its own
checkout — and the only repair is to re-encode and republish, which rewrites
members' history. The same argument retires the CTA (its destination depends on
whether the product is still purchasable) and the editorial badge (TRENDING is a
claim about this week).

There is a second argument, independent and just as final: burned-in text is
English. This codebase gates hardcoded strings in CI across eleven catalogs
precisely so no member is shown a language they did not choose, and a pixel is
the one place that gate cannot reach.

So the file this module produces is **product imagery in motion, and nothing
else**. Every element the brief asks for is rendered by the client, live, over
the video — see ``CommerceOverlay`` and the hydration layer. The motion, the
framing and the pacing are what make it feel composed; the words are what make
it correct, and they arrive separately. That is not a reduction of the brief, it
is the only way to satisfy both halves of it.

## Why the background is blurred and not cropped

A product photograph is usually square or 4:3 and shot centred. Cropping it to
9:16 removes half the product; stretching it is worse. So a source whose aspect
ratio is far from vertical is *fitted* — scaled to fit inside the frame — over a
blurred, darkened, zoomed copy of itself. Nothing of the product is lost, the
frame is full, and the backdrop reads as intentional because it is made of the
product's own colours. Sources that are already near-vertical are filled
normally, because for those the crop costs nothing.

## Why renders are keyed by their inputs

``pulsedrop_renders`` is addressed by ``(listing_id, composition_version,
source_fingerprint)``. A retry of the same product with the same photographs and
the same composition finds the finished file instead of spending ninety seconds
of CPU re-encoding it. A seller who replaces their photographs changes the
fingerprint and gets a new render; bumping :data:`COMPOSITION_VERSION` re-renders
everything, which is what makes the composition safe to change.

## Why rendering does not run under the curator's lease

An encode is minutes. The lease is five. A curator that rendered inline would
routinely outlive its own lease and hand a second instance the same tick — the
one failure the lease exists to prevent. So the curator enqueues and returns, and
:func:`run_pending` drains the queue from the media worker with its own per-row
claim.

## Why a finished encode is not yet a finished render

PulseSoc does not serve video from the bucket CDN. Every reel belonging to a
member plays from ``stream.mux.com/<playback_id>.m3u8``, and PulseDrop was the
one producer writing a ``cdn.`` URL into ``pulse_reels.video_url`` -- which
Cloudflare answers with a bot challenge for an ``.mp4`` while returning 200 for
the poster ``.jpg`` beside it. The visible result was thirty-four reels that
showed a still frame and never played.

So the encode is only the first half. The file is uploaded to the bucket, which
is now purely a durable origin for Mux to ingest from, handed to Mux over a
*presigned* URL (the CDN would challenge Mux's fetcher exactly as it challenged
curl), and the render parks in :data:`TRANSCODING` until Mux reports the asset
ready. :func:`promote_transcoding` is the second half. Every reader of a render
already gates on ``state == READY and video_url``, so the waiting state needs no
cooperation from the publisher or the curator -- it simply is not ready yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from services.pulsedrop import config, distribution, eligibility, schema

log = logging.getLogger(__name__)

#: Bump to invalidate every existing render. The value is part of the render
#: key, so a bump does not delete anything — it makes the old rows unreachable
#: and lets the next tick build replacements, which means a bad composition can
#: be rolled back by putting the old value back.
COMPOSITION_VERSION = "pd-reel-v1"

#: 1080x1920 is what the Reels pipeline already uses everywhere else. Matching
#: it means PulseDrop's Reels decode on the same path as a member's, rather than
#: becoming the one source that needs a special case in the player.
FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920
FRAME_RATE = 30

#: Above this the source is close enough to vertical that filling the frame
#: costs nothing. 0.62 is 9:16 plus a little tolerance — a 2:3 portrait photo
#: fills, a square one does not.
_FILL_MIN_RATIO = 0.62

#: Encoder settings, chosen to match the bitrate range the rest of the pipeline
#: produces rather than to maximise quality: a PulseDrop Reel that is three
#: times the size of a member's Reel is a CDN bill, not a better video.
_VIDEO_BITRATE = "2600k"
_VIDEO_MAXRATE = "3200k"
_VIDEO_BUFSIZE = "5200k"

PENDING = "pending"
RENDERING = "rendering"
#: Encoded and uploaded, handed to Mux, waiting for Mux to finish ingesting.
#:
#: A distinct state rather than a flag on ``pending`` because the two want
#: opposite things from the next sweep: a pending render needs ffmpeg to run, a
#: transcoding one must *not* run ffmpeg again -- the file is already made and
#: re-encoding it would create a second Mux asset for the same bytes every
#: sweep. :func:`find_or_enqueue` returns this row unchanged and its callers
#: already treat anything that is not READY as "come back next tick", so no
#: publisher needs to learn about it.
TRANSCODING = "transcoding"
READY = "ready"
FAILED = "failed"

#: The composed file carries no audio stream, and should not be made to.
#:
#: This is not "PulseDrop has no music" — :mod:`services.pulsedrop.audio`
#: attaches a cleared track to the published Reel. It is a statement about
#: *where* the music lives. The platform resolves ``audio.attached_audio_url``
#: per request from the live track row and the clients play it against the
#: video's clock, so a takedown silences every Reel ever published the moment it
#: lands. Muxing the same track into the file would mean re-encoding the back
#: catalogue to honour that takedown, and leaving the audio on the CDN until the
#: re-encode finished.
#:
#: The feed payload also blanks ``attached_audio_url`` whenever
#: ``audio_baked_in`` is set, so the two are exclusive by construction: flipping
#: this to True would not produce two tracks, it would silently disable the one
#: that can be withdrawn. A seller's own video keeps whatever audio it came
#: with, which is a separate question and not this constant's.
COMPOSED_HAS_AUDIO = False


# ---------------------------------------------------------------------------
# Keying
# ---------------------------------------------------------------------------


def source_fingerprint(listing: dict, source_kind: str) -> str:
    """A stable digest of everything that would change the output.

    Includes the target duration and the frame geometry, not just the URLs: a
    deployment that shortens Reels must re-render, and a fingerprint that
    ignored the setting would hand it the old length forever.
    """
    if source_kind == distribution.SOURCE_SELLER_VIDEO:
        parts = [eligibility.video_source(listing)]
    else:
        parts = eligibility.image_urls(listing)[: config.reel_max_images()]
    payload = json.dumps(
        {
            "kind": source_kind,
            "parts": parts,
            "seconds": config.reel_target_seconds(),
            "frame": [FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def find_or_enqueue(listing: dict, source_kind: str, *, now: datetime | None = None) -> dict:
    """The render row for this product, creating a pending one if needed.

    Returns the row as a dict. A caller wanting to publish checks
    ``state == READY`` and a non-empty ``video_url``; anything else means come
    back next tick. This never blocks and never encodes.
    """
    schema.ensure_schema()
    from services import db as db_service

    listing_id = int(listing.get("id") or 0)
    fingerprint = source_fingerprint(listing, source_kind)
    stamp = (now or datetime.utcnow()).isoformat(timespec="seconds")
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM pulsedrop_renders
            WHERE listing_id=? AND composition_version=? AND source_fingerprint=?
            ORDER BY id DESC LIMIT 1
            """,
            (listing_id, COMPOSITION_VERSION, fingerprint),
        )
        existing = dict(cur.fetchone() or {})
        if existing:
            return existing
        cur.execute(
            """
            INSERT INTO pulsedrop_renders
                (listing_id, composition_version, source_kind, source_fingerprint,
                 state, max_attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                COMPOSITION_VERSION,
                source_kind,
                fingerprint,
                PENDING,
                config.reel_render_max_attempts(),
                stamp,
                stamp,
            ),
        )
        conn.commit()
        cur.execute(
            """
            SELECT * FROM pulsedrop_renders
            WHERE listing_id=? AND composition_version=? AND source_fingerprint=?
            ORDER BY id DESC LIMIT 1
            """,
            (listing_id, COMPOSITION_VERSION, fingerprint),
        )
        return dict(cur.fetchone() or {})
    except Exception:
        log.warning("pulsedrop_render_enqueue_failed listing_id=%s", listing_id, exc_info=True)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _claim_render(owner: str, *, now: datetime | None = None) -> dict:
    """Take one pending render. ``{}`` when there is nothing to do.

    The claim is a conditional UPDATE on ``state``, so two workers cannot take
    the same row: the loser updates zero rows and moves on. A row stuck in
    ``rendering`` past two timeouts is reclaimed, because the process that had
    it is gone and the alternative is a queue that drains to zero and stops.
    """
    from services import db as db_service

    moment = now or datetime.utcnow()
    stamp = moment.isoformat(timespec="seconds")
    stale = (moment - timedelta(seconds=config.reel_render_timeout_seconds() * 2)).isoformat(
        timespec="seconds"
    )
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM pulsedrop_renders
            WHERE (state=? AND attempts < max_attempts)
               OR (state=? AND COALESCE(claimed_at,'') < ?)
            ORDER BY id ASC LIMIT 1
            """,
            (PENDING, RENDERING, stale),
        )
        row = dict(cur.fetchone() or {})
        render_id = int(row.get("id") or 0)
        if not render_id:
            return {}
        cur.execute(
            """
            UPDATE pulsedrop_renders
            SET state=?, claimed_by=?, claimed_at=?, attempts=attempts+1, updated_at=?
            WHERE id=? AND state<>?
            """,
            (RENDERING, owner, stamp, stamp, render_id, READY),
        )
        won = int(cur.rowcount or 0) > 0
        conn.commit()
        if not won:
            return {}
        cur.execute("SELECT * FROM pulsedrop_renders WHERE id=? LIMIT 1", (render_id,))
        claimed = dict(cur.fetchone() or {})
        return claimed if str(claimed.get("claimed_by") or "") == owner else {}
    except Exception:
        log.warning("pulsedrop_render_claim_failed", exc_info=True)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _settle_render(render_id: int, state: str, *, video_url: str = "", poster_url: str = "",
                   duration: float = 0.0, reason: str = "", mux_asset_id: str = "",
                   mux_playback_id: str = "", now: datetime | None = None) -> None:
    """Write a render's terminal (or waiting) state. Never raises.

    ``frame_width``/``frame_height`` are stamped for TRANSCODING as well as for
    READY. The encode is finished by the time either state is written -- the
    frame really is 1080x1920 -- and zeroing them while Mux ingests would make
    the admin render list show a dimensionless row for the one minute a render
    is most likely to be looked at.
    """
    from services import db as db_service

    stamp = (now or datetime.utcnow()).isoformat(timespec="seconds")
    encoded = state in (READY, TRANSCODING)
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulsedrop_renders
            SET state=?, video_url=?, poster_url=?, duration_seconds=?,
                frame_width=?, frame_height=?, failure_reason=?, claimed_by='',
                mux_asset_id=?, mux_playback_id=?, updated_at=?
            WHERE id=?
            """,
            (
                state,
                video_url,
                poster_url,
                float(duration or 0),
                FRAME_WIDTH if encoded else 0,
                FRAME_HEIGHT if encoded else 0,
                str(reason or "")[:300],
                str(mux_asset_id or ""),
                str(mux_playback_id or ""),
                stamp,
                int(render_id),
            ),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_render_settle_failed id=%s", render_id, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_pending(limit: int = 1, *, now: datetime | None = None) -> dict:
    """Drain up to ``limit`` renders. The media worker's entry point.

    Returns the counters the run log wants. One at a time by default: an encode
    saturates a core, and a worker that took four would starve the cover
    generation sharing the same container.

    Polling Mux happens first and unconditionally. It is an HTTP GET, not an
    encode, so it is not what the ``limit`` is rationing; and running it before
    the claim means a render whose asset went ready a minute ago is published on
    this tick rather than waiting behind an encode that may not even be due.
    It also runs when ``_ffmpeg()`` is missing -- an already-finished encode
    needs no encoder, and returning early would strand it.
    """
    from services.pulsedrop import lease

    counters = {"started": 0, "succeeded": 0, "failed": 0}
    if not config.reels_enabled():
        return counters
    # Flat ``mux_*`` keys rather than a nested dict: the curator's _record reads
    # scalar counters and logs the rest, so nesting would bury the ingest outcome
    # at exactly the moment an operator is asking why a reel has not appeared.
    counters.update({f"mux_{k}": v for k, v in promote_transcoding(now=now).items()})
    if not _ffmpeg():
        return counters
    owner = lease.owner_id()
    for _ in range(max(1, int(limit))):
        row = _claim_render(owner, now=now)
        if not row:
            break
        counters["started"] += 1
        ok, reason = _execute_render(row, now=now)
        counters["succeeded" if ok else "failed"] += 1
        if not ok:
            log.warning(
                "pulsedrop_render_failed id=%s listing_id=%s reason=%s",
                row.get("id"), row.get("listing_id"), reason,
            )
    return counters


def _execute_render(row: dict, *, now: datetime | None = None) -> tuple[bool, str]:
    """Fetch the sources, encode, publish, settle. Never raises."""
    render_id = int(row.get("id") or 0)
    listing_id = int(row.get("listing_id") or 0)
    attempts = int(row.get("attempts") or 0)
    max_attempts = int(row.get("max_attempts") or config.reel_render_max_attempts())

    def fail(reason: str) -> tuple[bool, str]:
        # Exhausted attempts go to FAILED and stay there; earlier ones go back to
        # PENDING so the next sweep retries. Without that distinction a transient
        # download failure would permanently retire a product.
        terminal = attempts >= max_attempts
        _settle_render(render_id, FAILED if terminal else PENDING, reason=reason, now=now)
        return False, reason

    listing = _load_listing(listing_id)
    if not listing:
        return fail("listing_unavailable")

    kind = str(row.get("source_kind") or "")
    try:
        with tempfile.TemporaryDirectory(prefix="pulsedrop-reel-") as workspace:
            work = Path(workspace)
            if kind == distribution.SOURCE_SELLER_VIDEO:
                produced = _render_from_video(listing, work)
            else:
                produced = _render_from_images(listing, work)
            if not produced:
                return fail(f"{kind}_composition_failed")
            video_path, poster_path, duration = produced
            key_base = f"pulsedrop/reels/{listing_id}/{row.get('source_fingerprint')}"
            video_url = _publish(video_path, f"{key_base}.mp4", "video/mp4")
            if not video_url:
                return fail("video_upload_failed")
            poster_url = _publish(poster_path, f"{key_base}.jpg", "image/jpeg") if poster_path else ""
            asset_id, playback_id = _to_mux(f"{key_base}.mp4", listing_id=listing_id)
    except Exception:
        log.exception("pulsedrop_render_raised id=%s", render_id)
        return fail("render_exception")

    if asset_id and playback_id:
        # Not READY yet: the playback URL is only valid once Mux reports the
        # asset ready, and a reel row written before then is a reel that 404s
        # for the first seconds of its life. promote_transcoding finishes it.
        _settle_render(
            render_id, TRANSCODING, video_url="", poster_url=poster_url,
            duration=duration, mux_asset_id=asset_id, mux_playback_id=playback_id,
            now=now,
        )
        return True, ""

    if _mux_required():
        # Durable storage is configured, so this reel was always going to be
        # served from a URL a machine fetch cannot read. Fail the render instead
        # of publishing it: a product that sits out a cycle is recoverable, a
        # reel in a member's feed that shows a poster and never plays is what
        # this whole path exists to stop happening again.
        return fail("mux_ingest_unavailable")

    # Local and CI: no object storage, so _publish returned a path under the
    # public upload root and serving it directly is correct.
    _settle_render(
        render_id, READY, video_url=video_url, poster_url=poster_url,
        duration=duration, now=now,
    )
    return True, ""


def _load_listing(listing_id: int) -> dict:
    """Re-read the product at render time.

    Not the row the curator ranked: an encode can start minutes after the tick
    that queued it, and spending ninety seconds of CPU on a listing that was
    deleted in between is the cheapest waste to avoid.
    """
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        listing, blocker = eligibility.revalidate(cur, int(listing_id))
        return {} if blocker else listing
    except Exception:
        log.warning("pulsedrop_render_listing_load_failed id=%s", listing_id, exc_info=True)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or ""


def _run(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout or config.reel_render_timeout_seconds(),
    )


def _download(url: str, target: Path) -> bool:
    """Fetch one source file. ``https`` only.

    The URLs come from the platform's own media columns, but they are still data
    from a row a seller can write, so the scheme is checked rather than assumed.
    A ``file://`` here would be a path traversal with an ffmpeg on the end of it.
    """
    if not str(url or "").lower().startswith("https://"):
        return False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "PulseDrop/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            target.write_bytes(response.read())
        return target.stat().st_size > 0
    except Exception:
        log.info("pulsedrop_render_download_failed url=%s", url[:120], exc_info=True)
        return False


def _fit_filter(label_in: str, label_out: str, *, scale: int = 1) -> str:
    """Scale-to-fit over a blurred copy of the same frame.

    Two branches of one input: the backdrop is over-scaled, blurred and dimmed;
    the foreground is scaled to fit whole and centred on top. The dimming matters
    — an undimmed blur competes with the product for attention and the result
    reads as a mistake rather than as depth.

    The blur is computed at a quarter frame and then upscaled, rather than at
    full resolution. A Gaussian wide enough to read as *background* costs, at
    2160x3840, about two thirds of the whole encode; resampling a small blurred
    image back up produces a picture that cannot be told apart from it, because
    upscaling a blur is itself a blur. Measured on the three-image composition:
    34s of CPU down to 12s.

    ``scale`` composes at a multiple of the delivery frame. Path B zooms after
    this step, and zooming a frame that is already exactly 1080 wide resamples
    from a shrinking pixel window — the end of every segment comes out softer
    than the start. Composing at 2x gives the zoom real pixels to take.

    ``label_out`` is reused as the uniquifier for the intermediate pad names, so
    callers must pass something filtergraph-safe. Deriving them from
    ``label_in`` would embed input specifiers like ``0:v``, and a pad named
    ``bg0:v`` is a parse waiting to go wrong.
    """
    width = FRAME_WIDTH * scale
    height = FRAME_HEIGHT * scale
    tag = label_out
    return (
        f"[{label_in}]split=2[bg{tag}][fg{tag}];"
        f"[bg{tag}]scale={FRAME_WIDTH // 4}:{FRAME_HEIGHT // 4}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH // 4}:{FRAME_HEIGHT // 4},gblur=sigma=11,"
        f"eq=brightness=-0.18:saturation=0.85,scale={width}:{height}[bgb{tag}];"
        f"[fg{tag}]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs{tag}];"
        f"[bgb{tag}][fgs{tag}]overlay=(W-w)/2:(H-h)/2[{tag}]"
    )


def _fill_filter(label_in: str, label_out: str) -> str:
    return (
        f"[{label_in}]scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}[{label_out}]"
    )


def _aspect_ratio(path: Path) -> float:
    """Width over height of a source file, or 0 when unmeasurable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = _run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            timeout=20,
        )
        width, _, height = (result.stdout or "").strip().partition("x")
        return float(width) / float(height) if float(height or 0) else 0.0
    except Exception:
        return 0.0


def _poster(video: Path, target: Path) -> Path | None:
    """First-second still, for the feed tile and the player's first paint."""
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return None
    try:
        result = _run(
            [ffmpeg, "-y", "-ss", "0.5", "-i", str(video), "-frames:v", "1",
             "-q:v", "3", str(target)],
            timeout=45,
        )
        return target if result.returncode == 0 and target.exists() else None
    except Exception:
        return None


def _render_from_video(listing: dict, work: Path) -> tuple[Path, Path | None, float] | None:
    """Path A — the seller's own footage, reframed to 9:16.

    Their framing, their lighting, their product moving. All this does is make it
    fit the surface: no speed change, no colour grade, no added motion, because
    every one of those would be PulseSoc editing a seller's video and then
    publishing the result as a description of their product.

    Audio is dropped. PulseSoc has permission to show the seller's product; it
    does not have a licence to redistribute whatever is playing in their
    workshop, and a silent Reel is a supported thing on this surface.
    """
    from services import media_covers

    source_url = eligibility.video_source(listing)
    source = work / "source.mp4"
    if not _download(source_url, source):
        return None

    measured = media_covers.video_duration_seconds(source)
    ceiling = float(config.reel_max_source_seconds())
    if measured and measured > ceiling:
        # Not truncated — refused. Cutting someone else's product video at an
        # arbitrary second is an editorial act, and the composer falls back to
        # the image path rather than perform it.
        log.info(
            "pulsedrop_render_video_too_long listing_id=%s seconds=%.1f",
            listing.get("id"), measured,
        )
        return _render_from_images(listing, work)

    ratio = _aspect_ratio(source)
    chain = _fill_filter("0:v", "v") if ratio >= _FILL_MIN_RATIO else _fit_filter("0:v", "v")
    output = work / "reel.mp4"
    command = [
        _ffmpeg(), "-y", "-i", str(source),
        "-filter_complex", chain, "-map", "[v]", "-an",
        "-r", str(FRAME_RATE), "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-b:v", _VIDEO_BITRATE, "-maxrate", _VIDEO_MAXRATE, "-bufsize", _VIDEO_BUFSIZE,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    result = _run(command)
    if result.returncode != 0 or not output.exists():
        log.info("pulsedrop_render_video_ffmpeg_failed stderr=%s", (result.stderr or "")[-400:])
        return None
    duration = media_covers.video_duration_seconds(output) or measured
    return output, _poster(output, work / "poster.jpg"), duration


def _render_from_images(listing: dict, work: Path) -> tuple[Path, Path | None, float] | None:
    """Path B — a composition from the product's photographs.

    Each still gets a slow push-in and the cuts are crossfades, which is the
    whole difference between this and a slideshow: a slideshow cuts on a timer
    and the eye reads it as a contact sheet, while continuous movement across a
    dissolve reads as one shot. The push is 8% over the full segment — small
    enough that no one identifies it as an effect, large enough that the frame is
    never still.

    The framing happens *before* the zoom, and that ordering is the whole
    correctness of this function rather than a preference. ``zoompan`` scales
    whatever window it crops to its output size ``s``, with no regard for the
    window's aspect ratio: hand it a square photograph and ask for 1080x1920 and
    it stretches the product to twice its height. Verified by rendering it. So
    the still is composed to the delivery aspect first — fitted over its own
    blurred backdrop — and only then zoomed, at which point the window and the
    output are the same shape and the zoom cannot distort anything.

    The composition is done at 2x so the zoom has pixels to take; see
    :func:`_fit_filter`.
    """
    urls = eligibility.image_urls(listing)[: config.reel_max_images()]
    if len(urls) < config.reel_min_images():
        return None

    sources: list[Path] = []
    for index, url in enumerate(urls):
        candidate = work / f"src-{index}.img"
        if _download(url, candidate):
            sources.append(candidate)
    if not sources:
        return None

    total = float(config.reel_target_seconds())
    segment = max(1.6, total / len(sources))
    # Crossfades consume time from both neighbours, so the segments have to be
    # longer than total/n or the finished Reel comes out short of its target.
    fade = 0.5 if len(sources) > 1 else 0.0
    segment += fade * (len(sources) - 1) / len(sources)
    frames = int(segment * FRAME_RATE)

    inputs: list[str] = []
    chains: list[str] = []
    for index, path in enumerate(sources):
        inputs += ["-loop", "1", "-t", f"{segment:.3f}", "-i", str(path)]
        chains.append(_fit_filter(f"{index}:v", f"c{index}", scale=2))
        # ``fps`` must be given to zoompan explicitly, not just upstream of it:
        # the filter re-tags its output at its own default of 25, so a 3.000s
        # segment leaves it claiming 3.600s and every xfade offset below —
        # computed in seconds — lands in the wrong place. Measured, not assumed.
        chains.append(
            f"[c{index}]fps={FRAME_RATE},"
            f"zoompan=z='min(1+0.08*on/{frames},1.08)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={FRAME_WIDTH}x{FRAME_HEIGHT}:fps={FRAME_RATE},"
            f"setsar=1[s{index}]"
        )

    if len(sources) == 1:
        chains.append("[s0]null[v]")
    else:
        previous = "s0"
        elapsed = segment
        for index in range(1, len(sources)):
            label = "v" if index == len(sources) - 1 else f"x{index}"
            offset = elapsed - fade
            chains.append(
                f"[{previous}][s{index}]xfade=transition=fade:duration={fade}:"
                f"offset={offset:.3f}[{label}]"
            )
            previous = label
            elapsed = offset + segment

    output = work / "reel.mp4"
    command = [_ffmpeg(), "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[v]"]
    if not COMPOSED_HAS_AUDIO:
        command.append("-an")
    command += [
        "-r", str(FRAME_RATE), "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-b:v", _VIDEO_BITRATE, "-maxrate", _VIDEO_MAXRATE, "-bufsize", _VIDEO_BUFSIZE,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    result = _run(command)
    if result.returncode != 0 or not output.exists():
        log.info("pulsedrop_render_images_ffmpeg_failed stderr=%s", (result.stderr or "")[-400:])
        return None

    from services import media_covers

    duration = media_covers.video_duration_seconds(output)
    return output, _poster(output, work / "poster.jpg"), duration


def _to_mux(storage_key: str, *, listing_id: int = 0) -> tuple[str, str]:
    """Hand the uploaded mp4 to Mux. ``("", "")`` when that is not possible.

    PulseSoc does not serve video from the bucket CDN. Every reel belonging to a
    real member plays from ``stream.mux.com/<playback_id>.m3u8``; PulseDrop was
    the only producer writing a ``cdn.`` URL into ``pulse_reels.video_url``, and
    Cloudflare answers those mp4 requests with a 403 bot challenge -- confirmed
    with a browser User-Agent and with a Range request, while the poster .jpg
    beside it returns 200. So every PulseDrop reel ever published rendered its
    poster frame and then failed to play. The fix is not a Cloudflare rule, it
    is using the pipeline the rest of the platform already uses.

    The input is a *presigned* S3 URL, not the public one, for the same reason:
    Mux's fetcher would be challenged exactly as curl was. The object is
    reachable over the S3 API and unreachable over the CDN, which is the whole
    asymmetry. This mirrors ``agora_cloud_recording_service``, which already
    presigns an R2 key to feed Mux.

    Returns ids only. The playback URL is not written here because the asset
    ingests asynchronously and a URL stored before Mux reports ``ready`` is a
    URL that 404s for the first few seconds of its life.
    """
    from services import media_storage

    try:
        status = media_storage.storage_status()
        bucket = str(status.get("bucket") or "")
        if status.get("provider") not in {"r2", "s3"} or not bucket:
            return "", ""
        client = media_storage.object_client()
        if not client:
            return "", ""
        source = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": storage_key},
            ExpiresIn=int(config.reel_mux_input_ttl_seconds()),
        )
    except Exception:
        log.warning("pulsedrop_mux_presign_failed key=%s", storage_key, exc_info=True)
        return "", ""

    try:
        from services import media_service

        created = media_service.create_mux_asset_from_url(
            source, trace_id=f"pulsedrop-reel-{listing_id}"
        )
    except Exception:
        log.warning("pulsedrop_mux_create_raised key=%s", storage_key, exc_info=True)
        return "", ""

    if not created.get("ok"):
        log.warning(
            "pulsedrop_mux_create_failed key=%s status=%s",
            storage_key, created.get("status"),
        )
        return "", ""
    return str(created.get("asset_id") or ""), str(created.get("playback_id") or "")


def _mux_required() -> bool:
    """Whether a reel on this deployment must go through Mux to be playable.

    True exactly when media lives in a bucket behind the CDN. That is the
    condition under which a bucket URL is unreadable by anything but a browser
    that has passed a Cloudflare challenge, so it is also the condition under
    which falling back to one would republish the original bug. A local
    checkout serves media off disk and has no such problem.
    """
    from services import media_storage

    try:
        return media_storage.storage_status().get("provider") in {"r2", "s3"}
    except Exception:
        log.warning("pulsedrop_storage_status_unreadable", exc_info=True)
        return False


def mux_state(asset_id: str) -> str:
    """Mux's own word for an asset: ``ready``, ``errored``, or something else.

    ``""`` when the asset cannot be read at all, which is deliberately *not*
    treated as an error by the caller: a Mux outage or an expired token would
    otherwise retire a perfectly good render.
    """
    from services import media_service

    try:
        return str((media_service.get_mux_asset(asset_id) or {}).get("mux_status") or "")
    except Exception:
        log.warning("pulsedrop_mux_status_failed asset=%s", asset_id, exc_info=True)
        return ""


def promote_transcoding(limit: int = 6, *, now: datetime | None = None) -> dict:
    """Move finished Mux ingests to READY. The other half of :func:`run_pending`.

    Separate from the render sweep because it is a cheap HTTP poll rather than
    an encode, so it is not worth rationing to one per tick, and because a
    render that is waiting on Mux must never be handed back to ffmpeg.

    An asset Mux reports ``errored`` falls back to FAILED rather than to the
    bucket URL. Publishing the CDN URL is what produced thirty-four unplayable
    reels; a product that silently skips a cycle is a far smaller fault than a
    reel in a member's feed that shows a poster and then stops.
    """
    from services import db as db_service
    from services.pulsedrop import campaigns

    counters = {"checked": 0, "ready": 0, "errored": 0, "waiting": 0, "requeued": 0}
    moment = now or datetime.utcnow()
    # Long enough that a slow ingest of a 15s clip is never mistaken for a stuck
    # one, short enough that a render lost to a Mux-side disappearance rejoins
    # the queue the same day. Bounded anyway: re-queuing lands on PENDING with
    # ``attempts`` already spent, so a row that cannot ingest retires on its own.
    stuck = timedelta(seconds=max(600, config.reel_render_timeout_seconds() * 10))
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, listing_id, mux_asset_id, mux_playback_id, poster_url,"
            " duration_seconds, updated_at FROM pulsedrop_renders"
            " WHERE state=? ORDER BY updated_at ASC LIMIT ?",
            (TRANSCODING, max(1, int(limit))),
        )
        rows = [dict(row) for row in cur.fetchall() or []]
    except Exception:
        log.warning("pulsedrop_transcoding_scan_failed", exc_info=True)
        return counters
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for row in rows:
        counters["checked"] += 1
        asset_id = str(row.get("mux_asset_id") or "")
        playback_id = str(row.get("mux_playback_id") or "")
        render_id = int(row.get("id") or 0)
        if not asset_id or not playback_id:
            # Nothing to poll and nothing to wait for. Back to PENDING so the
            # next sweep re-encodes and re-submits, which is the only way out.
            _settle_render(render_id, PENDING, reason="mux_ids_missing", now=now)
            counters["errored"] += 1
            continue
        state = mux_state(asset_id)
        if state == "ready":
            _settle_render(
                render_id, READY,
                video_url=f"https://stream.mux.com/{playback_id}.m3u8",
                poster_url=str(row.get("poster_url") or ""),
                duration=float(row.get("duration_seconds") or 0),
                mux_asset_id=asset_id, mux_playback_id=playback_id, now=now,
            )
            counters["ready"] += 1
        elif state == "errored":
            _settle_render(render_id, FAILED, reason="mux_ingest_errored", now=now)
            counters["errored"] += 1
        elif campaigns._is_stale(row.get("updated_at"), moment, stuck):
            # Still not ready long after any real ingest would have finished, or
            # unreadable for that long. Without this the row waits forever: a
            # transcoding render is invisible to _claim_render by design, so
            # nothing else in the system would ever touch it again.
            _settle_render(render_id, PENDING, reason="mux_ingest_stalled", now=now)
            counters["requeued"] += 1
        else:
            counters["waiting"] += 1
    return counters


def _publish(local: Path, storage_key: str, content_type: str) -> str:
    """Store the finished file where the CDN serves it. ``""`` on failure.

    Reuses ``media_storage`` rather than writing a second uploader, so PulseDrop
    lands in the same bucket, behind the same CDN, with the same public-URL rule
    as every other piece of media on the platform.

    For the poster that is the whole story. For the video this is only the
    durable copy -- see :func:`_to_mux` for why the URL a member plays is a Mux
    one, and why the bucket copy still has to exist for Mux to ingest from.
    """
    from services import media_storage

    try:
        if media_storage.provider() in {"r2", "s3"}:
            uploaded, error = media_storage._upload_to_object_storage(
                local, storage_key, content_type
            )
            if not uploaded:
                log.warning("pulsedrop_render_upload_failed key=%s error=%s", storage_key, error)
                return ""
            return media_storage.public_media_url(storage_key)
        target = media_storage.PUBLIC_UPLOAD_ROOT / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        return f"/static/uploads/{storage_key}"
    except Exception:
        log.warning("pulsedrop_render_publish_failed key=%s", storage_key, exc_info=True)
        return ""
