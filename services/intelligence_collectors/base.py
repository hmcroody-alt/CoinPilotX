"""Shared primitives for PulseSoc Intelligence source collectors.

Collectors run outside user request paths. They fetch trusted sources with short
timeouts, normalize candidates, and let the central engine score/dedupe/store.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


_MEMORY_CACHE: dict[str, tuple[float, Any]] = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = low
    return max(low, min(number, high))


def compact(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def source_status(source_key: str, status: str, *, reason: str = "", duration_ms: int = 0, candidates: int = 0) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "status": status,
        "failure_reason": compact(reason, 500),
        "duration_ms": int(duration_ms or 0),
        "candidate_count": int(candidates or 0),
    }


@dataclass
class IntelligenceCandidate:
    stream: str
    source: str
    title: str
    summary: str
    why_it_matters: str
    category: str
    dedupe_key: str
    source_url: str = ""
    source_confidence: float = 0.75
    asset_symbol: str = ""
    region: str = "global"
    severity: str = "normal"
    confidence: float = 0.75
    freshness_score: float = 0.8
    impact_score: float = 0.6
    event_time: str = field(default_factory=utc_now_iso)
    expected_impact: str = ""
    source_keys: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(clamp(data["confidence"]), 4)
        data["source_confidence"] = round(clamp(data["source_confidence"]), 4)
        data["freshness_score"] = round(clamp(data["freshness_score"]), 4)
        data["impact_score"] = round(clamp(data["impact_score"]), 4)
        return data

    def to_engine_payload(self, collector_key: str) -> dict[str, Any]:
        confidence_pct = int(clamp(self.confidence) * 100)
        freshness_pct = int(clamp(self.freshness_score) * 100)
        impact_pct = int(clamp(self.impact_score) * 100)
        source_confidence_pct = int(clamp(self.source_confidence) * 100)
        source_keys = list(dict.fromkeys(self.source_keys or [self.source]))
        metadata = {
            **(self.metadata or {}),
            "normalized_candidate": self.normalized(),
            "source_url": self.source_url,
            "asset_symbol": self.asset_symbol,
            "region": self.region,
        }
        return {
            "stream_key": self.stream,
            "event_type": self.category,
            "headline": self.title,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "expected_impact": self.expected_impact,
            "source_keys": source_keys,
            "evidence": self.evidence,
            "published_at": self.event_time,
            "dedupe_key": self.dedupe_key,
            "collector": collector_key,
            "priority": self.severity,
            "importance_score": impact_pct,
            "freshness_score": freshness_pct,
            "global_impact": impact_pct if self.region in {"global", ""} else int(impact_pct * 0.72),
            "regional_impact": impact_pct if self.region not in {"global", ""} else int(impact_pct * 0.55),
            "duplicate_confidence": max(25, min(92, len(source_keys) * 24)),
            "accuracy_score": source_confidence_pct,
            "spam_probability": 4 if self.severity in {"urgent", "breaking", "high"} else 10,
            "metadata": metadata,
        }


@dataclass
class CollectorResult:
    stream: str
    collector_key: str
    status: str
    candidates: list[IntelligenceCandidate] = field(default_factory=list)
    source_statuses: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    message: str = ""

    def to_dict(self, *, include_candidates: bool = True) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "collector_key": self.collector_key,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "candidate_count": len(self.candidates),
            "source_statuses": self.source_statuses,
            "candidates": [item.normalized() for item in self.candidates] if include_candidates else [],
        }


class BaseCollector:
    stream = "pulsesoc_discoveries"
    collector_key = "base_collector"
    timeout_seconds = 6.0

    def fetch_json(self, url: str, *, cache_key: str, ttl_seconds: int, headers: dict[str, str] | None = None) -> tuple[Any, bool, int]:
        now = time.time()
        cached = _MEMORY_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1], True, 0
        started = time.perf_counter()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PulseSocIntelligenceCollector/2.0 (+https://pulsesoc.com)",
                "Accept": "application/json,text/plain,*/*",
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read(2_000_000)
        duration_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        _MEMORY_CACHE[cache_key] = (now + int(ttl_seconds or 60), data)
        return data, False, duration_ms

    def fetch_text(self, url: str, *, cache_key: str, ttl_seconds: int, headers: dict[str, str] | None = None) -> tuple[str, bool, int]:
        now = time.time()
        cached = _MEMORY_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return str(cached[1] or ""), True, 0
        started = time.perf_counter()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PulseSocIntelligenceCollector/2.0 (+https://pulsesoc.com)",
                "Accept": "application/rss+xml,application/xml,text/xml,text/plain,*/*",
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read(2_000_000)
        duration_ms = int((time.perf_counter() - started) * 1000)
        text = raw.decode("utf-8", errors="replace")
        _MEMORY_CACHE[cache_key] = (now + int(ttl_seconds or 300), text)
        return text, False, duration_ms

    def failure(self, source_key: str, exc: Exception, started: float, candidates: list[IntelligenceCandidate] | None = None) -> CollectorResult:
        duration_ms = int((time.perf_counter() - started) * 1000)
        reason = str(exc)
        status = "config_missing" if reason == "config_missing" else "failed"
        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status=status,
            candidates=candidates or [],
            duration_ms=duration_ms,
            message=compact(reason, 500),
            source_statuses=[source_status(source_key, status, reason=reason, duration_ms=duration_ms, candidates=len(candidates or []))],
        )

    def run(self, limit: int = 20) -> CollectorResult:
        raise NotImplementedError


def network_error_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return compact(getattr(exc, "reason", exc), 200)
    return compact(exc, 200)
