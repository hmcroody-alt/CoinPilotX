"""Why is this capability not available? — answered by naming the flag responsible.

``/health/undx`` already reports every rollout boolean, and
:mod:`services.undx_capability_lifecycle` already projects each capability onto
AVAILABLE / LIMITED / TRAINING / PLANNED / DISABLED. Between them they say *what* the
state is. Neither says *which switch produced it*, and that is the question an operator
actually has.

The gap has a cost, and it was paid in a real support conversation. Someone asked UNDX
to like a post; UNDX answered *"final execution still requires the current PulseSoc
interface"*, which is the canonical LIMITED sentence. That sentence is correct and
useless: LIMITED is reachable from six independent environment variables, four of which
are kill switches with near-identical names, and one of which
(``UNDX_AGENT_REQUIRE_VERIFICATION`` set explicitly to ``0``) disables writes *because a
safety guard was turned off* — the opposite of what an operator reading "writes
suspended" would go looking for. Answering "why" meant reading
:func:`services.undx_agent_policy.writes_available` line by line.

This module reads no new state and changes none. Every value comes from
``undx_agent_policy``; every status comes from ``undx_capability_lifecycle``. It is a
projection over two projections, and it exists so the answer is one call rather than one
careful reading of a boolean expression.

Nothing here is a control. There is no setter, no override and no argument that changes
a flag — a diagnostic that could also flip switches would be a second authority layer
over the rollout surface, which is precisely the thing the policy module's docstring
exists to prevent.
"""

from __future__ import annotations

import os
from typing import Any

from services import undx_agent_policy as policy
from services import undx_capability_lifecycle as lifecycle
from services.undx_capability_registry import REGISTRY


def _raw(name: str) -> str:
    """The literal environment value, or ``—`` for unset.

    Unset and empty are shown differently on purpose: ``UNDX_AGENT_WRITES_ENABLED=""``
    and an absent ``UNDX_AGENT_WRITES_ENABLED`` behave identically here but mean very
    different things about the deploy that produced them.
    """
    value = os.getenv(name)
    if value is None:
        return "—"
    return f'"{value}"'


#: Ordered exactly as :func:`services.undx_agent_policy.writes_available` tests them, so
#: the first entry reported is the first one that would have to be cleared. Order is the
#: useful part: an operator who clears a later switch first sees no change and concludes,
#: wrongly, that flags are not being read.
_WRITE_GATES: tuple[tuple[str, str], ...] = (
    (policy.EMERGENCY_KILL_SWITCH_ENV,
     "emergency stop — blocks reads and writes together"),
    (policy.GLOBAL_WRITE_KILL_SWITCH_ENV,
     "global write kill switch"),
    (policy.AGENT_KILL_SWITCH_ENV,
     "agent write kill switch"),
    (policy.LEGACY_WRITE_KILL_SWITCH_ENV,
     "legacy write kill switch, still honoured"),
)

_READ_GATES: tuple[tuple[str, str], ...] = (
    (policy.EMERGENCY_KILL_SWITCH_ENV,
     "emergency stop — blocks reads and writes together"),
    (policy.READ_KILL_SWITCH_ENV,
     "read kill switch"),
)


def write_blockers() -> list[dict[str, str]]:
    """Every reason writes are currently unavailable, in the order policy tests them.

    Empty when writes are available. Deliberately reports *all* active blockers rather
    than short-circuiting on the first: two kill switches set is a common state after an
    incident, and telling an operator about one of them sends them round the loop twice.
    """
    found: list[dict[str, str]] = []
    for name, why in _WRITE_GATES:
        if policy._truthy(os.getenv(name)):  # noqa: SLF001 - one truthiness definition
            found.append({"flag": name, "value": _raw(name), "effect": why,
                          "clear_by": "unset it, or set it to 0"})
    for name in policy.REQUIRED_WRITE_GUARDS:
        if not policy._guard_enabled(name):  # noqa: SLF001
            found.append({
                "flag": name, "value": _raw(name),
                "effect": ("a required write guard was explicitly disabled; writes fail "
                           "closed rather than run less protected"),
                "clear_by": "unset it, or set it to 1",
            })
    if policy._truthy(os.getenv(policy.EXECUTOR_ONLY_SUCCESS_ENV)):  # noqa: SLF001
        found.append({
            "flag": policy.EXECUTOR_ONLY_SUCCESS_ENV, "value": _raw(policy.EXECUTOR_ONLY_SUCCESS_ENV),
            "effect": ("would let an executor return value stand as completion evidence "
                       "without verification; writes are refused instead"),
            "clear_by": "unset it, or set it to 0",
        })
    if not found and not policy._truthy(os.getenv(policy.AGENT_WRITES_ENV)):  # noqa: SLF001
        found.append({
            "flag": policy.AGENT_WRITES_ENV, "value": _raw(policy.AGENT_WRITES_ENV),
            "effect": "writes are simply not switched on for this deployment",
            "clear_by": "set it to 1",
        })
    return found


