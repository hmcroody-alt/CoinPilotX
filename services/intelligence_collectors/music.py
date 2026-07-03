"""Music Pulse collector for internal PulseSoc Music telemetry."""

from __future__ import annotations

import time

from services import db as db_service

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, source_status, utc_now_iso


def _cell(row, key, default=None):
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


class MusicPulseCollector(BaseCollector):
    stream = "music_pulse"
    collector_key = "music_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                tables = {str(row[0]) for row in cur.fetchall()}
            except Exception:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {str(row[0]) for row in cur.fetchall()}
            table = next((name for name in ("pulse_music_tracks", "music_tracks", "pulse_sounds") if name in tables), "")
            if not table:
                return CollectorResult(
                    stream=self.stream,
                    collector_key=self.collector_key,
                    status="skipped",
                    candidates=[],
                    source_statuses=[source_status("pulse_music", "skipped", reason="no_internal_music_telemetry", duration_ms=int((time.perf_counter() - started) * 1000))],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    message="Music Pulse skipped because no supported PulseSoc Music telemetry table exists.",
                )
            cur.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (int(limit or 20),))
            rows = cur.fetchall()
            candidates: list[IntelligenceCandidate] = []
            for row in rows[: int(limit or 20)]:
                title = compact(_cell(row, "title") or _cell(row, "name") or "PulseSoc sound", 120)
                artist = compact(_cell(row, "artist") or _cell(row, "creator_name") or "PulseSoc Music", 100)
                candidates.append(IntelligenceCandidate(
                    stream=self.stream,
                    source="pulse_music",
                    source_keys=["pulse_music"],
                    source_url="/pulse/music",
                    source_confidence=0.82,
                    title=f"{title} is available in PulseSoc Music",
                    summary=f"{artist} has a PulseSoc Music item available for Status, Reels, or discovery.",
                    why_it_matters="Music Pulse helps users discover real sounds already available inside PulseSoc.",
                    expected_impact="Popular sounds can improve Status and Reels atmosphere when creators use them intentionally.",
                    category="music_release",
                    severity="low",
                    confidence=0.72,
                    freshness_score=0.72,
                    impact_score=0.52,
                    dedupe_key=f"music:{table}:{_cell(row, 'id', title)}",
                    event_time=utc_now_iso(),
                    evidence=[{"source": "pulse_music", "table": table, "id": _cell(row, "id")}],
                    metadata={"deep_link": "/pulse/music"},
                ))
            return CollectorResult(
                stream=self.stream,
                collector_key=self.collector_key,
                status="success",
                candidates=candidates,
                source_statuses=[source_status("pulse_music", "success", duration_ms=int((time.perf_counter() - started) * 1000), candidates=len(candidates))],
                duration_ms=int((time.perf_counter() - started) * 1000),
                message="" if candidates else "No PulseSoc Music item exceeded thresholds.",
            )
        finally:
            conn.close()
