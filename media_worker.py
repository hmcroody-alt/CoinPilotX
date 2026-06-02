"""CoinPilotXAI media processing worker.

Railway worker command:
    python media_worker.py

This worker keeps media uploads moving independently from the web process. It
does conservative local validation, applies safe thumbnail fallbacks, processes
media queue jobs, and reports worker heartbeats for observability.
"""

from __future__ import annotations

import logging
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

print("CoinPilotX media engine boot starting", flush=True)
print("DATABASE_URL present=", bool(os.getenv("DATABASE_URL")), flush=True)
print("REDIS_URL present=", bool(os.getenv("REDIS_URL")), flush=True)
print("MEDIA_STORAGE_PROVIDER=", os.getenv("MEDIA_STORAGE_PROVIDER", "local"), flush=True)
print("R2_BUCKET present=", bool(os.getenv("R2_BUCKET")), flush=True)
print("R2_PUBLIC_BASE_URL present=", bool(os.getenv("R2_PUBLIC_BASE_URL")), flush=True)
print("ffmpeg present=", bool(shutil.which("ffmpeg")), flush=True)


def running_on_railway() -> bool:
    return any(
        os.getenv(name)
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_NAME",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_SERVICE_NAME",
        )
    )


if running_on_railway() and not os.getenv("DATABASE_URL"):
    print("Missing DATABASE_URL. Attach Postgres variables to coinpilotx-media-engine.", flush=True)
    sys.exit(78)

try:
    import bot
    from services import media_service, media_storage
except Exception as exc:
    print("CoinPilotX media engine import failed", repr(exc), flush=True)
    traceback.print_exc()
    raise


WORKER_NAME = "coinpilotx-media-engine"
INTERVAL_SECONDS = max(5, int(os.getenv("MEDIA_WORKER_INTERVAL_SECONDS", "20")))
BATCH_SIZE = max(1, min(int(os.getenv("MEDIA_WORKER_BATCH_SIZE", "25")), 100))
MAX_ATTEMPTS = max(1, int(os.getenv("MEDIA_WORKER_MAX_ATTEMPTS", "3")))
MEDIA_JOB_TYPES = {"generate_thumbnail", "process_video"}
RUNNING = True


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _handle_stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def dependency_snapshot() -> dict:
    storage_provider = os.getenv("MEDIA_STORAGE_PROVIDER", "local").strip().lower() or "local"
    r2_bucket = bool(os.getenv("R2_BUCKET"))
    r2_public_base_url = bool(os.getenv("R2_PUBLIC_BASE_URL"))
    r2_endpoint = bool(os.getenv("R2_ENDPOINT_URL") or os.getenv("R2_ENDPOINT") or os.getenv("R2_ACCOUNT_ID"))
    r2_credentials = bool(os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_SECRET_ACCESS_KEY"))
    storage_configured = True
    if storage_provider in {"r2", "s3"}:
        storage_configured = r2_bucket and r2_public_base_url and r2_endpoint and r2_credentials
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_version = ""
    if ffmpeg_path:
        try:
            import subprocess

            result = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=3)
            ffmpeg_version = (result.stdout.splitlines() or [""])[0][:180]
        except Exception as exc:
            ffmpeg_version = f"version unavailable: {exc.__class__.__name__}"
    return {
        "database_url_present": bool(os.getenv("DATABASE_URL")),
        "redis_url_present": bool(os.getenv("REDIS_URL")),
        "media_storage_provider": storage_provider,
        "storage_configured": storage_configured,
        "r2_bucket_present": r2_bucket,
        "r2_endpoint_present": r2_endpoint,
        "r2_credentials_present": r2_credentials,
        "r2_public_base_url_present": r2_public_base_url,
        "ffmpeg_present": bool(ffmpeg_path),
        "ffmpeg_version": ffmpeg_version,
        "railway_ffmpeg_hint": "Set RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg when Railway image ffmpeg is missing.",
        "worker_heartbeat": "coinpilotx-media-engine",
        "last_processed_media": "",
        "last_error": "",
        "batch_size": BATCH_SIZE,
        "interval_seconds": INTERVAL_SECONDS,
    }


