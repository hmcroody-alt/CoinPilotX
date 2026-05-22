"""Global event system for market, safety, creator, and education events."""

from __future__ import annotations

from datetime import datetime


EVENT_SEVERITY = {"info": 1, "watch": 2, "high": 3, "critical": 4}


def create_event(event_type: str, title: str, severity: str = "info", payload=None) -> dict:
    severity = str(severity or "info").lower()
    return {
        "event_type": str(event_type or "general")[:80],
        "title": str(title or "Global event")[:180],
        "severity": severity if severity in EVENT_SEVERITY else "info",
        "priority": EVENT_SEVERITY.get(severity, 1),
        "payload": payload or {},
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def should_show_banner(event=None) -> bool:
    event = event or {}
    return EVENT_SEVERITY.get(str(event.get("severity") or "info").lower(), 1) >= 3


def event_summary(events=None) -> dict:
    events = events or []
    critical = [e for e in events if str(e.get("severity")) == "critical"]
    high = [e for e in events if str(e.get("severity")) == "high"]
    return {"total": len(events), "critical": len(critical), "high": len(high), "banner_events": [e for e in events if should_show_banner(e)][:5]}