def read_blockers() -> list[dict[str, str]]:
    """Every reason reads are currently unavailable. Empty when reads are available."""
    found: list[dict[str, str]] = []
    for name, why in _READ_GATES:
        if policy._truthy(os.getenv(name)):  # noqa: SLF001
            found.append({"flag": name, "value": _raw(name), "effect": why,
                          "clear_by": "unset it, or set it to 0"})
    if not found and not policy._truthy(os.getenv(policy.AGENT_READS_ENV)):  # noqa: SLF001
        found.append({
            "flag": policy.AGENT_READS_ENV, "value": _raw(policy.AGENT_READS_ENV),
            "effect": "reads are simply not switched on for this deployment",
            "clear_by": "set it to 1",
        })
    return found


def explain(capability_id: str) -> dict[str, Any]:
    """Current status of one capability, with the flag that decides it named.

    ``blockers`` is empty for an AVAILABLE capability and never empty otherwise — a
    non-AVAILABLE status with nothing to point at would mean this module and the policy
    module disagree, which is worth being able to see.
    """
    spec = REGISTRY.get(str(capability_id or "").strip())
    if spec is None:
        return {"capability_id": str(capability_id or ""), "registered": False,
                "status": "", "blockers": []}

    denylist = policy._id_set(os.getenv(policy.AGENT_DENYLIST_ENV, ""))  # noqa: SLF001
    allowlist = policy._id_set(os.getenv(policy.AGENT_ALLOWLIST_ENV, ""))  # noqa: SLF001
    blockers: list[dict[str, str]] = []

    if spec.capability_id in denylist:
        blockers.append({
            "flag": policy.AGENT_DENYLIST_ENV, "value": _raw(policy.AGENT_DENYLIST_ENV),
            "effect": "this capability id is on the denylist",
            "clear_by": f"remove {spec.capability_id} from the list",
        })
    elif allowlist and spec.capability_id not in allowlist:
        # The quiet one. An allowlist that omits a capability reads, from the outside,
        # exactly like a capability that does not exist.
        blockers.append({
            "flag": policy.AGENT_ALLOWLIST_ENV, "value": _raw(policy.AGENT_ALLOWLIST_ENV),
            "effect": "an allowlist is set and this capability id is not on it",
            "clear_by": f"add {spec.capability_id} to the list, or unset the allowlist",
        })
    else:
        blockers.extend(write_blockers() if spec.is_write else read_blockers())

    status, reason = lifecycle._registered_status(  # noqa: SLF001 - one status definition
        spec.capability_id, bool(spec.is_write))
    return {
        "capability_id": spec.capability_id,
        "registered": True,
        "is_write": bool(spec.is_write),
        "status": status,
        "reason": reason,
        "user_message": lifecycle.CANONICAL_STATUS_LANGUAGE.get(status, ""),
        "blockers": blockers,
    }


def snapshot(*, sample: int = 8) -> dict[str, Any]:
    """Whole-registry rollup: counts by status, plus the flags behind each non-available one.

    ``sample`` caps the capability ids listed per status. The counts are always exact;
    only the illustrative ids are truncated, because a payload listing 120 ids to say
    "writes are off" buries the one line that matters.
    """
    by_status: dict[str, list[str]] = {}
    for capability_id in sorted(REGISTRY):
        spec = REGISTRY[capability_id]
        status, _ = lifecycle._registered_status(capability_id, bool(spec.is_write))  # noqa: SLF001
        by_status.setdefault(status, []).append(capability_id)

    writes = write_blockers()
    reads = read_blockers()
    return {
        "capability_count": len(REGISTRY),
        "writes_available": policy.writes_available(),
        "reads_available": policy.reads_available(),
        "write_blockers": writes,
        "read_blockers": reads,
        "status_counts": {status: len(ids) for status, ids in sorted(by_status.items())},
        "status_sample": {status: ids[:max(0, sample)]
                          for status, ids in sorted(by_status.items())
                          if status != lifecycle.CapabilityStatus.AVAILABLE},
        "summary": _summary(writes, reads),
    }


def _summary(writes: list[dict[str, str]], reads: list[dict[str, str]]) -> str:
    """One sentence naming the flags, for a log line or a terminal."""
    if not writes and not reads:
        return "Reads and writes are both available."
    parts: list[str] = []
    if writes:
        parts.append("writes blocked by " + ", ".join(item["flag"] for item in writes))
    if reads:
        parts.append("reads blocked by " + ", ".join(item["flag"] for item in reads))
    return "; ".join(parts) + "."


__all__ = ["write_blockers", "read_blockers", "explain", "snapshot"]
