"""Canonical Pulse Live session model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_STATES = {"idle", "camera_ready", "publishing", "live", "reconnecting", "ended", "failed", "archived"}


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value or fallback)
    except Exception:
        return fallback


@dataclass
class LiveSession:
    id: int = 0
    creator_id: int = 0
    title: str = "Pulse Live"
    description: str = ""
    thumbnail: str = ""
    status: str = "idle"
    visibility: str = "public"
    started_at: str = ""
    ended_at: str = ""
    stream_key: str = ""
    viewer_count: int = 0
    peak_viewers: int = 0
    replay_asset_id: int = 0
    replay_url: str = ""
    chat_room_id: str = ""
    engagement_score: int = 0
    destinations: list[dict] = field(default_factory=list)
    recording_status: str = "pending"
    playback_url: str = ""
    webrtc_room_id: str = ""
    feed_post_id: int = 0

    @classmethod
    def from_row(cls, row: dict | None, destinations: list[dict] | None = None) -> "LiveSession":
        item = dict(row or {})
        status = item.get("publish_state") or item.get("status") or "idle"
        if status not in VALID_STATES:
            status = "live" if status in {"starting", "started"} else "idle"
        return cls(
            id=_int(item.get("id")),
            creator_id=_int(item.get("user_id") or item.get("creator_id")),
            title=item.get("title") or "Pulse Live",
            description=item.get("description") or item.get("body") or "",
            thumbnail=item.get("preview_url") or item.get("thumbnail_url") or "",
            status=status,
            visibility=item.get("audience") or item.get("visibility") or "public",
            started_at=item.get("started_at") or item.get("created_at") or "",
            ended_at=item.get("ended_at") or "",
            stream_key=item.get("stream_key") or "",
            viewer_count=_int(item.get("viewer_count")),
            peak_viewers=_int(item.get("peak_viewers") or item.get("viewer_count")),
            replay_asset_id=_int(item.get("replay_asset_id")),
            replay_url=item.get("replay_url") or "",
            chat_room_id=item.get("chat_room_id") or "",
            engagement_score=_int(item.get("engagement_score")),
            destinations=destinations or [],
            recording_status=item.get("recording_status") or ("recording" if status == "live" else "ready" if item.get("ended_at") else "pending"),
            playback_url=item.get("playback_url") or item.get("hls_url") or "",
            webrtc_room_id=item.get("webrtc_room_id") or "",
            feed_post_id=_int(item.get("feed_post_id")),
        )

    def public_payload(self) -> dict:
        return {
            "id": self.id,
            "creator_id": self.creator_id,
            "title": self.title,
            "description": self.description,
            "thumbnail": self.thumbnail,
            "status": self.status,
            "visibility": self.visibility,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "viewer_count": self.viewer_count,
            "peak_viewers": self.peak_viewers,
            "replay_asset_id": self.replay_asset_id,
            "replay_url": self.replay_url,
            "chat_room_id": self.chat_room_id,
            "engagement_score": self.engagement_score,
            "destinations": self.destinations,
            "recording_status": self.recording_status,
            "playback_url": self.playback_url,
            "webrtc_room_id": self.webrtc_room_id,
            "feed_post_id": self.feed_post_id,
        }
