"""PulseSoc Intelligence collector registry and runner."""

from __future__ import annotations

import time
from typing import Any

from services import pulsesoc_intelligence_engine as engine

from .base import CollectorResult, IntelligenceCandidate
from .creator import CreatorPulseCollector
from .crypto import CryptoPulseCollector
from .markets import MarketPulseCollector
from .music import MusicPulseCollector
from .pulsesoc import PulseSocDiscoveryCollector, PulseSocPulseCollector
from .security import SecurityPulseCollector
from .system import SystemPulseCollector
from .technology import TechnologyPulseCollector
from .world import WorldPulseCollector


COLLECTOR_CLASSES = {
    "crypto_pulse": CryptoPulseCollector,
    "market_pulse": MarketPulseCollector,
    "world_pulse": WorldPulseCollector,
    "security_pulse": SecurityPulseCollector,
    "technology_pulse": TechnologyPulseCollector,
    "pulsesoc_discoveries": PulseSocDiscoveryCollector,
    "pulsesoc_pulse": PulseSocPulseCollector,
    "creator_pulse": CreatorPulseCollector,
    "music_pulse": MusicPulseCollector,
    "system_pulse": SystemPulseCollector,
}


def collector_keys() -> list[str]:
    return list(COLLECTOR_CLASSES)


def _json(value: Any) -> str:
    return engine._json_dumps(value)  # Internal collector package shares engine serialization.


def _update_source_statuses(statuses: list[dict[str, Any]]) -> None:
    if not statuses:
        return
    conn = engine.connect()
    try:
        engine.ensure_schema(conn)
        cur = conn.cursor()
        now = engine.now_iso()
        for status in statuses:
            source_key = engine._slug(status.get("source_key") or "")
            if not source_key:
                continue
            state = status.get("status") or "unknown"
            failure = status.get("failure_reason") or ""
            fields = ["status=?", "updated_at=?"]
            values: list[Any] = [state, now]
            if state.startswith("success"):
                fields.append("last_success_at=?")
                values.append(now)
                fields.append("failure_reason=?")
                values.append("")
            elif state in {"failed", "config_missing"}:
                fields.append("last_failure_at=?")
                values.append(now)
                fields.append("failure_reason=?")
                values.append(failure)
            values.append(source_key)
            cur.execute(f"UPDATE intelligence_sources SET {', '.join(fields)} WHERE source_key=?", tuple(values))
        conn.commit()
    finally:
        conn.close()


def _record_run(result: CollectorResult, *, dry_run: bool, ingest_results: list[dict[str, Any]], deliver: bool, target_user_id: int) -> int:
    conn = engine.connect()
    try:
        engine.ensure_schema(conn)
        cur = conn.cursor()
        accepted = len([item for item in ingest_results if item.get("ok") and item.get("status") == "accepted"])
        failure_reason = "" if result.status in {"success", "skipped"} else result.message
        cur.execute(
            """
            INSERT INTO intelligence_collector_runs
            (collector_key, stream_key, status, started_at, finished_at, duration_ms,
             events_seen, events_accepted, failure_reason, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.collector_key,
                result.stream,
                "dry_run" if dry_run and result.status == "success" else result.status,
                engine.now_iso(),
                engine.now_iso(),
                result.duration_ms,
                len(result.candidates),
                accepted,
                failure_reason,
                _json({
                    "dry_run": dry_run,
                    "deliver": deliver,
                    "target_user_id": target_user_id,
                    "message": result.message,
                    "source_statuses": result.source_statuses,
                    "ingest_results": ingest_results[:20],
                }),
            ),
        )
        run_id = int(getattr(cur, "lastrowid", 0) or 0)
        conn.commit()
        return run_id
    finally:
        conn.close()


def run_collectors(
    *,
    stream_key: str = "",
    all_streams: bool = False,
    dry_run: bool = True,
    limit: int = 20,
    deliver: bool = False,
    target_user_id: int = 0,
) -> dict[str, Any]:
    started = time.perf_counter()
    keys = collector_keys() if all_streams or not stream_key else [engine._slug(stream_key)]
    runs: list[dict[str, Any]] = []
    for key in keys:
        collector_cls = COLLECTOR_CLASSES.get(key)
        if not collector_cls:
            runs.append({"stream": key, "status": "invalid_stream", "message": "No collector registered for stream."})
            continue
        collector = collector_cls()
        try:
            result = collector.run(limit=limit)
        except Exception as exc:
            result = CollectorResult(
                stream=key,
                collector_key=f"{key}_collector",
                status="failed",
                message=str(exc),
            )
        _update_source_statuses(result.source_statuses)
        ingest_results: list[dict[str, Any]] = []
        if not dry_run and result.status == "success":
            for candidate in result.candidates[: int(limit or 20)]:
                payload = candidate.to_engine_payload(result.collector_key)
                ingest_results.append(engine.ingest_signal(payload, deliver=deliver, target_user_id=target_user_id))
        run_id = _record_run(result, dry_run=dry_run, ingest_results=ingest_results, deliver=deliver, target_user_id=target_user_id)
        run_payload = result.to_dict(include_candidates=True)
        run_payload["collector_run_id"] = run_id
        run_payload["ingest_results"] = ingest_results
        runs.append(run_payload)
    accepted = sum(1 for run in runs for item in run.get("ingest_results", []) if item.get("ok") and item.get("status") == "accepted")
    candidates = sum(int(run.get("candidate_count") or 0) for run in runs)
    return {
        "ok": True,
        "dry_run": dry_run,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "streams": keys,
        "candidate_count": candidates,
        "accepted_count": accepted,
        "runs": runs,
        "user_request_fetching": "disabled",
    }


__all__ = [
    "COLLECTOR_CLASSES",
    "collector_keys",
    "run_collectors",
    "IntelligenceCandidate",
    "CollectorResult",
]
