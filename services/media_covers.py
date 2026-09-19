"""Smart cover/thumbnail generation for uploaded media.

Every grid tile in the apps must show a meaningful preview — never a black
square. This module produces small/medium/large JPEG covers once per media
item (at upload, or lazily for legacy rows via the media worker) and stores
them permanently next to the source object.

Videos get intelligent frame selection: ffmpeg's `thumbnail` filter picks a
representative frame; if the result is too dark (black/fade/transition frame)
we fall back to frames at 15% and 40% of the duration and keep the brightest
usable candidate.

ffmpeg-only on purpose — it is already a deploy dependency (Railway:
RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg) and handles both images and video, so no
new Python imaging dependency is introduced. Every public function degrades to
a no-op (returns {}) when ffmpeg is missing or generation fails: covers are an
enhancement and must never block an upload.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from services import media_storage

COVER_SIZES = {"small": 320, "medium": 960, "large": 1440}

# Below this average luma (0-255) a frame reads as a black square in a grid.
_MIN_ACCEPTABLE_LUMA = float(os.getenv("MEDIA_COVER_MIN_LUMA", "18"))
_FFMPEG_TIMEOUT = int(os.getenv("MEDIA_COVER_FFMPEG_TIMEOUT_SECONDS", "45"))

_YAVG_PATTERN = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def _run(command: list[str], timeout: int = _FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def video_duration_seconds(source: Path | str) -> float:
    """Read a stored video's real length off the container, in seconds.

    0.0 means unmeasurable, not zero-length: no ffprobe on the box, an unreadable
    container, a probe that timed out. Callers enforcing a duration ceiling must
    treat that as an absent measurement rather than a passing one -- which is what
    `stored_video_policy.exceeds_limit` already does with a falsy duration.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = _run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
            timeout=15,
        )
        return max(0.0, float((result.stdout or "0").strip() or 0))
    except Exception:
        return 0.0


