"""Sentinel kill switches (Stage 23) — owner authority is absolute (SC10).

Precedence mirrors the UNDX convention exactly (emergency > domain > per-unit),
with a Sentinel namespace so the two systems can be killed independently:

1. ``SENTINEL_EMERGENCY_KILL_SWITCH=1``   → everything off, no exceptions.
2. ``SENTINEL_AUTOMATION_ENABLED``        → master for ALL automation. Default OFF.
3. ``SENTINEL_<DOMAIN>_AUTOMATION_ENABLED`` → per-domain gate. Default OFF.
4. ``SENTINEL_RUNBOOK_<NAME>_ENABLED``    → per-runbook gate. Default OFF.

Read-side (observe/ingest) is separately gated and defaults ON because
observation is how Sentinel maintains evidence; it never mutates state:
``SENTINEL_INGEST_ENABLED`` (default on), killed by the emergency switch.

High-risk automation therefore requires FOUR explicit opt-ins and can be
revoked at any layer instantly (fail closed, SC15).
"""

from __future__ import annotations

import os
import re

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _env(name: str) -> str | None:
    return os.getenv(name)


def emergency_killed() -> bool:
    return _truthy(_env("SENTINEL_EMERGENCY_KILL_SWITCH"))


def ingest_enabled() -> bool:
    """Observation/ingest. Default ON; the emergency switch still kills it."""
    if emergency_killed():
        return False
    raw = _env("SENTINEL_INGEST_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return _truthy(raw)


def automation_enabled() -> bool:
    """Master automation gate. DEFAULT OFF — absence of the variable means no."""
    if emergency_killed():
        return False
    return _truthy(_env("SENTINEL_AUTOMATION_ENABLED"))


_NAME_RE = re.compile(r"[^A-Z0-9]+")


def _norm(name: str) -> str:
    return _NAME_RE.sub("_", str(name or "").upper()).strip("_")


def domain_automation_enabled(domain: str) -> bool:
    """Per-domain gate (e.g. SECURITY, OPERATIONAL). DEFAULT OFF, and only
    meaningful when the master gate is already on."""
    if not automation_enabled():
        return False
    return _truthy(_env(f"SENTINEL_{_norm(domain)}_AUTOMATION_ENABLED"))


def runbook_enabled(runbook_name: str, domain: str) -> bool:
    """Per-runbook gate. Requires the whole chain: emergency clear → master on
    → domain on → runbook on. Every layer defaults OFF."""
    if not domain_automation_enabled(domain):
        return False
    return _truthy(_env(f"SENTINEL_RUNBOOK_{_norm(runbook_name)}_ENABLED"))


def switch_state() -> dict:
    """Snapshot for health/evidence — records which layer is blocking (SC12/SC13)."""
    return {
        "emergency_killed": emergency_killed(),
        "ingest_enabled": ingest_enabled(),
        "automation_enabled": automation_enabled(),
    }
