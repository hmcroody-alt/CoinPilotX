#!/usr/bin/env python3
"""Deploy gate: does the *deployed* process serve the routes it should?

Why this is a separate script from route_contract_gate.py
---------------------------------------------------------
They look like the same check and are not, and running only one of them leaves
the hole that produced the TestFlight build 5 incident.

``route_contract_gate.py`` runs at **build time**, offline, against the source
tree. It proves: *the code in this repository has a route for every path its
clients call.* It checks all 449 of them, because it can -- it reads the URL map
directly, with no network and no auth.

This script runs at **deploy time**, against a URL. It proves something the
other one structurally cannot: *the process now serving traffic is that code.*
A green build-time gate says nothing about a deployment built from a different
commit, or one where a route pack raised during registration -- and route packs
here are registered inside ``except Exception`` blocks precisely so one broken
feature cannot block boot. The trade-off is that a subsystem can vanish in
production while the build stays green and ``/health`` answers 200 throughout.
That is exactly what happened: a client shipped against endpoints the deployed
server did not have, every write returned a generic 404, and the screens
rendered perfectly.

So: the build gate catches "we never wrote it". This one catches "we wrote it
and this box is not running it". Neither substitutes for the other.

Could-not-check is not a pass
-----------------------------
A deploy gate that treats a connection error as success is worse than no gate,
because the one moment it is most likely to fail to connect is the moment a
deployment is broken. Unreachable, non-JSON, and unexpected-shape all exit 3
(``EXIT_NO_DATA``), distinct from both pass and fail.

Usage
-----
    python3 scripts/ops/deploy_route_liveness.py --url https://pulsesoc.com
    python3 scripts/ops/deploy_route_liveness.py --url ... --wait 120
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_NO_DATA = 3

ENDPOINT = "/health/routes"
DEFAULT_TIMEOUT = 15


def fetch(url, timeout):
    """(status, payload) or (None, reason-string) if it could not be read.

    A 503 here is a real answer, not a failure to read: `/health/routes`
    returns 503 *with a body* when something is missing, and that body names
    what. So the status is carried alongside the payload rather than raised.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "pulsesoc-deploy-gate/1",
                      "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            # An HTML error page means something in front answered, not the
            # app: an edge 502, a WAF block, a wrong host. Not a route result.
            return None, (f"HTTP {exc.code} with a non-JSON body "
                          f"({body[:120]!r}); something other than the app "
                          f"answered")
    except urllib.error.URLError as exc:
        return None, f"could not connect: {exc.reason}"
    except ValueError as exc:
        return None, f"response was not JSON: {exc}"
    except OSError as exc:
        return None, f"transport error: {exc}"


def describe(payload):
    """Human-readable failure detail, or '' if the payload says healthy."""
    problems = []
    for path in payload.get("missing") or []:
        problems.append(f"route absent from the deployed map: {path}")
    packs = payload.get("route_packs") or {}
    for name, state in sorted(packs.items()):
        registered = state.get("registered") if isinstance(state, dict) else state
        if not registered:
            detail = ""
            if isinstance(state, dict) and state.get("error"):
                detail = f" ({str(state['error'])[:160]})"
            problems.append(f"route pack failed to register: {name}{detail}")
    return "\n".join(f"  - {p}" for p in problems)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True,
                    help="base URL of the deployment, e.g. https://pulsesoc.com")
    ap.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="keep retrying for up to this long while the deploy "
                         "rolls; a transport error is retried, an unhealthy "
                         "answer is not")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    url = args.url.rstrip("/") + ENDPOINT
    deadline = time.monotonic() + max(0, args.wait)
    attempt = 0

    while True:
        attempt += 1
        status, payload = fetch(url, args.timeout)

        if status is None:
            # Unreachable. Worth retrying during a rollout, never worth passing.
            if time.monotonic() < deadline:
                print(f"route-liveness: attempt {attempt}: {payload}; retrying",
                      file=sys.stderr)
                time.sleep(min(5, max(1, deadline - time.monotonic())))
                continue
            print(f"route-liveness: could not check {url}: {payload}",
                  file=sys.stderr)
            return EXIT_NO_DATA

        if not isinstance(payload, dict) or "ok" not in payload:
            print(f"route-liveness: {ENDPOINT} answered a shape this gate does "
                  f"not understand; it has changed and this script has not.",
                  file=sys.stderr)
            return EXIT_NO_DATA

        healthy = bool(payload["ok"]) and status == 200
        if healthy or time.monotonic() >= deadline:
            break
        # Unhealthy but still inside the window: a rollout in progress can
        # legitimately answer from the old process for a few seconds.
        print(f"route-liveness: attempt {attempt}: not healthy yet; retrying",
              file=sys.stderr)
        time.sleep(min(5, max(1, deadline - time.monotonic())))

    if args.json:
        print(json.dumps({"url": url, "status": status, "payload": payload},
                         indent=2))
    elif healthy:
        endpoints = payload.get("endpoints") or {}
        packs = payload.get("route_packs") or {}
        print(f"route-liveness: {url} healthy "
              f"({len(endpoints)} required endpoint(s), "
              f"{len(packs)} route pack(s) registered)")
    else:
        print(f"route-liveness: {url} UNHEALTHY (HTTP {status})")
        detail = describe(payload)
        print(detail if detail else
              "  - reported ok=false without naming a cause")
        print("\nThis deployment is serving a route map the clients were not "
              "built against. Roll back rather than debugging forward: the "
              "symptom on the client is a generic 404 body, which looks like a "
              "client bug and is not one.")

    return EXIT_OK if healthy else EXIT_UNHEALTHY


if __name__ == "__main__":
    raise SystemExit(main())
