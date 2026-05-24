"""Post-live replay sharing workflow helpers."""

from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def create_post_live_options(cur, *, live_id: int, replay_url: str = "", selected=None) -> list[dict]:
    selected = selected or ["post_as_pulse_video", "share_replay_link"]
    now = now_iso()
    created = []
    for action in selected:
        platform = "pulse" if "pulse" in action else "link"
        status = "ready" if replay_url else "waiting_for_replay"
        cur.execute(
            """
            INSERT INTO pulse_live_archive_shares
            (live_id, platform, action, status, target_url, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """,
            (int(live_id), platform, str(action or "")[:80], status, replay_url or "", now, now),
        )
        created.append({"action": action, "platform": platform, "status": status, "target_url": replay_url or ""})
    return created


def public_share_options(cur, *, live_id: int) -> list[dict]:
    cur.execute(
        "SELECT platform, action, status, target_url, error_message, updated_at FROM pulse_live_archive_shares WHERE live_id=? ORDER BY id",
        (int(live_id),),
    )
    return [dict(row) for row in cur.fetchall()]
