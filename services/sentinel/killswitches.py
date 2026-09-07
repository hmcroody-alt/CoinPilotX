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
from dataclasses import dataclass

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


# --- Mission 5: financial switches (Stage 49) --------------------------------
# Detection switches default OFF: absence means no financial detection runs.
# The automation switch is different in kind — see financial_automation_enabled.


def financial_detection_enabled() -> bool:
    """Master financial DETECTION gate (read-only analysis). DEFAULT OFF."""
    if emergency_killed():
        return False
    return _truthy(_env("SENTINEL_FINANCIAL_DETECTION_ENABLED"))


def _financial_subdomain(name: str) -> bool:
    if not financial_detection_enabled():
        return False
    return _truthy(_env(name))


def marketplace_risk_enabled() -> bool:
    return _financial_subdomain("SENTINEL_MARKETPLACE_RISK_ENABLED")


def payout_risk_enabled() -> bool:
    return _financial_subdomain("SENTINEL_PAYOUT_RISK_ENABLED")


def refund_risk_enabled() -> bool:
    return _financial_subdomain("SENTINEL_REFUND_RISK_ENABLED")


def ad_wallet_risk_enabled() -> bool:
    return _financial_subdomain("SENTINEL_AD_WALLET_RISK_ENABLED")


def financial_automation_enabled() -> bool:
    """ALWAYS False. No financial automation exists in Mission 5 — Sentinel
    has zero money-movement capability, so there is nothing this switch could
    enable. The env var SENTINEL_FINANCIAL_AUTOMATION_ENABLED is reserved for
    a future mission and deliberately ignored here: setting it changes
    NOTHING (adversarial test proves this)."""
    return False


# --- Gate registry (Stage 21) -------------------------------------------------
# Every switch that decides whether a Sentinel capability runs is registered
# here, and the registration is what the emergency switch is tested against.
#
# The registry exists because the docstring at the top of this module makes a
# claim — "everything off, no exceptions" — that nothing was checking. The test
# named ``test_emergency_kills_everything`` called the three functions defined in
# *this file* and stopped there, so it could never notice a gate defined
# somewhere else. Two of them had in fact grown without the switch:
# ``bootstrap.bootstrap_enabled`` and ``request_bridge.bridge_enabled``, both
# added by earlier stages of this mission. Neither was found by review; both were
# found by asking the running process what it actually returns.
#
# So the fix is not only to correct those two. A registry plus a discovery test
# turns "someone remembered" into "CI fails", which is the only version of this
# that survives the next stage.
#
# Gates are resolved lazily by module path because several of the modules that
# own them import this one. Late binding also means a monkeypatched env is read
# at call time, which is what makes the invariant testable at all.

_OBSERVE = "observe"    # builds evidence; mutates nothing outside Sentinel's own tables
_ENFORCE = "enforce"    # can change what a user's request does — the client feels this
_AUTOMATE = "automate"  # takes autonomous action on the platform
_SCHEMA = "schema"      # creates storage


@dataclass(frozen=True)
class Gate:
    name: str
    module: str          # dotted path, imported lazily
    func: str
    kind: str
    default_on: bool     # what it returns with no env var set and no emergency
    governs: str


#: The order is boring on purpose: observation first, then enforcement, then
#: automation, then storage. A reader scanning for "what can touch production"
#: should not have to search.
GATES: tuple[Gate, ...] = (
    Gate("ingest", "services.sentinel.killswitches", "ingest_enabled",
         _OBSERVE, True, "writing observations into Sentinel's own tables"),
    Gate("request_bridge", "services.sentinel.request_bridge", "bridge_enabled",
         _OBSERVE, False, "the request path emitting security events"),
    Gate("external_intel", "services.sentinel.external_providers", "master_enabled",
         _OBSERVE, False, "outbound calls to external intelligence providers"),
    Gate("financial_detection", "services.sentinel.killswitches",
         "financial_detection_enabled", _OBSERVE, False,
         "read-only financial risk analysis"),
    Gate("marketplace_risk", "services.sentinel.killswitches",
         "marketplace_risk_enabled", _OBSERVE, False, "marketplace risk scoring"),
    Gate("payout_risk", "services.sentinel.killswitches", "payout_risk_enabled",
         _OBSERVE, False, "payout risk scoring"),
    Gate("refund_risk", "services.sentinel.killswitches", "refund_risk_enabled",
         _OBSERVE, False, "refund risk scoring"),
    Gate("ad_wallet_risk", "services.sentinel.killswitches", "ad_wallet_risk_enabled",
         _OBSERVE, False, "ad wallet risk scoring"),
    Gate("distributed_limits", "services.sentinel.rate_limit", "enabled",
         _ENFORCE, False, "rejecting requests with 429 across gunicorn workers"),
    Gate("automation", "services.sentinel.killswitches", "automation_enabled",
         _AUTOMATE, False, "all runbook automation"),
    Gate("financial_automation", "services.sentinel.killswitches",
         "financial_automation_enabled", _AUTOMATE, False,
         "money movement — hard-false, no such capability exists"),
    Gate("schema_bootstrap", "services.sentinel.bootstrap", "bootstrap_enabled",
         _SCHEMA, True, "creating Sentinel tables at boot"),
)

ENFORCEMENT_KINDS = frozenset({_ENFORCE, _AUTOMATE})


def _resolve(gate: Gate):
    from importlib import import_module
    return getattr(import_module(gate.module), gate.func)


def gate_value(name: str) -> bool:
    """Current answer from one registered gate. Unknown name is False, because an
    unknown gate is not an open one."""
    for gate in GATES:
        if gate.name == name:
            return bool(_resolve(gate)())
    return False


def all_gates() -> dict[str, bool]:
    """Every registered gate's current answer. Cheap: all of them read env only."""
    return {gate.name: bool(_resolve(gate)()) for gate in GATES}


def enforcement_gates() -> tuple[Gate, ...]:
    """Gates whose effect a user can feel. These may never default on."""
    return tuple(g for g in GATES if g.kind in ENFORCEMENT_KINDS)


def switch_state() -> dict:
    """Snapshot for health/evidence — records which layer is blocking (SC12/SC13)."""
    return {
        "emergency_killed": emergency_killed(),
        "ingest_enabled": ingest_enabled(),
        "automation_enabled": automation_enabled(),
        # Every registered gate, so a health reader sees the same list the
        # emergency-switch test asserts over rather than a hand-maintained subset.
        "gates": all_gates(),
        # Mission 5 financial switches (detection read-only; automation is
        # hard-false — no financial automation capability exists).
        "financial_detection_enabled": financial_detection_enabled(),
        "marketplace_risk_enabled": marketplace_risk_enabled(),
        "payout_risk_enabled": payout_risk_enabled(),
        "refund_risk_enabled": refund_risk_enabled(),
        "ad_wallet_risk_enabled": ad_wallet_risk_enabled(),
        "financial_automation_enabled": financial_automation_enabled(),
        "financial_automation_note": "hard-false: no money-movement capability exists",
    }
