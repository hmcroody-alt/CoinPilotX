"""One answer to "is the model fabric actually doing what it claims".

Four modules now each know one true thing and none of them knows the others:
`undx_health` knows which providers are resting and why, `undx_cost` knows what
the month has spent and how much of that it could price, `undx_config_drift`
knows which settings have stopped meaning what the code assumes, and
`undx_router` knows which providers are configured at all. An operator looking
at any one of them in isolation can reach a confident wrong conclusion — a
green breaker on a provider that has no sendable key, a $0 month that is either
a quiet month or an unreachable ledger.

This composes them into one payload and, more importantly, into one `ok`. The
rule is the same one `undx_config_drift` uses: `ok` is false when a guarantee
this subsystem advertises is not in force. A provider resting on a 503 is not
that — it is the breaker working. A provider resting on a revoked key is,
because no amount of waiting restores it and nothing else will say so.

Secret-free, deliberately and more strictly than it looks
--------------------------------------------------------
Everything here is reachable by anyone who can reach the service, because a
health surface that needs a session cannot be used by the thing that restarts
the service. That constrains what may appear far beyond "do not print the key":

  `last_error` is excluded, as it is from `undx_run_health` and for the same
  reason — a provider error body is written by the provider, and this
  deployment has already seen one carry a full request URL.

  Drift findings are published as `severity`, `code`, `provider` and `where`
  only. `detail` is the readable half and is exactly the half that quotes
  configuration *values* back: "ANTHROPIC_BASE_URL points at
  'https://api.meta.ai'" is an accurate finding and an unauthenticated
  disclosure of internal routing. `where` names the variable, which is public
  in `.env.example` already; the value stays behind the CLI.

  Only the runtime half of the drift check runs. `scan_source` needs a checkout
  and reports file paths and line numbers, and neither belongs on a public
  endpoint or exists in a deployed container.

What it costs to call
---------------------
Two database reads and a pass over the environment. No provider is contacted:
that is `undx_model_audit`, it spends money, and an endpoint anyone can GET is
the last place to put a paid call — a scraper on a 30-second interval would
turn a fraction of a cent into a standing bill, and anyone who found the URL
could bill us on purpose.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services import undx_config_drift, undx_cost, undx_health

log = logging.getLogger(__name__)

#: The payload shape, so a scraper can tell a format change from an outage.
SURFACE = "undx-fabric-1"

#: Drift finding fields safe for an unauthenticated reader. See the module
#: docstring: `detail` and `fix` quote configuration values.
PUBLIC_FINDING_FIELDS = ("severity", "code", "provider", "where")

#: Provider fields safe for an unauthenticated reader. `last_error` is absent
#: on purpose and its absence is load-bearing.
PUBLIC_PROVIDER_FIELDS = (
    "state", "underlying_state", "distributed", "circuit", "probing",
    "consecutive_failures", "successes", "failures", "last_status",
    "cooldown_remaining_s", "actionable",
)


def _providers() -> dict[str, Any]:
    """Every configured provider, whether or not it has ever been observed.

    `undx_health.snapshot()` only knows providers that have been called, so a
    provider that has never once succeeded is simply missing from it — which
    reads, on a dashboard, as "no problems here". That is the §19 defect in a
    different costume: a provider that never answered must not be absent any
    more than it may be labelled "Online".
    """
    import undx_router  # noqa: PLC0415

    observed = undx_health.snapshot()
    out: dict[str, Any] = {}
    for name, config in undx_router.PROVIDERS.items():
        row = observed.get(name)
        entry: dict[str, Any] = {
            key: row[key] for key in PUBLIC_PROVIDER_FIELDS if row and key in row
        }
        entry["label"] = config.label
        entry["model"] = undx_router._model(name)
        entry["enabled"] = undx_router.provider_enabled(name)
        entry["key_present"] = bool(undx_router._api_key(name))
        if row is None:
            # Never called in this deployment's memory. UNKNOWN is the §19 state
            # for exactly this and is not a synonym for healthy.
            entry["state"] = undx_health.UNKNOWN
            entry["underlying_state"] = undx_health.UNKNOWN
            entry["observed"] = False
            entry["actionable"] = False
        else:
            entry["observed"] = True
        _apply_configuration(entry)
        out[name] = entry
    return out


def _apply_configuration(entry: dict[str, Any]) -> None:
    """Let present configuration override a remembered verdict.

    Composing the surfaces turned up a pairing neither of them could see alone:
    `state: HEALTHY` on a provider whose key has since been removed. Both halves
    are honest — the breaker remembers a success that really happened, and the
    router really would refuse to call it now — and together they are a
    dashboard saying a provider is fine when the next request to it cannot
    leave the process. That is the §19 defect wearing its other face: as wrong
    as "Online" for a provider that never answered, and harder to spot, because
    this one was once true.

    `AUTH_FAILED` rather than a new state, because `undx_model_audit.probe`
    already made this call for the same situation and for the same reason: from
    inside this process a missing key and a revoked one are the same fact —
    nothing it sends will authenticate. A second vocabulary for one condition
    is how two surfaces start disagreeing.
    """
    if not entry["enabled"]:
        state = undx_health.UNAVAILABLE
    elif not entry["key_present"]:
        state = undx_health.AUTH_FAILED
    else:
        return
    # Kept beside the override, not replaced by it: "it succeeded before" is the
    # thing that distinguishes a key someone rotated away from one nobody ever
    # set, and those need different people.
    entry["remembered_state"] = entry["state"]
    entry["state"] = state
    entry["underlying_state"] = state
    entry["actionable"] = state in undx_health.ACTIONABLE_STATES


def _cost() -> dict[str, Any]:
    """Month-to-date spend, with the part it could not price kept visible.

    `cost_usd` alone is a number that looks complete and is not: providers with
    no verified price contribute calls and tokens but zero dollars. Reporting
    `uncosted_calls` beside it is the difference between a total and a floor,
    and a budget read off a floor is not a budget.
    """
    snapshot = undx_cost.month_snapshot()
    providers = snapshot.get("providers") or {}
    micro = sum(int(row.get("cost_micro_usd") or 0) for row in providers.values())
    uncosted = sum(int(row.get("uncosted_calls") or 0) for row in providers.values())
    calls = sum(int(row.get("calls") or 0) for row in providers.values())
    return {
        "month": snapshot.get("month"),
        # "ledger" or "process". A caller that cannot tell which one it got
        # cannot tell a quiet month from an unreachable ledger, and those two
        # demand opposite reactions.
        "source": snapshot.get("source"),
        "calls": calls,
        "uncosted_calls": uncosted,
        "cost_usd": round(undx_cost.from_micro_usd(micro), 6),
        "cost_complete": uncosted == 0,
        "budgets_configured": undx_cost.budgets_configured(),
        "providers": {
            name: {"calls": int(row.get("calls") or 0),
                   "uncosted_calls": int(row.get("uncosted_calls") or 0),
                   "cost_usd": round(
                       undx_cost.from_micro_usd(int(row.get("cost_micro_usd") or 0)), 6)}
            for name, row in providers.items()
        },
    }


def _drift() -> dict[str, Any]:
    """The runtime configuration check, stripped to its publishable fields."""
    result = undx_config_drift.check()
    return {
        "ok": result["ok"],
        "critical": result["critical"],
        "warning": result["warning"],
        "info": result["info"],
        "findings": [{key: finding[key] for key in PUBLIC_FINDING_FIELDS}
                     for finding in result["findings"]],
    }


def _routing() -> dict[str, Any]:
    """What the runtime guard saw, as opposed to what the source says.

    `config` above reads the repository and answers "is there a call site that
    bypasses the router". This answers "did a call bypass it", which is a
    different question with a different failure mode: the source check cannot see
    a module it skipped or could not parse, and this cannot see a call site that
    did not fire. Published side by side rather than merged, because an operator
    looking at a disagreement between them is looking at the most informative
    thing this surface can show — one of the two is wrong about the deployment.
    """
    from services import undx_call_guard

    return undx_call_guard.snapshot()


def snapshot() -> dict[str, Any]:
    """The whole fabric in one payload. Never raises.

    Each section is collected independently so that one broken subsystem
    reports as broken rather than blanking the other three — a health surface
    that goes dark on the first failure hides the outage it exists to report.
    """
    started = time.time()
    out: dict[str, Any] = {"surface": SURFACE, "checked_at": time.time()}
    degraded: list[str] = []
    for section, collect in (("providers", _providers), ("cost", _cost),
                             ("config", _drift), ("routing", _routing)):
        try:
            out[section] = collect()
        except Exception as exc:  # noqa: BLE001
            log.warning("UNDX fabric health section %s failed: %s",
                        section, type(exc).__name__)
            # The exception class, not the exception: a database error can carry
            # a statement and a statement can carry a value.
            out[section] = {"error": type(exc).__name__}
            degraded.append(section)

    providers = out.get("providers") or {}
    actionable = sorted(name for name, row in providers.items()
                        if isinstance(row, dict) and row.get("actionable"))
    reachable = sorted(name for name, row in providers.items()
                       if isinstance(row, dict)
                       and row.get("enabled") and row.get("key_present"))
    out["actionable_providers"] = actionable
    out["reachable_providers"] = reachable
    out["degraded_sections"] = degraded
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    # `ok` is about guarantees, not about weather. A provider resting on a 503
    # is the breaker working and does not appear here; a provider resting on a
    # revoked key does, because waiting will not fix it. A section that failed
    # to collect counts too: an unverified guarantee is not a kept one, which is
    # the same rule `undx_config_drift` applies to a check that did not run.
    # A chat call that bypassed the router counts the same way a CRITICAL drift
    # finding does. The check that found it is weaker — it only sees what ran —
    # but what it saw, ran: this is the one signal here that is evidence of a
    # breach rather than of the conditions for one.
    out["ok"] = (not actionable and not degraded
                 and bool(reachable)
                 and bool((out.get("config") or {}).get("ok", False))
                 and bool((out.get("routing") or {}).get("ok", False)))
    return out


__all__ = ["SURFACE", "PUBLIC_FINDING_FIELDS", "PUBLIC_PROVIDER_FIELDS", "snapshot"]
