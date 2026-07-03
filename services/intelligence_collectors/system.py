"""System Pulse collector for explicit PulseSoc system events."""

from __future__ import annotations

import os
import time

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, source_status, utc_now_iso


class SystemPulseCollector(BaseCollector):
    stream = "system_pulse"
    collector_key = "system_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        message = compact(os.getenv("PULSESOC_SYSTEM_PULSE_MESSAGE") or "", 500)
        title = compact(os.getenv("PULSESOC_SYSTEM_PULSE_TITLE") or "PulseSoc system update", 180)
        event_type = compact(os.getenv("PULSESOC_SYSTEM_PULSE_TYPE") or "system_update", 60)
        if not message:
            return CollectorResult(
                stream=self.stream,
                collector_key=self.collector_key,
                status="skipped",
                candidates=[],
                source_statuses=[source_status("pulsesoc_system", "skipped", reason="no_explicit_system_event", duration_ms=int((time.perf_counter() - started) * 1000))],
                duration_ms=int((time.perf_counter() - started) * 1000),
                message="System Pulse skipped because no explicit system event env was configured.",
            )
        candidate = IntelligenceCandidate(
            stream=self.stream,
            source="pulsesoc_system",
            source_keys=["pulsesoc_system"],
            source_url="/pulse/intelligence",
            source_confidence=0.92,
            title=title,
            summary=message,
            why_it_matters="System Pulse is reserved for real app updates, maintenance, incidents, and rollout notices.",
            expected_impact=compact(os.getenv("PULSESOC_SYSTEM_PULSE_IMPACT") or "Users may need to open PulseSoc or review the update.", 500),
            category=event_type,
            region="global",
            severity=compact(os.getenv("PULSESOC_SYSTEM_PULSE_PRIORITY") or "high", 20),
            confidence=0.9,
            freshness_score=0.94,
            impact_score=0.72,
            dedupe_key=f"system:{event_type}:{title.lower()}:{utc_now_iso()[:10]}",
            event_time=utc_now_iso(),
            evidence=[{"source": "pulsesoc_system", "configured": True}],
            metadata={"deep_link": "/pulse/intelligence"},
        )
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success",
            candidates=[candidate][: int(limit or 20)],
            source_statuses=[source_status("pulsesoc_system", "success", duration_ms=int((time.perf_counter() - started) * 1000), candidates=1)],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