def log_boot_diagnostics() -> None:
    details = dependency_snapshot()
    logging.info(
        "MEDIA_ENGINE_BOOT_CHECK database_url=%s redis_url=%s provider=%s storage_configured=%s r2_bucket=%s r2_endpoint=%s r2_credentials=%s r2_public_base_url=%s ffmpeg=%s",
        details["database_url_present"],
        details["redis_url_present"],
        details["media_storage_provider"],
        details["storage_configured"],
        details["r2_bucket_present"],
        details["r2_endpoint_present"],
        details["r2_credentials_present"],
        details["r2_public_base_url_present"],
        details["ffmpeg_present"],
    )
    if details["media_storage_provider"] in {"r2", "s3"} and not details["storage_configured"]:
        logging.warning("MEDIA_ENGINE_STORAGE_CONFIG_INCOMPLETE provider=%s", details["media_storage_provider"])
    if not details["ffmpeg_present"]:
        logging.warning("MEDIA_ENGINE_FFMPEG_MISSING thumbnails/transcoding will use safe fallbacks until ffmpeg is installed")


def _media_path(media_url: str) -> Path | None:
    url = str(media_url or "").strip()
    if not url.startswith("/static/uploads/"):
        return None
    path = Path(bot.webhook_app.root_path, url.lstrip("/")).resolve()
    uploads_root = Path(bot.webhook_app.root_path, "static", "uploads").resolve()
    try:
        path.relative_to(uploads_root)
    except ValueError:
        return None
    return path


def _media_file_exists(media_url: str) -> bool:
    path = _media_path(media_url)
    return bool(path and path.is_file())


def _process_upload_row(cur, row) -> dict:
    item = dict(row or {})
    media_id = int(item.get("id") or 0)
    media_url = item.get("media_url") or ""
    media_type = item.get("media_type") or "file"
    updates = {
        "moderation_status": "approved",
        "moderation_reason": "",
        "thumbnail_url": item.get("thumbnail_url") or "",
    }
    if media_url.startswith("/static/uploads/") and not _media_file_exists(media_url):
        updates["moderation_status"] = "blocked"
        updates["moderation_reason"] = "Media file is missing from local storage."
    elif media_type == "video" and not updates["thumbnail_url"]:
        updates["thumbnail_url"] = media_url
    elif media_type in {"image", "gif", "audio", "file"} and not updates["thumbnail_url"]:
        updates["thumbnail_url"] = media_url

    cur.execute(
        """
        UPDATE chat_media_uploads
        SET moderation_status=?, moderation_reason=?, thumbnail_url=COALESCE(NULLIF(?, ''), thumbnail_url)
        WHERE id=?
        """,
        (updates["moderation_status"], updates["moderation_reason"], updates["thumbnail_url"], media_id),
    )
    return {"media_id": media_id, "status": updates["moderation_status"], "media_type": media_type}


def process_pending_uploads(limit: int = BATCH_SIZE) -> dict:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND COALESCE(moderation_status, 'pending') IN ('pending', 'needs_review', '')
        ORDER BY id ASC
        LIMIT ?
        """,
        (max(1, int(limit or BATCH_SIZE)),),
    )
    rows = cur.fetchall()
    approved = 0
    blocked = 0
    for row in rows:
        result = _process_upload_row(cur, row)
        logging.info(
            "MEDIA_WORKER_UPLOAD_STATE media_id=%s media_type=%s status=%s",
            result.get("media_id"),
            result.get("media_type"),
            result.get("status"),
        )
        if result["status"] == "blocked":
            blocked += 1
        else:
            approved += 1
    conn.commit()
    conn.close()
    return {"checked": len(rows), "approved": approved, "blocked": blocked}


def _complete_job(cur, job_id: int, status: str = "done", error: str = "") -> None:
    cur.execute(
        "UPDATE pulse_jobs SET status=?, error_message=?, updated_at=?, completed_at=? WHERE id=?",
        (status, str(error or "")[:1000], _now(), _now() if status == "done" else None, int(job_id)),
    )


def _table_has_column(cur, table: str, column: str) -> bool:
    try:
        if getattr(bot.db_service, "IS_POSTGRES", False):
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=? AND column_name=?
                """,
                (table, column),
            )
            return bool(cur.fetchone())
        cur.execute(f"PRAGMA table_info({table})")
        return any(str(row[1] if not hasattr(row, "get") else row.get("name")) == column for row in cur.fetchall())
    except Exception:
        return False


def _media_row_matches_target(row: dict, target_id: int) -> bool:
    if not target_id:
        return False
    context_type = str(row.get("context_type") or "")
    context_id = str(row.get("context_id") or "")
    return (
        int(row.get("id") or 0) == int(target_id)
        or int(row.get("message_id") or 0) == int(target_id)
        or (context_type in {"pulse", "pulse_post", "pulse_reel"} and context_id == str(target_id))
    )


