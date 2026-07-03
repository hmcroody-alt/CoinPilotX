"""World Pulse collector for official emergency and science sources."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, network_error_message, safe_float, source_status, utc_now_iso


class WorldPulseCollector(BaseCollector):
    stream = "world_pulse"
    collector_key = "world_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        candidates: list[IntelligenceCandidate] = []
        statuses: list[dict[str, Any]] = []
        try:
            data, cached, duration = self.fetch_json(
                "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson",
                cache_key="usgs_significant_day",
                ttl_seconds=180,
            )
            features = data.get("features") if isinstance(data, dict) else []
            features = features if isinstance(features, list) else []
            statuses.append(source_status("usgs", "success_cached" if cached else "success", duration_ms=duration, candidates=len(features)))
            for item in features:
                if not isinstance(item, dict):
                    continue
                props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
                mag = safe_float(props.get("mag"))
                if mag < 5.5:
                    continue
                place = compact(props.get("place"), 140)
                url = compact(props.get("url"), 300)
                severity = "urgent" if mag >= 7.0 else "high"
                candidates.append(IntelligenceCandidate(
                    stream=self.stream,
                    source="usgs",
                    source_keys=["usgs"],
                    source_url=url or "https://earthquake.usgs.gov/",
                    source_confidence=0.92,
                    title=f"Magnitude {mag:.1f} earthquake reported near {place}",
                    summary=f"USGS reported a magnitude {mag:.1f} earthquake near {place}.",
                    why_it_matters="Official earthquake intelligence can matter for safety, travel, infrastructure, and regional awareness.",
                    expected_impact="Aftershock, infrastructure, and travel updates may follow from official agencies.",
                    category="earthquake",
                    region="regional",
                    severity=severity,
                    confidence=0.88 if severity == "high" else 0.93,
                    freshness_score=0.93,
                    impact_score=min(0.98, mag / 8),
                    dedupe_key=f"world:usgs:{props.get('code') or props.get('ids') or place}:{utc_now_iso()[:10]}",
                    event_time=utc_now_iso(),
                    evidence=[{"source": "usgs", "magnitude": mag, "place": place, "url": url}],
                    metadata={"source_event_id": props.get("code") or props.get("ids")},
                ))
        except Exception as exc:
            statuses.append(source_status("usgs", "failed", reason=network_error_message(exc)))

        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success" if any(item["status"].startswith("success") for item in statuses) else "failed",
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="" if candidates else "No major World Pulse event exceeded filters.",
        )
