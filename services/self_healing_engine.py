"""Self-healing infrastructure architecture."""

from __future__ import annotations


def recovery_plan(health=None) -> dict:
    health = health or {}
    actions = []
    if int(health.get("failed_jobs") or 0) > 100:
        actions.append("quarantine_failed_jobs")
    if int(health.get("websocket_errors") or 0) > 20:
        actions.append("restart_realtime_channel")
    if health.get("provider_status") == "down":
        actions.append("pause_provider_and_use_fallback")
    return {"actions": actions or ["monitor"], "auto_recoverable": bool(actions)}