def _frame_luma(image_path: Path) -> float:
    """Average luma of a still, via signalstats. Returns -1 when unmeasurable."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return -1.0
    try:
        result = _run(
            [ffmpeg, "-i", str(image_path), "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
            timeout=15,
        )
        match = _YAVG_PATTERN.search(result.stdout or "") or _YAVG_PATTERN.search(result.stderr or "")
        return float(match.group(1)) if match else -1.0
    except Exception:
        return -1.0


def _extract_frame(source: Path | str, target: Path, seek_seconds: float | None) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    command = [ffmpeg, "-y"]
    if seek_seconds is not None:
        command += ["-ss", f"{max(0.0, seek_seconds):.2f}"]
    command += ["-i", str(source)]
    if seek_seconds is None:
        # Representative-frame selection across the first ~100 frames; this is
        # what skips black intro, loading, and fade frames on most videos.
        command += ["-vf", "thumbnail=n=100"]
    command += ["-frames:v", "1", "-q:v", "3", str(target)]
    try:
        result = _run(command)
    except Exception:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def extract_video_poster_frame(source: Path | str, tmp_dir: Path) -> Path | None:
    """Best usable frame: representative first, brighter seeks if it is dark.

    `source` may be a remote manifest URL as well as a local path. Pass such a
    URL as a plain string -- `Path("https://host/x.m3u8")` collapses the double
    slash into `https:/host/x.m3u8`, which ffmpeg cannot open.
    """
    duration = video_duration_seconds(source)
    attempts: list[tuple[str, float | None]] = [("representative", None)]
    if duration > 2:
        attempts += [("seek15", duration * 0.15), ("seek40", duration * 0.40)]
    else:
        attempts += [("seek1", 1.0)]
    best: tuple[float, Path] | None = None
    for name, seek in attempts:
        candidate = tmp_dir / f"frame-{name}.jpg"
        if not _extract_frame(source, candidate, seek):
            continue
        luma = _frame_luma(candidate)
        if luma >= _MIN_ACCEPTABLE_LUMA:
            return candidate
        if best is None or luma > best[0]:
            best = (luma, candidate)
    # Every candidate was dark (night footage is legitimate) — keep the
    # brightest one rather than shipping no poster at all.
    return best[1] if best else None


def _scaled_jpeg(source_image: Path, target: Path, width: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source_image),
        "-frames:v",
        "1",
        "-vf",
        # Never upscale; -2 keeps height even for the encoder.
        f"scale='min({int(width)},iw)':-2",
        "-q:v",
        "4",
        str(target),
    ]
    try:
        result = _run(command)
    except Exception:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def _cover_key(storage_key: str, size_name: str) -> str:
    base = str(storage_key or "").strip().replace("\\", "/").lstrip("/")
    stem = base.rsplit(".", 1)[0] if "." in base.rsplit("/", 1)[-1] else base
    return f"{stem}-cover-{size_name}.jpg"


_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _mux_playback_source(row: dict) -> str:
    """Public HLS manifest for a row whose original upload was never retained.

    Videos ingested straight to Mux keep no local or R2 object: `storage_key`,
    `object_key` and every `*_url` source column are empty, so there is no file
    to read a frame out of. ffmpeg opens the manifest directly, which is what
    lets the poster stay a stored JPEG of ours instead of a live
    `image.mux.com` URL the apps would have to fetch on every render.

    Only a `ready` asset is offered: an earlier status means Mux has not
    finished producing the manifest.
    """
    if str(row.get("mux_status") or "").strip().lower() != "ready":
        return ""
    # Keep the stored URL verbatim when it is already a Mux manifest -- a signed
    # asset carries its token in the query string.
    playback_url = str(row.get("playback_url") or "").strip()
    if playback_url.startswith("https://stream.mux.com/"):
        return playback_url
    playback_id = str(row.get("mux_playback_id") or "").strip()
    return f"https://stream.mux.com/{playback_id}.m3u8" if playback_id else ""


def _mux_cover_base_key(row: dict) -> str:
    """Storage key to hang Mux-sourced covers off, for rows that have none."""
    playback_id = _UNSAFE_KEY_CHARS.sub("", str(row.get("mux_playback_id") or "").strip())
    return f"pulse_media/covers/mux/{playback_id}.jpg" if playback_id else ""


def _publish(local_file: Path, storage_key: str) -> str:
    """Store one cover durably; returns its public URL or '' on failure."""
    if media_storage.provider() in {"r2", "s3"}:
        uploaded, error = media_storage._upload_to_object_storage(local_file, storage_key, "image/jpeg")
        if not uploaded:
            logging.warning("MEDIA_COVER_UPLOAD_FAILED key=%s error=%s", storage_key, error)
            return ""
        return media_storage.public_media_url(storage_key)
    target = media_storage.PUBLIC_UPLOAD_ROOT / storage_key
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_file, target)
    except Exception as exc:
        logging.warning("MEDIA_COVER_LOCAL_SAVE_FAILED key=%s error=%s", storage_key, exc)
        return ""
    return f"/static/uploads/{storage_key}"


def generate_covers(source_path: Path | str, media_type: str, storage_key: str) -> dict:
    """Produce and store S/M/L covers for one media file.

    Returns {thumbnail_url, poster_url, small_url, medium_url, large_url} on
    success, {} on any failure. Audio/text content gets no server-side cover —
    clients render designed cards for those.
    """
    media_type = str(media_type or "").lower()
    if media_type not in {"image", "gif", "video"}:
        return {}
    source = Path(source_path or "")
    if not source.is_file() or not ffmpeg_available():
        return {}
    try:
        with tempfile.TemporaryDirectory(prefix="coinpilotx-covers-") as tmp:
            tmp_dir = Path(tmp)
            if media_type == "video":
                still = extract_video_poster_frame(source, tmp_dir)
                if still is None:
                    return {}
            else:
                still = source
            urls: dict[str, str] = {}
            for name, width in COVER_SIZES.items():
                scaled = tmp_dir / f"cover-{name}.jpg"
                if not _scaled_jpeg(still, scaled, width):
                    continue
                url = _publish(scaled, _cover_key(storage_key, name))
                if url:
                    urls[name] = url
            if not urls:
                return {}
            large = urls.get("large") or urls.get("medium") or urls.get("small") or ""
            medium = urls.get("medium") or large
            small = urls.get("small") or medium
            return {
                "small_url": small,
                "medium_url": medium,
                "large_url": large,
                "thumbnail_url": medium,
                "poster_url": large,
            }
    except Exception as exc:
        logging.warning("MEDIA_COVER_GENERATE_FAILED key=%s type=%s error=%s", storage_key, media_type, exc)
        return {}


def row_needs_covers(row: dict) -> bool:
    """True when a chat_media_uploads row still lacks real generated covers.

    Legacy rows have small/medium/large equal to the source URL (or empty) and
    videos have empty/self-referential posters — both read as "no cover".
    """
    media_type = str(row.get("media_type") or "").lower()
    if media_type not in {"image", "gif", "video"}:
        return False
    # Voice notes were stored as media_type='video' with an audio mime. There is
    # no frame to extract, so asking for one only burns the retry budget --
    # clients render a designed card for audio.
    if str(row.get("mime_type") or "").lower().startswith("audio/"):
        return False
    media_url = str(row.get("media_url") or "")
    small = str(row.get("small_url") or "")
    if not small or small == media_url:
        return True
    if media_type == "video":
        poster = str(row.get("poster_url") or "")
        return not poster or poster == media_url
    return False


def ensure_covers_for_row(row: dict) -> dict:
    """Backfill path: fetch the source (local or R2), generate, store covers.

    Returns the cover URL dict or {} — never raises.
    """
    try:
        if not row_needs_covers(row) or not ffmpeg_available():
            return {}
        storage_key = str(row.get("storage_key") or row.get("object_key") or "").strip().replace("\\", "/").lstrip("/")
        media_type = str(row.get("media_type") or "").lower()
        local_candidate = media_storage.PUBLIC_UPLOAD_ROOT / storage_key if storage_key else None
        if local_candidate and local_candidate.is_file():
            return generate_covers(local_candidate, media_type, storage_key)
        if storage_key and media_storage.provider() in {"r2", "s3"}:
            suffix = Path(storage_key).suffix or ".bin"
            with tempfile.TemporaryDirectory(prefix="coinpilotx-cover-src-") as tmp:
                source = Path(tmp) / f"source{suffix}"
                obj = media_storage.get_object(storage_key)
                body = obj.get("Body")
                try:
                    with source.open("wb") as fh:
                        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                            if chunk:
                                fh.write(chunk)
                finally:
                    close = getattr(body, "close", None)
                    if callable(close):
                        close()
                return generate_covers(source, media_type, storage_key)
        if media_type == "video":
            return _covers_from_mux_playback(row)
        return {}
    except Exception as exc:
        logging.warning("MEDIA_COVER_BACKFILL_FAILED media_id=%s error=%s", row.get("id"), exc)
        return {}


def _covers_from_mux_playback(row: dict) -> dict:
    """Cover a video whose only surviving copy is its Mux asset."""
    hls = _mux_playback_source(row)
    cover_base = str(row.get("storage_key") or "").strip().replace("\\", "/").lstrip("/") or _mux_cover_base_key(row)
    if not hls or not cover_base:
        return {}
    with tempfile.TemporaryDirectory(prefix="coinpilotx-cover-hls-") as tmp:
        still = extract_video_poster_frame(hls, Path(tmp))
        if still is None:
            return {}
        # The frame is a local JPEG by now, so the scale-and-publish half of the
        # pipeline runs exactly as it does for a still image.
        return generate_covers(still, "image", cover_base)


def apply_cover_updates(cur, media_id: int, covers: dict) -> bool:
    """Persist generated cover URLs; only fills, never clobbers a creator cover."""
    if not covers or not media_id:
        return False
    cur.execute(
        """
        UPDATE chat_media_uploads
        SET small_url=?,
            medium_url=?,
            large_url=?,
            thumbnail_url=CASE
                WHEN COALESCE(thumbnail_url, '')='' OR thumbnail_url=media_url THEN ?
                ELSE thumbnail_url
            END,
            poster_url=CASE
                WHEN COALESCE(poster_url, '')='' OR poster_url=media_url THEN ?
                ELSE poster_url
            END
        WHERE id=?
        """,
        (
            covers.get("small_url") or "",
            covers.get("medium_url") or "",
            covers.get("large_url") or "",
            covers.get("thumbnail_url") or "",
            covers.get("poster_url") or "",
            int(media_id),
        ),
    )
    return True
