"""Ask every provider, for real, whether the model this deployment sends still exists.

Why this cannot be built on ListModels
--------------------------------------
The obvious implementation is to call each vendor's model-listing endpoint and
compare it against the configured IDs. That control would report green through
the exact outage it is meant to catch, and this repo already holds the evidence:
`undx_router.py` lines 98-100 record that `gemini-2.5-flash` and
`gemini-2.5-flash-lite` are both advertised by ListModels to this deployment's
key, and both return 404 from `generateContent`. A listing says what a vendor
will *name*, not what it will *serve* to this key on this endpoint with this
billing state. Claude and Gemini each 404'd every request in production for an
unknown period; a listing-based check would have been green for all of it.

So the probe is a real completion. It is the smallest honest one available —
a fixed one-word prompt and a handful of output tokens — but it goes down the
same code path a user request takes, through `undx_router.CALLERS`, because any
cheaper check is checking something else.

What it costs, and why that is recorded
---------------------------------------
Real calls spend real money. Each probe is metered through the same
`undx_cost` ledger as any other call, so the money appears in the month's
totals rather than as an invisible line item that makes the budget quietly
wrong. Seven probes at a few hundred tokens is a fraction of a cent; the reason
to record it is not the amount, it is that a budget with an unmetered spender
in it is not a budget.

The ledger has one row per provider per month and no column for *why* a call
was made, so an audit's spend is indistinguishable from a user's inside it.
That is a real limitation and it is stated rather than papered over with a tag
the schema would discard: `audit()` returns its own `cost_usd` for the run, and
that figure is the only place the split is visible.

What it does *not* do
---------------------
It does not consult the circuit breaker, by design. The audit's job is to find
out why a provider is resting, and a check that refused to look at resting
providers would go blind exactly when it was needed. It does not claim the
half-open probe lease either — it calls the adapter directly — so an audit
cannot be mistaken for organic recovery traffic, and cannot consume the one
trial request a real recovery is entitled to.

It carries no user content. The prompt is a module constant, and
`test_the_probe_never_carries_caller_content` exists to keep it that way: an
audit that echoed a caller's text would be sending data to every configured
provider at once, including ones a privacy ceiling would have refused, and it
would be doing it from a code path nobody reads as a data path.

It is an operator action, not a request path. Nine workers each running an
audit is a nine-way herd against providers that may already be failing, which
is the harm the breaker exists to prevent. Call it from a script, an admin
route, or one scheduled worker.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services import undx_cost, undx_health

log = logging.getLogger(__name__)


#: The probe turn. A module constant, never a parameter — see the module
#: docstring. "ping" rather than a question, because a question invites a long
#: answer and every output token on this path is bought to learn one bit.
PROBE_SYSTEM = "Reply with the single word: ok"
PROBE_PROMPT = "ping"

#: Enough for a one-word reply on every provider. Meta and Gemini spend output
#: tokens on reasoning that never reaches the text, so a cap of 1 can return an
#: empty completion from a perfectly healthy model — which would be recorded as
#: a failure and would make this check lie in the more expensive direction.
PROBE_MAX_TOKENS = 16

#: Short. A provider that needs a minute to answer "ok" has told us what we
#: came to find out, and the audit should not spend `META_MUSE_TIMEOUT_MS` per
#: provider to hear it.
PROBE_TIMEOUT_SECONDS = 20

#: The verdicts callers act on, as opposed to the ones they wait out.
ACTIONABLE_STATES = undx_health.ACTIONABLE_STATES


def _router():
    """Imported lazily so that importing this module does not drag the router in.

    The dependency only points one way — the router knows nothing about the
    audit — but the audit is also loaded by scripts and admin routes that have
    no other reason to pay for the router's import.
    """
    import undx_router  # noqa: PLC0415

    return undx_router


def probe(provider: str, *, timeout: int | None = None,
          max_tokens: int = PROBE_MAX_TOKENS,
          record: bool = True) -> dict[str, Any]:
    """One real completion against one provider. Never raises.

    Returns a verdict in `undx_health.HEALTH_STATES`, so a caller cannot invent
    a second vocabulary for the same question — the classification is
    `undx_health.failure_state` on the outcome, the same function the breaker
    and the dashboard use.
    """
    router = _router()
    provider = (provider or "").strip().lower()
    started = time.time()
    config = router.PROVIDERS.get(provider)
    model = router._model(provider) if config else ""
    out: dict[str, Any] = {
        "provider": provider,
        "label": config.label if config else provider,
        "model": model,
        "probed": False,
        "ok": False,
        "state": undx_health.UNKNOWN,
        "status": "",
        "error": "",
        "served_model": "",
        "model_mismatch": False,
        "latency_ms": 0,
        "cost_usd": None,
        "skipped": "",
    }
    if config is None:
        out["skipped"] = "unknown provider"
        return out
    if not router.provider_enabled(provider):
        # Not a failure: the operator turned it off. Reporting UNAVAILABLE says
        # the true thing without implying anything is broken.
        out["state"] = undx_health.UNAVAILABLE
        out["skipped"] = "disabled"
        return out
    if not router._api_key(provider):
        # No key is indistinguishable, from here, from a revoked one: either
        # way nothing this process can send will authenticate.
        out["state"] = undx_health.AUTH_FAILED
        out["skipped"] = "no api key"
        return out

    seconds = int(timeout or PROBE_TIMEOUT_SECONDS)
    try:
        result = router.CALLERS[provider](
            PROBE_SYSTEM, "", [], seconds,
            user_content=PROBE_PROMPT, temperature=0.0, max_tokens=max_tokens,
        )
        text = router._clean_text(result.get("text"), 200)
        served = str(result.get("model") or "")
        usage = result.get("usage") or router._normalise_usage(provider, model, None)
        if record:
            undx_cost.record(usage)
        out.update({
            "probed": True,
            "ok": bool(text),
            "served_model": served,
            "latency_ms": int((time.time() - started) * 1000),
            "cost_usd": usage.get("cost_usd"),
            # Providers pin a dated build behind a floating alias: asking for
            # `gpt-4o-mini` gets `gpt-4o-mini-2024-07-18` back, which is not
            # drift. A served name that does not extend the requested one is,
            # and it is worth seeing, because a silent substitution means the
            # price table and the benchmark results are about a different model
            # than the one answering.
            "model_mismatch": bool(
                served and model and not served.startswith(model)
                and not model.startswith(served)),
        })
        if text:
            out["state"] = undx_health.HEALTHY
            out["status"] = "success"
            if record:
                undx_health.record_success(provider)
        else:
            # A completion that returns nothing is a failure even though the
            # transport succeeded, and it is the shape a retired model takes on
            # at least one provider.
            out["status"] = undx_health.STATUS_RESPONSE
            out["error"] = "empty completion"
            out["state"] = _verdict(out["status"], out["error"])
            if record:
                undx_health.record_failure(provider, out["status"], out["error"])
        return out
    except Exception as exc:  # noqa: BLE001 - a probe must not become an outage
        detail = router._safe_error(exc)
        status = undx_health.classify_failure(exc)
        out.update({
            "probed": True,
            "status": status,
            "error": detail,
            "state": _verdict(status, detail),
            "latency_ms": int((time.time() - started) * 1000),
        })
        if record:
            undx_health.record_failure(provider, status, detail)
        return out


def _verdict(status: str, error: str) -> str:
    """One observed failure, classified by the module that owns the taxonomy.

    `consecutive_failures` is 1 because that is what a single probe observed.
    Inflating it to force UNAVAILABLE would make one transient 503 read as an
    outage; the states that matter here — AUTH_FAILED, BILLING_FAILED,
    MODEL_RETIRED — do not depend on the count, because a second attempt tells
    you nothing new about a revoked key.
    """
    return undx_health.failure_state({
        "consecutive_failures": 1,
        "last_status": status,
        "last_error": error,
        "last_success_at": 1.0,
    })


def audit(providers: Any = None, *, timeout: int | None = None,
          record: bool = True) -> dict[str, Any]:
    """Probe every configured provider and report what each one actually did.

    `retired` is the answer this exists for: models the deployment is still
    configured to send that the provider no longer serves. `actionable` is the
    wider set — a revoked key and an unpaid invoice are the same kind of finding
    as a dead model ID, in that waiting will not fix any of them.
    """
    router = _router()
    names = [str(p).strip().lower() for p in providers] if providers else list(router.PROVIDERS)
    started = time.time()
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        results[name] = probe(name, timeout=timeout, record=record)

    retired = sorted(n for n, r in results.items()
                     if r["state"] == undx_health.MODEL_RETIRED)
    actionable = sorted(n for n, r in results.items()
                        if r["state"] in ACTIONABLE_STATES)
    healthy = sorted(n for n, r in results.items()
                     if r["state"] == undx_health.HEALTHY)
    mismatched = sorted(n for n, r in results.items() if r["model_mismatch"])
    probed = [r for r in results.values() if r["probed"]]
    spent = sum(r["cost_usd"] or 0.0 for r in probed)

    if retired:
        # ERROR, not warning: a configured model the provider will not serve is
        # a hard outage for that provider that failover hides, which is the
        # precise way the last two went unnoticed.
        log.error("UNDX model audit found retired models: %s", ", ".join(retired))
    for name in mismatched:
        log.warning("UNDX model audit: %s asked for %s and was served %s",
                    name, results[name]["model"], results[name]["served_model"])

    return {
        "ok": not actionable,
        "checked_at": time.time(),
        "providers": results,
        "retired": retired,
        "actionable": actionable,
        "healthy": healthy,
        "model_mismatch": mismatched,
        "probes": len(probed),
        # A floor, not a total, whenever a probed model has no verified price —
        # the same distinction `undx_cost` draws, kept rather than rounded away.
        "cost_usd": round(spent, 6),
        "cost_complete": all(r["cost_usd"] is not None for r in probed),
        "elapsed_ms": int((time.time() - started) * 1000),
        "method": "completion",
    }


__all__ = [
    "PROBE_SYSTEM", "PROBE_PROMPT", "PROBE_MAX_TOKENS", "PROBE_TIMEOUT_SECONDS",
    "ACTIONABLE_STATES", "probe", "audit",
]