def _needs_playback_transcode(row: dict) -> bool:
    if str(row.get("media_type") or "").lower() != "video":
        return False
    if str(row.get("playback_storage_key") or "").strip():
        return False
    mime_type = str(row.get("mime_type") or "").lower()
    storage_key = str(row.get("storage_key") or row.get("object_key") or row.get("media_url") or "").lower()
    return mime_type in {"video/quicktime", "application/quicktime"} or storage_key.split("?", 1)[0].endswith((".mov", ".qt"))


def _object_to_file(storage_key: str, target: Path) -> None:
    obj = media_storage.get_object(storage_key)
    body = obj.get("Body")
    try:
        with target.open("wb") as fh:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


def _local_source_path(row: dict) -> Path | None:
    local_url = row.get("media_url") or row.get("public_url") or ""
    try:
        path = media_service.local_path_for_url(local_url)
    except Exception:
        path = None
    return Path(path) if path and Path(path).exists() else None


def _playback_key_for(row: dict) -> str:
    source_key = str(row.get("storage_key") or row.get("object_key") or row.get("stored_filename") or "").strip().replace("\\", "/").lstrip("/")
    if not source_key:
        source_key = f"pulse_media/playback/media-{int(row.get('id') or 0)}.mov"
    path = Path(source_key)
    stem = str(path.with_suffix(""))
    return f"{stem}-playback.mp4"


def _transcode_video_to_mp4(source: Path, target: Path) -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not installed")
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        os.getenv("MEDIA_WORKER_FFMPEG_PRESET", "veryfast"),
        "-crf",
        os.getenv("MEDIA_WORKER_FFMPEG_CRF", "23"),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=int(os.getenv("MEDIA_WORKER_TRANSCODE_TIMEOUT_SECONDS", "180")))
    if result.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        error = (result.stderr or result.stdout or "ffmpeg failed").strip().splitlines()[-1:]
        raise RuntimeError((error[0] if error else "ffmpeg failed")[:500])


def _save_local_playback_file(source: Path, playback_key: str) -> str:
    root = Path(bot.webhook_app.root_path, "static", "uploads").resolve()
    target = root / playback_key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"/static/uploads/{playback_key}"


def _mark_video_processing_failed(cur, media_id: int, message: str) -> None:
    error_column = "availability_error" if _table_has_column(cur, "chat_media_uploads", "availability_error") else "moderation_reason"
    cur.execute(
        f"""
        UPDATE chat_media_uploads
        SET processing_status='processing_blocked',
            {error_column}=?,
            error_message=?,
            updated_at=?
        WHERE id=?
        """,
        (str(message or "Video processing failed.")[:1000], str(message or "Video processing failed.")[:1000], _now(), int(media_id)),
    )


def _ensure_video_playback_asset(cur, row: dict) -> bool:
    media_id = int(row.get("id") or 0)
    if not media_id or not _needs_playback_transcode(row):
        return False
    storage_key = str(row.get("storage_key") or row.get("object_key") or "").strip().replace("\\", "/").lstrip("/")
    provider = str(row.get("storage_provider") or "").lower()
    playback_key = _playback_key_for(row)
    logging.info("MEDIA_WORKER_VIDEO_TRANSCODE_START media_id=%s provider=%s source_key=%s playback_key=%s", media_id, provider, storage_key, playback_key)
    with tempfile.TemporaryDirectory(prefix="coinpilotx-media-") as tmp:
        tmp_dir = Path(tmp)
        source_path = tmp_dir / "source.mov"
        output_path = tmp_dir / "playback.mp4"
        if provider in {"r2", "s3"} and storage_key:
            _object_to_file(storage_key, source_path)
        else:
            local = _local_source_path(row)
            if not local:
                raise RuntimeError("Original video file is missing.")
            source_path = local
        _transcode_video_to_mp4(source_path, output_path)
        if provider in {"r2", "s3"}:
            uploaded, upload_error = media_storage._upload_to_object_storage(output_path, playback_key, "video/mp4")
            if not uploaded:
                raise RuntimeError(upload_error or "Playable MP4 upload failed.")
            playback_url = f"/api/pulse/media/{media_id}/stream"
        else:
            playback_url = _save_local_playback_file(output_path, playback_key)
        cur.execute(
            """
            UPDATE chat_media_uploads
            SET playback_url=?, playback_storage_key=?, playback_mime_type='video/mp4',
                processing_status='ready', verification_status='verified',
                is_available=1, availability_error='', error_message='',
                transcoded_at=?, updated_at=?
            WHERE id=?
            """,
            (playback_url, playback_key, _now(), _now(), media_id),
        )
    logging.info("MEDIA_WORKER_VIDEO_TRANSCODE_COMPLETE media_id=%s playback_key=%s", media_id, playback_key)
    return True


