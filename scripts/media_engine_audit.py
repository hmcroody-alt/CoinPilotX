#!/usr/bin/env python3
"""Audit the CoinPilotX media worker boot surface and observability."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utc_parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def main() -> int:
    import bot

    failures: list[str] = []
    warnings: list[str] = []
    lines: list[str] = []

    worker_file = ROOT / "media_worker.py"
    lines.append(f"media_worker.py exists: {worker_file.exists()}")
    if not worker_file.exists():
        failures.append("media_worker.py is missing.")

    lines.append("Railway worker start command: python media_worker.py")
    ffmpeg_path = shutil.which("ffmpeg")
    lines.append(f"ffmpeg available: {bool(ffmpeg_path)}{f' ({ffmpeg_path})' if ffmpeg_path else ''}")
    if not ffmpeg_path:
        warnings.append("ffmpeg is not available locally. Railway should install it through nixpacks.toml.")

    provider = os.getenv("MEDIA_STORAGE_PROVIDER", "local").strip().lower() or "local"
    env_status = {
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "REDIS_URL": bool(os.getenv("REDIS_URL")),
        "MEDIA_STORAGE_PROVIDER": provider,
        "R2_BUCKET": bool(os.getenv("R2_BUCKET")),
        "R2_PUBLIC_BASE_URL": bool(os.getenv("R2_PUBLIC_BASE_URL")),
    }
    lines.append("environment: " + json.dumps(env_status, sort_keys=True))
    if provider in {"r2", "s3"} and not (env_status["R2_BUCKET"] and env_status["R2_PUBLIC_BASE_URL"]):
        failures.append("R2/S3 storage selected but R2_BUCKET or R2_PUBLIC_BASE_URL is missing.")

    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()

    for table in ("worker_heartbeats", "pulse_jobs", "chat_media_uploads"):
        exists = _table_exists(cur, table)
        lines.append(f"table {table}: {exists}")
        if not exists:
            failures.append(f"Required table {table} is missing.")

    failed_media_jobs = 0
    if _table_exists(cur, "pulse_jobs"):
        cur.execute(
            "SELECT COUNT(*) AS total FROM pulse_jobs WHERE job_type IN ('generate_thumbnail','process_video') AND status='failed'"
        )
        failed_media_jobs = int(dict(cur.fetchone() or {}).get("total") or 0)
    lines.append(f"failed media jobs: {failed_media_jobs}")
    if failed_media_jobs:
        warnings.append(f"{failed_media_jobs} failed media job(s) need review.")

    if _table_exists(cur, "worker_heartbeats"):
        cur.execute(
            """
            SELECT *
            FROM worker_heartbeats
            WHERE worker_name IN ('coinpilotx-media-engine','media_worker')
            ORDER BY last_seen_at DESC
            LIMIT 1
            """
        )
        heartbeat = dict(cur.fetchone() or {})
        if heartbeat:
            last_seen = _utc_parse(heartbeat.get("last_seen_at"))
            age_seconds = int((datetime.now(timezone.utc).replace(tzinfo=None) - last_seen).total_seconds()) if last_seen else None
            lines.append(
                "latest media heartbeat: "
                + json.dumps(
                    {
                        "worker_name": heartbeat.get("worker_name"),
                        "status": heartbeat.get("status"),
                        "last_seen_at": heartbeat.get("last_seen_at"),
                        "age_seconds": age_seconds,
                        "last_error": heartbeat.get("last_error"),
                    },
                    sort_keys=True,
                )
            )
            if age_seconds is not None and age_seconds > 180:
                warnings.append("Latest media engine heartbeat is older than 3 minutes.")
        else:
            warnings.append("No media engine heartbeat has been recorded yet.")

    conn.close()

    print("CoinPilotX media engine audit")
    print("=" * 34)
    for line in lines:
        print(line)
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")
    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nPASS: media engine foundation is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
