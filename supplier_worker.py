"""Disabled-by-default CJ scheduler entry point; no bot/mobile/media imports.

Run after approved configuration/schema bootstrap with `python supplier_worker.py`.
`--once` is suitable for an existing scheduler. Each tick and shared provider
admission are bounded; no worker automatically enables network or production.
"""

import argparse
import json
import time

from services.business_os.suppliers import policy, schema, gateway, quota, fulfillment, worker


def run_tick():
    if not policy.enabled("CJ_RECONCILIATION_ENABLED"):
        return {"status": "disabled"}
    try:
        policy.require_enabled()
        policy.require_network()
        for ensure in (schema.ensure_schema, gateway.ensure_schema, quota.ensure_schema,
                       fulfillment.ensure_schema, worker.ensure_schema):
            ensure()
        return {"status": "ok", **worker.run_once(limit=20)}
    except Exception:
        # No exception message/traceback: request-local credentials are private.
        return {"status": "deferred"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args(argv)
    while True:
        print(json.dumps(run_tick(), sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(max(60, min(args.interval, 3600)))


if __name__ == "__main__":
    main()
