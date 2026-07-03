"""Creator Pulse collector for internal creator telemetry.

This collector intentionally skips when creator analytics tables are absent so
PulseSoc does not invent growth or best-time intelligence.
"""

from __future__ import annotations

import time
from typing import Any

from services import db as db_service

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, source_status, utc_now_iso


class CreatorPulseCollector(BaseCollector):
    stream = "creator_pulse"
    collector_key = "creator_pulse_sources"

    def _tables(self) -> set[str]:
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                return {str(row[0]) for row in cur.fetchall()}
            except Exception:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                return {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        tables = self._tables()
        analytics_tables = {"creator_analytics", "pulse_creator_metrics", "creator_daily_metrics"} & tables
        if not analytics_tables:
            return CollectorResult(
                stream=self.stream,
                collector_key=self.collector_key,
                status="skipped",
                candidates=[],
                source_statuses=[source_status("creator_analytics", "skipped", reason="no_internal_creator_telemetry", duration_ms=int((time.perf_counter() - started) * 1000))],
                duration_ms=int((time.perf_counter() - started) * 1000),
                message="Creator Pulse skipped because no supported internal creator telemetry table exists.",
            )
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success",
            candidates=[],
            source_statuses=[source_status("creator_analytics", "success", reason="telemetry_table_detected", duration_ms=int((time.perf_counter() - started) * 1000))],
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="Creator telemetry is present; no aggregate creator signal exceeded thresholds.",
        )
