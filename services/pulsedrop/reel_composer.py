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
READY = "ready"
FAILED = "failed"

#: Reels are muted by default and that is a decision, not an omission. PulseDrop
#: does not own a music licence, and a seller's own product video may carry
#: audio they have rights to but PulseSoc does not have rights to redistribute
#: under its own account. Composed Reels therefore have no audio track at all —
#: which the surface already supports, because members post silent Reels.
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
                   duration: float = 0.0, reason: str = "", now: datetime | None = None) -> None:
    from services import db as db_service

    stamp = (now or datetime.utcnow()).isoformat(timespec="seconds")
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulsedrop_renders
            SET state=?, video_url=?, poster_url=?, duration_seconds=?,
                frame_width=?, frame_height=?, failure_reason=?, claimed_by='', updated_at=?
            WHERE id=?
            """,
            (
                state,
                video_url,
                poster_url,
                float(duration or 0),
                FRAME_WIDTH if state == READY else 0,
                FRAME_HEIGHT if state == READY else 0,
                str(reason or "")[:300],
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
    """
    from services.pulsedrop import lease

    counters = {"started": 0, "succeeded": 0, "failed": 0}
    if not config.reels_enabled():
        return counters
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
    except Exception:
        log.exception("pulsedrop_render_raised id=%s", render_id)
        return fail("render_exception")

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


def _publish(local: Path, storage_key: str, content_type: str) -> str:
    """Store the finished file where the CDN serves it. ``""`` on failure.

    Reuses ``media_storage`` rather than writing a second uploader, so PulseDrop
    lands in the same bucket, behind the same CDN, with the same public-URL rule
    as every other piece of media on the platform.
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
