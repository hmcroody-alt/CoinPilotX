"""Multi-destination restream orchestration for Pulse Live."""

from __future__ import annotations

from datetime import datetime
import logging

from . import live_destination_service


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def normalize_destinations(destinations) -> list[dict]:
    if not isinstance(destinations, list):
        destinations = ["pulse"]
    normalized: list[dict] = []
    seen = set()
    for item in destinations:
        if isinstance(item, str):
            data = {"platform": item}
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue
        platform = live_destination_service.normalize_platform(data.get("platform") or data.get("id") or "pulse")
        if platform in seen:
            continue
        seen.add(platform)
        normalized.append({
            "platform": platform,
            "label": data.get("label") or platform.replace("_", " ").title(),
            "rtmp_url": data.get("rtmp_url") or data.get("url") or "",
            "stream_key": data.get("stream_key") or "",
        })
    if "pulse" not in seen:
        normalized.insert(0, {"platform": "pulse", "label": "Pulse Live", "rtmp_url": "", "stream_key": ""})
    return normalized


def prepare_restream_targets(cur, *, live_id: int, user_id: int, destinations=None, custom_rtmp_url: str = "", custom_stream_key: str = "") -> list[dict]:
    """Persist per-platform live target state without ever exposing secrets."""
    now = now_iso()
    targets = []
    for destination in normalize_destinations(destinations):
        try:
            platform = destination["platform"]
            rtmp_url = destination.get("rtmp_url") or ""
            stream_key = destination.get("stream_key") or ""
            if platform == "custom_rtmp" and custom_rtmp_url:
                rtmp_url = custom_rtmp_url
                stream_key = custom_stream_key or stream_key
            status = "live" if platform == "pulse" else "connecting"
            error = ""
            if platform in {"facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin"} and not stream_key and not rtmp_url:
                status = "failed"
                error = "Destination is selected but no OAuth or stream key is configured."
            if platform == "custom_rtmp":
                valid, error = live_destination_service.validate_rtmp_url(rtmp_url)
                status = "connecting" if valid else "failed"
            destination_id = 0
            if platform != "pulse":
                destination_id = live_destination_service.upsert_destination(
                    cur,
                    user_id=user_id,
                    platform=platform,
                    label=destination.get("label") or platform,
                    rtmp_url=rtmp_url,
                    stream_key=stream_key,
                )
            cur.execute(
                """
                INSERT INTO pulse_live_restream_targets
                (live_id, destination_id, platform, status, last_error, retry_count, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (int(live_id), int(destination_id), platform, status, error, now, now),
            )
            targets.append({
                "platform": platform,
                "status": status,
                "destination_id": destination_id,
                "message": error or ("Pulse Live is primary." if platform == "pulse" else "Connection is queued."),
            })
        except Exception as exc:
            platform = live_destination_service.normalize_platform(destination.get("platform") if isinstance(destination, dict) else destination)
            logging.exception("PULSE_LIVE_RESTREAM_TARGET_FAILED live_id=%s user_id=%s platform=%s", live_id, user_id, platform)
            try:
                cur.execute(
                    """
                    INSERT INTO pulse_live_restream_targets
                    (live_id, destination_id, platform, status, last_error, retry_count, started_at, updated_at)
                    VALUES (?, 0, ?, 'failed', ?, 0, ?, ?)
                    """,
                    (int(live_id), platform, str(exc)[:500], now, now),
                )
            except Exception:
                logging.exception("PULSE_LIVE_RESTREAM_FAILURE_LOG_FAILED live_id=%s platform=%s", live_id, platform)
            targets.append({"platform": platform, "status": "failed", "destination_id": 0, "message": str(exc)[:180]})
    return targets


def mark_targets_ended(cur, *, live_id: int) -> None:
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_live_restream_targets
        SET status=CASE WHEN status='failed' THEN status ELSE 'ended' END, updated_at=?
        WHERE live_id=?
        """,
        (now, int(live_id)),
    )


def destination_statuses(cur, *, live_id: int) -> list[dict]:
    cur.execute(
        "SELECT platform, status, last_error, retry_count, updated_at FROM pulse_live_restream_targets WHERE live_id=? ORDER BY id",
        (int(live_id),),
    )
    return [dict(row) for row in cur.fetchall()]
