"""Reconciliation worker — drains the durable webhook inbox into the ledger.

The webhook request path only *persists* events (``webhook_inbox.enqueue_event``)
so it stays fast and can never be broken by ledger logic. Actually turning those
events into money movement is this worker's job: it replays every non-terminal
inbox row through :func:`stripe_ledger_handler.handle_stripe_event`, which is
idempotent, so running the worker repeatedly is safe.

Intended callers: an out-of-band schedule (cron / scheduled task) and an
admin-triggered "reconcile now" button. Deliberately dependency-light and free
of any ``bot.py`` import so it can run in a worker process or be unit-tested.

Usage::

    from services.business_os.payments import reconcile_worker
    summary = reconcile_worker.run_once()            # all providers
    summary = reconcile_worker.run_once("stripe")    # one provider
"""

from __future__ import annotations

from typing import Optional

from services.business_os.ledger import ensure_schema as _ensure_ledger_schema
from services.business_os.payments import webhook_inbox
from services.business_os.payments.stripe_ledger_handler import handle_stripe_event

# Map a provider name to the handler that knows how to post its events. Adding a
# new provider (e.g. a second PSP) is a one-line change here.
_HANDLERS = {
    "stripe": handle_stripe_event,
}


def run_once(provider: Optional[str] = "stripe", *, limit: int = 200) -> dict:
    """Replay pending inbox rows for ``provider`` into the ledger, once.

    Returns the ``reconcile_pending`` summary augmented with ``provider``. Safe
    to call on a schedule; idempotent handlers make re-runs no-ops.
    """
    handler = _HANDLERS.get(provider or "stripe")
    if handler is None:
        raise ValueError(f"no ledger handler registered for provider {provider!r}")

    # Make sure both schemas exist before we start writing.
    _ensure_ledger_schema()
    webhook_inbox.ensure_schema()

    summary = webhook_inbox.reconcile_pending(handler, provider=provider, limit=limit)
    summary["provider"] = provider
    return summary


def _main(argv=None) -> int:
    """CLI / worker-process entrypoint.

    Run once (cron-friendly)::

        python -m services.business_os.payments.reconcile_worker --provider stripe

    Or run as a long-lived sweeper::

        python -m services.business_os.payments.reconcile_worker --interval 30

    Prints one JSON summary per sweep to stdout.
    """
    import argparse
    import json
    import time

    parser = argparse.ArgumentParser(description="Business OS payments reconciliation worker")
    parser.add_argument("--provider", default="stripe", help="provider to reconcile (default: stripe)")
    parser.add_argument("--limit", type=int, default=200, help="max rows per sweep (default: 200)")
    parser.add_argument(
        "--interval", type=float, default=0.0,
        help="seconds between sweeps; 0 (default) runs once and exits",
    )
    args = parser.parse_args(argv)

    def _sweep() -> dict:
        summary = run_once(args.provider, limit=args.limit)
        print(json.dumps(summary, sort_keys=True), flush=True)
        return summary

    if args.interval and args.interval > 0:
        # Long-running sweeper. A per-sweep failure is logged and the loop
        # continues, so a transient DB blip never kills the worker.
        while True:
            try:
                _sweep()
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"error": str(exc)}), flush=True)
            time.sleep(args.interval)

    _sweep()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
