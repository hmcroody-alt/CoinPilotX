"""Lightweight realtime event broker for Arena polling/SSE fallbacks.

This module is intentionally dependency-free so production can boot even when
websocket infrastructure is not installed yet. It gives the app one place to
publish live Arena events today and can sit behind Socket.IO/Redis later.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4


@dataclass
class RealtimeEvent:
    id: int
    channel: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class RealtimeConnection:
    connection_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    channels: set[str] = field(default_factory=set)
    last_seen_at: datetime = field(default_factory=datetime.utcnow)


class RealtimeChannelManager:
    def __init__(self, max_events_per_channel: int = 200) -> None:
        self.max_events_per_channel = max_events_per_channel
        self._lock = RLock()
        self._event_id = 0
        self._channels: dict[str, deque[RealtimeEvent]] = defaultdict(
            lambda: deque(maxlen=self.max_events_per_channel)
        )
        self._connections: dict[str, RealtimeConnection] = {}

    def subscribe(self, channel: str, connection_id: str | None = None, metadata: dict[str, Any] | None = None) -> str:
        with self._lock:
            cid = connection_id or uuid4().hex
            connection = self._connections.get(cid) or RealtimeConnection(cid)
            connection.metadata.update(metadata or {})
            connection.channels.add(channel)
            connection.last_seen_at = datetime.utcnow()
            self._connections[cid] = connection
            return cid

    def unsubscribe(self, channel: str, connection_id: str) -> None:
        with self._lock:
            connection = self._connections.get(connection_id)
            if not connection:
                return
            connection.channels.discard(channel)
            if not connection.channels:
                self._connections.pop(connection_id, None)

    def heartbeat(self, connection_id: str, channels: list[str] | None = None, metadata: dict[str, Any] | None = None) -> None:
        with self._lock:
            connection = self._connections.get(connection_id) or RealtimeConnection(connection_id)
            if channels:
                connection.channels.update(channels)
            if metadata:
                connection.metadata.update(metadata)
            connection.last_seen_at = datetime.utcnow()
            self._connections[connection_id] = connection

    def publish(self, channel: str, event_type: str, payload: dict[str, Any] | None = None) -> RealtimeEvent:
        with self._lock:
            self._event_id += 1
            event = RealtimeEvent(
                id=self._event_id,
                channel=channel,
                event_type=event_type,
                payload=payload or {},
                created_at=datetime.utcnow().isoformat(),
            )
            self._channels[channel].append(event)
            return event

    def poll(self, channel: str, after_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            events = [
                event
                for event in self._channels.get(channel, [])
                if int(event.id) > int(after_id or 0)
            ]
            return [self._serialize(event) for event in events[-max(1, min(int(limit or 50), 100)):]]

    def presence_snapshot(self, max_age_seconds: int = 90) -> list[dict[str, Any]]:
        cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
        with self._lock:
            return [
                {
                    "connection_id": connection.connection_id,
                    "channels": sorted(connection.channels),
                    "metadata": connection.metadata,
                    "last_seen_at": connection.last_seen_at.isoformat(),
                }
                for connection in self._connections.values()
                if connection.last_seen_at >= cutoff
            ]

    @staticmethod
    def _serialize(event: RealtimeEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "channel": event.channel,
            "type": event.event_type,
            "payload": event.payload,
            "created_at": event.created_at,
        }


realtime_manager = RealtimeChannelManager()


def arena_channel(kind: str, identifier: str | int) -> str:
    clean_kind = "".join(ch for ch in str(kind) if ch.isalnum() or ch in ("_", "-"))[:40] or "arena"
    clean_identifier = "".join(ch for ch in str(identifier) if ch.isalnum() or ch in ("_", "-"))[:80] or "global"
    return f"arena:{clean_kind}:{clean_identifier}"


def publish_arena_event(kind: str, identifier: str | int, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return realtime_manager._serialize(
        realtime_manager.publish(arena_channel(kind, identifier), event_type, payload or {})
    )


def poll_arena_channel(kind: str, identifier: str | int, after_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    return realtime_manager.poll(arena_channel(kind, identifier), after_id=after_id, limit=limit)
