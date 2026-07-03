"""Internal PulseSoc collectors for feature, discovery, and release signals."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, source_status, utc_now_iso


ROOT = Path(__file__).resolve().parents[2]
FEATURE_MAP = ROOT / "data" / "pulse_ai" / "pulsesoc_feature_map.json"
REPORTS_DIR = ROOT / "reports"


class PulseSocDiscoveryCollector(BaseCollector):
    stream = "pulsesoc_discoveries"
    collector_key = "pulsesoc_discovery_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        candidates: list[IntelligenceCandidate] = []
        status = "success"
        message = ""
        try:
            features = json.loads(FEATURE_MAP.read_text(encoding="utf-8"))
            features = features if isinstance(features, list) else []
        except Exception as exc:
            features = []
            status = "failed"
            message = compact(exc, 300)
        for feature in features:
            if not isinstance(feature, dict):
                continue
            feature_id = compact(feature.get("id"), 90)
            name = compact(feature.get("name"), 120)
            if not feature_id or not name:
                continue
            if feature.get("status") not in {"active", "available", None, ""}:
                continue
            if feature_id not in {"pulse_ai.chat", "intelligence.center", "messenger.calls.video", "messenger.calls.audio", "status.creator", "app_store.download_share"}:
                continue
            user_help = compact(feature.get("user_help") or feature.get("description"), 400)
            entry_points = feature.get("entry_points") if isinstance(feature.get("entry_points"), list) else []
            deep_link = "/pulse/intelligence" if "intelligence" in feature_id else "/pulse/messages" if "messenger" in feature_id or feature_id == "pulse_ai.chat" else "/pulse" if feature_id == "app_store.download_share" else "/pulse/status"
            candidates.append(IntelligenceCandidate(
                stream=self.stream,
                source="pulsesoc_feature_registry",
                source_keys=["pulsesoc_feature_registry"],
                source_url=deep_link,
                source_confidence=0.92,
                title=f"Try {name} inside PulseSoc",
                summary=user_help or f"{name} is available in PulseSoc.",
                why_it_matters="PulseSoc Discoveries help users find real features without repeated onboarding popups.",
                expected_impact="Users can explore the feature when it is relevant, or keep the stream in digest mode.",
                category="platform_discovery",
                region="global",
                severity="normal",
                confidence=0.8,
                freshness_score=0.78,
                impact_score=0.58,
                dedupe_key=f"pulsesoc_discovery:{feature_id}",
                event_time=utc_now_iso(),
                evidence=[{"source": "pulsesoc_feature_registry", "feature_id": feature_id, "entry_points": entry_points}],
                metadata={
                    "deep_link": deep_link,
                    "feature_id": feature_id,
                    "actions": [
                        {"label": "Try It", "type": "deep_link", "url": deep_link, "style": "primary", "icon": "spark"},
                        {"label": "Invite Friends", "type": "share", "style": "secondary", "icon": "share"},
                    ],
                },
            ))
            if len(candidates) >= int(limit or 20):
                break
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status=status,
            candidates=candidates,
            source_statuses=[source_status("pulsesoc_feature_registry", status, reason=message, duration_ms=int((time.perf_counter() - started) * 1000), candidates=len(candidates))],
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=message,
        )


class PulseSocPulseCollector(BaseCollector):
    stream = "pulsesoc_pulse"
    collector_key = "pulsesoc_platform_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        candidates: list[IntelligenceCandidate] = []
        report_files = []
        if REPORTS_DIR.exists():
            report_files = sorted(
                [path for path in REPORTS_DIR.glob("*.md") if any(token in path.name for token in ("pulse_ai", "pulsesoc_intelligence", "call", "status_ui", "app_store"))],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[: int(limit or 20)]
        for path in report_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            first_heading = ""
            for line in text.splitlines():
                if line.strip().startswith("#"):
                    first_heading = compact(line.lstrip("# ").strip(), 160)
                    break
            title = first_heading or path.stem.replace("_", " ").title()
            lower = path.name.lower()
            deep_link = "/pulse/intelligence"
            if "call" in lower:
                deep_link = "/pulse/messages"
            elif "status" in lower:
                deep_link = "/pulse/status"
            elif "app_store" in lower:
                deep_link = "/pulse"
            event_type = "app_update" if "app_store" in lower else "feature_update"
            candidates.append(IntelligenceCandidate(
                stream=self.stream,
                source="pulsesoc_telemetry",
                source_keys=["pulsesoc_telemetry"],
                source_url=deep_link,
                source_confidence=0.9,
                title=title,
                summary=f"PulseSoc recorded a platform update from {path.name}.",
                why_it_matters="PulseSoc Pulse keeps users aware of real platform improvements and rollout notes.",
                expected_impact="Users may see improved feature behavior or new entry points as the rollout reaches them.",
                category=event_type,
                region="global",
                severity="normal",
                confidence=0.82,
                freshness_score=0.82,
                impact_score=0.62,
                dedupe_key=f"pulsesoc_pulse:{path.name}:{int(path.stat().st_mtime)}",
                event_time=utc_now_iso(),
                evidence=[{"source": "local_report", "path": f"reports/{path.name}"}],
                metadata={"deep_link": deep_link},
            ))
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success",
            candidates=candidates[: int(limit or 20)],
            source_statuses=[source_status("pulsesoc_telemetry", "success", duration_ms=int((time.perf_counter() - started) * 1000), candidates=len(candidates))],
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="" if candidates else "No recent PulseSoc report-backed platform signals found.",
        )
