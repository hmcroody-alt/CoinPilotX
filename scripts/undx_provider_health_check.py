#!/usr/bin/env python3
"""Prove each UNDX provider is actually reachable, through the router itself.

Not through a hand-written HTTP call. A smoke test that builds its own request
verifies the vendor's uptime and nothing about this application: it passes while
the router reads the wrong environment variable, sends the wrong model ID, or
crashes extracting the reply. Every check here goes through
`undx_router.route_structured_request` with an explicit provider, so a pass means
the path production uses works end to end.

Prints names and statuses only. Credentials never appear in the output: failures
are routed through the router's own redactor, which matches known key values as
well as credential-shaped parameter names.

Usage, against the deployed environment's variables:

    railway run --service CoinPilotX -- .venv/bin/python3 \
        scripts/undx_provider_health_check.py

Exit code is 1 if a provider that is both configured and enabled fails. A
provider that is missing its key, or switched off, is reported and not counted
as a failure - that is a deployment choice, not a fault.
"""

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import undx_router  # noqa: E402

PROMPT = "Reply with exactly the two letters: ok"
SYSTEM = "You are a connectivity probe. Answer with the two letters requested and nothing else."


def check(provider: str, timeout: int) -> dict:
    config = undx_router.PROVIDERS[provider]
    if not undx_router._api_key(provider):
        # Ask the router, not the environment. "Set to something unusable" and
        # "not set" both stop the provider, but only one of them is fixed by
        # adding a variable, and a report that conflates them sends the reader
        # looking for something that is already there.
        health = undx_router.provider_health(provider)
        detail = "is set but cannot be sent as a header" if health == "Malformed API Key" else "not set"
        return {"state": "DISABLED", "reason": f"{config.key_env} {detail}"}
    if not undx_router.provider_enabled(provider):
        return {"state": "DISABLED", "reason": f"{config.enable_env} is off"}

    started = time.time()
    result = undx_router.route_structured_request(
        "provider-health-check", SYSTEM, PROMPT,
        timeout=timeout, max_tokens=256, providers=[provider],
    )
    elapsed = int((time.time() - started) * 1000)

    if not result.get("ok"):
        statuses = [a.get("status") for a in result.get("attempts") or []]
        return {"state": "FAILED", "reason": ", ".join(statuses) or "no attempt recorded", "ms": elapsed}
    # The loop falls through to other providers on failure, so a successful
    # envelope from a *different* provider means this one did not answer.
    if result.get("provider") != provider:
        return {"state": "FAILED", "reason": f"answered by {result.get('provider')} instead", "ms": elapsed}
    return {
        "state": "CONNECTED",
        "model": result.get("model"),
        "ms": result.get("latency_ms", elapsed),
        "reply": (result.get("response") or "")[:40],
        "citations": len(result.get("citations") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=60, help="per-provider seconds; the router may raise it")
    parser.add_argument("--provider", action="append", help="check only these (repeatable)")
    args = parser.parse_args()

    providers = args.provider or sorted(undx_router.PROVIDERS)
    unknown = [p for p in providers if p not in undx_router.PROVIDERS]
    if unknown:
        print(f"UNDX_PROVIDER_HEALTH_FAIL unknown provider: {unknown}")
        return 2

    print("UNDX PROVIDER HEALTH MATRIX")
    print(f"router_enabled={undx_router.router_enabled()} "
          f"multi_model_mode={undx_router.multi_model_mode()} "
          f"default_provider={undx_router.default_provider()}")
    print()

    failures = []
    for provider in providers:
        label = undx_router.PROVIDERS[provider].label
        outcome = check(provider, args.timeout)
        state = outcome["state"]
        if state == "CONNECTED":
            extra = f" citations={outcome['citations']}" if outcome["citations"] else ""
            print(f"  {label:12} CONNECTED  model={outcome['model']} {outcome['ms']}ms "
                  f"reply={outcome['reply']!r}{extra}")
        elif state == "DISABLED":
            print(f"  {label:12} DISABLED   {outcome['reason']}")
        else:
            print(f"  {label:12} FAILED     {outcome['reason']} ({outcome.get('ms')}ms)")
            failures.append(label)

    _print_spend()
    _print_open_circuits()

    print()
    if failures:
        print(f"UNDX_PROVIDER_HEALTH_FAIL configured providers that did not answer: {', '.join(failures)}")
        return 1
    print("UNDX_PROVIDER_HEALTH_PASS")
    return 0


def _print_spend() -> None:
    """What this run cost, per provider.

    A connectivity check that reports only reachability hides the fact that it
    just bought seven completions. It also makes the reasoning overhead visible:
    Meta bills ~95% of its output tokens for thinking nobody reads, and that is
    invisible from the length of the two-letter reply above.
    """
    state = undx_router.spend_state()
    if not state["providers"]:
        return
    print()
    print(f"  spend this run (month {state['month']})")
    for provider, bucket in sorted(state["providers"].items()):
        label = undx_router.PROVIDERS[provider].label
        # A total that silently omits unpriced calls reads as complete. Only
        # models whose rate was read from the vendor's own console are priced;
        # everything else is a floor, and has to say so.
        cost = f"${bucket['cost_usd']:.6f}" if bucket["cost_known"] else "unpriced"
        reasoning = bucket.get("reasoning_tokens") or 0
        share = f" ({reasoning * 100 // max(bucket['output_tokens'], 1)}% reasoning)" if reasoning else ""
        print(f"    {label:12} in={bucket['input_tokens']:<6} out={bucket['output_tokens']:<6}"
              f"{share:<18} {cost}")


def _print_open_circuits() -> None:
    """Providers the breaker has taken out, if any.

    Normally empty: this script probes each provider once, and the breaker needs
    three consecutive failures. It is printed because a non-empty section here
    means the failure above is not the provider refusing one request - it is a
    provider the router has stopped sending traffic to at all, which is a
    different sentence to put in an incident channel.
    """
    health = undx_router.provider_runtime_health()
    rested = {p: h for p, h in health.items() if h["state"] == "open"}
    if not rested:
        return
    print()
    print("  circuit breaker")
    for provider, bucket in sorted(rested.items()):
        label = undx_router.PROVIDERS[provider].label
        probing = " probe in flight" if bucket["probing"] else ""
        print(f"    {label:12} OPEN  consecutive_failures={bucket['consecutive_failures']} "
              f"cooldown_remaining={bucket['cooldown_remaining_s']}s "
              f"last={bucket['last_status']}{probing}")


if __name__ == "__main__":
    raise SystemExit(main())
