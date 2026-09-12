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

    print()
    if failures:
        print(f"UNDX_PROVIDER_HEALTH_FAIL configured providers that did not answer: {', '.join(failures)}")
        return 1
    print("UNDX_PROVIDER_HEALTH_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