def _video_rows_for_target(cur, target_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT *
        FROM chat_media_uploads
        WHERE deleted_at IS NULL AND media_type='video'
          AND (
            (context_type IN ('pulse', 'pulse_post', 'pulse_reel') AND context_id=?)
            OR message_id=?
            OR id=?
          )
        ORDER BY id ASC
        """,
        (str(target_id), int(target_id), int(target_id)),
    )
    return [dict(row) for row in cur.fetchall()]


def process_playback_backlog(limit: int = 2) -> dict:
    if not shutil.which("ffmpeg"):
        return {"checked": 0, "processed": 0, "failed": 0, "status": "ffmpeg_missing"}
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND media_type='video'
          AND COALESCE(playback_storage_key, '')=''
          AND (
            LOWER(COALESCE(mime_type, '')) IN ('video/quicktime', 'application/quicktime')
            OR LOWER(COALESCE(storage_key, object_key, media_url, '')) LIKE '%.mov'
          )
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 2), 5)),),
    )
    rows = [dict(row) for row in cur.fetchall()]
    processed = 0
    failed = 0
    for row in rows:
        try:
            if _ensure_video_playback_asset(cur, row):
                processed += 1
        except Exception as exc:
            failed += 1
            logging.exception("MEDIA_WORKER_VIDEO_BACKLOG_FAILED media_id=%s error=%s", row.get("id"), exc)
            _mark_video_processing_failed(cur, int(row.get("id") or 0), str(exc))
    conn.commit()
    conn.close()
    return {"checked": len(rows), "processed": processed, "failed": failed}


def _fail_or_retry_job(cur, job, error: Exception) -> None:
    attempts = int(job.get("attempts") or 0) + 1
    max_attempts = int(job.get("max_attempts") or MAX_ATTEMPTS)
    status = "failed" if attempts >= max_attempts else "pending"
    run_after = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=min(900, 30 * attempts))).isoformat(timespec="seconds")
    cur.execute(
        "UPDATE pulse_jobs SET status=?, attempts=?, error_message=?, run_after=?, updated_at=? WHERE id=?",
        (status, attempts, str(error)[:1000], run_after, _now(), int(job.get("id") or 0)),
    )


def _process_media_job(cur, job) -> None:
    job_type = str(job.get("job_type") or "")
    target_id = int(job.get("target_id") or 0)
    if job_type not in MEDIA_JOB_TYPES:
        _complete_job(cur, int(job.get("id") or 0), "done")
        return
    if job_type == "process_video" and not shutil.which("ffmpeg"):
        message = "Video processing is blocked because ffmpeg is not installed. Set RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg on Railway."
        logging.warning("MEDIA_ENGINE_VIDEO_PROCESSING_BLOCKED job_id=%s target_id=%s reason=%s", job.get("id"), target_id, message)
        if target_id and _table_has_column(cur, "chat_media_uploads", "processing_status"):
            error_column = "availability_error" if _table_has_column(cur, "chat_media_uploads", "availability_error") else "moderation_reason"
            cur.execute(
                f"""
                UPDATE chat_media_uploads
                SET processing_status='processing_blocked',
                    {error_column}=COALESCE(NULLIF({error_column}, ''), ?)
                WHERE deleted_at IS NULL
                  AND (
                    (context_type IN ('pulse', 'pulse_post', 'pulse_reel') AND context_id=?)
                    OR message_id=?
                    OR id=?
                  )
                """,
                (message, str(target_id), target_id, target_id),
            )
        _complete_job(cur, int(job.get("id") or 0), "pending_unavailable", message)
        return
    if job_type == "process_video" and target_id:
        processed_any = False
        for row in _video_rows_for_target(cur, target_id):
            try:
                processed_any = _ensure_video_playback_asset(cur, row) or processed_any
            except Exception as exc:
                logging.exception("MEDIA_WORKER_VIDEO_PROCESS_FAILED job_id=%s media_id=%s error=%s", job.get("id"), row.get("id"), exc)
                _mark_video_processing_failed(cur, int(row.get("id") or 0), str(exc))
                raise
        if processed_any:
            _complete_job(cur, int(job.get("id") or 0), "done")
            return
    if target_id:
        cur.execute(
            """
            UPDATE chat_media_uploads
            SET moderation_status=COALESCE(NULLIF(moderation_status, ''), 'approved'),
                thumbnail_url=COALESCE(NULLIF(thumbnail_url, ''), media_url)
            WHERE deleted_at IS NULL
              AND (
                (context_type IN ('pulse', 'pulse_post') AND context_id=?)
                OR message_id=?
                OR id=?
              )
            """,
            (str(target_id), target_id, target_id),
        )
    _complete_job(cur, int(job.get("id") or 0), "done")


def process_media_jobs(limit: int = BATCH_SIZE) -> dict:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(MEDIA_JOB_TYPES))
    cur.execute(
        f"""
        SELECT *
        FROM pulse_jobs
        WHERE status='pending'
          AND job_type IN ({placeholders})
          AND (run_after IS NULL OR run_after<=?)
        ORDER BY id ASC
        LIMIT ?
        """,
        [*sorted(MEDIA_JOB_TYPES), _now(), max(1, int(limit or BATCH_SIZE))],
    )
    jobs = [dict(row) for row in cur.fetchall()]
    processed = 0
    failed = 0
    for job in jobs:
        try:
            cur.execute(
                "UPDATE pulse_jobs SET status='processing', attempts=COALESCE(attempts,0)+1, updated_at=? WHERE id=? AND status='pending'",
                (_now(), int(job.get("id") or 0)),
            )
            if cur.rowcount:
                logging.info(
                    "MEDIA_WORKER_JOB_PROCESSING job_id=%s job_type=%s target_id=%s attempts=%s",
                    job.get("id"),
                    job.get("job_type"),
                    job.get("target_id"),
                    int(job.get("attempts") or 0) + 1,
                )
                _process_media_job(cur, job)
                logging.info(
                    "MEDIA_WORKER_JOB_COMPLETE job_id=%s job_type=%s target_id=%s status=done",
                    job.get("id"),
                    job.get("job_type"),
                    job.get("target_id"),
                )
                processed += 1
        except Exception as exc:
            failed += 1
            logging.exception("MEDIA_WORKER_JOB_FAILED job_id=%s error=%s", job.get("id"), exc)
            _fail_or_retry_job(cur, job, exc)
    conn.commit()
    conn.close()
    return {"queued": len(jobs), "processed": processed, "failed": failed}


def run_cycle() -> dict:
    uploads = process_pending_uploads(BATCH_SIZE)
    jobs = process_media_jobs(BATCH_SIZE)
    playback = process_playback_backlog(int(os.getenv("MEDIA_WORKER_PLAYBACK_BACKLOG_BATCH", "2")))
    return {"uploads": uploads, "jobs": jobs, "playback": playback}


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log_boot_diagnostics()
    bot.init_db()
    try:
        bot.record_worker_heartbeat(WORKER_NAME, "booting", metadata=dependency_snapshot())
    except Exception:
        logging.exception("Media worker boot heartbeat failed")
    logging.info(
        "MEDIA_WORKER_START database_url=%s provider=%s batch_size=%s interval=%s",
        bool(os.getenv("DATABASE_URL")),
        os.getenv("MEDIA_STORAGE_PROVIDER", "local"),
        BATCH_SIZE,
        INTERVAL_SECONDS,
    )
    print("CoinPilotX media engine boot complete", flush=True)
    while RUNNING:
        try:
            result = run_cycle()
            result["dependencies"] = dependency_snapshot()
            bot.record_worker_heartbeat(WORKER_NAME, "healthy", metadata=result)
            logging.info("MEDIA_WORKER_CYCLE uploads=%s jobs=%s", result.get("uploads"), result.get("jobs"))
        except Exception as exc:
            logging.exception("MEDIA_WORKER_CYCLE_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc), dependency_snapshot())
            except Exception:
                logging.exception("Media worker heartbeat failed")
        for _ in range(INTERVAL_SECONDS):
            if not RUNNING:
                break
            time.sleep(1)
    logging.info("MEDIA_WORKER_STOPPED")


if __name__ == "__main__":
    main()
