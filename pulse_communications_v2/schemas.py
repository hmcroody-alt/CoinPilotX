"""Small schema helpers for inactive Pulse Communications 2.0 services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    status: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"ok": self.ok, "status": self.status}
        if self.message:
            payload["message"] = self.message
        if self.data:
            payload.update(self.data)
        return payload


@dataclass(frozen=True)
class ConversationCreate:
    conversation_type: str = "direct"
    title: str = ""
    participant_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class MessageCreate:
    conversation_id: int = 0
    body: str = ""
    message_type: str = "text"
    media_id: int = 0


@dataclass(frozen=True)
class CommunityCreate:
    name: str = ""
    privacy: str = "public"


@dataclass(frozen=True)
class ChannelCreate:
    community_id: int = 0
    name: str = ""
    channel_type: str = "text"
