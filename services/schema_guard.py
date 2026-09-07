"""Run schema DDL once per worker process instead of once per request.

`CREATE TABLE / INDEX IF NOT EXISTS` is free on SQLite and a trap on PostgreSQL:
it takes a ShareLock on the table, which conflicts with the RowExclusiveLock an
INSERT or UPDATE needs. Every caller guarded here runs the DDL and then writes
the same table moments later, frequently on a transaction the request holds open
afterwards — so the ShareLock is still held when the write arrives.

Two gunicorn workers interleaving (write, DDL) against (DDL, write) is a lock
cycle. PostgreSQL kills one side, and the losing thread strands its pooled
connection in a failed transaction. gunicorn's gthread `--timeout` watches the
worker heartbeat rather than an individual request, so that thread is never
recycled: the worker simply stops answering. At `--workers 2` that is half of
production hanging, which is how the presence incident presented from outside —
probes alternating between a fast 401 and a connection that never returned.

Two modules (`alert_engine`, `dashboard_crypto_command_center`) had already
hand-rolled this guard before the incident. This is that same pattern factored
out, so the remaining call sites get it without a thirteenth transcription of
double-checked locking.
"""

from __future__ import annotations

import functools
import threading

_RESETTERS = []


def run_once_per_process(fn):
    """Make a DDL function a no-op after its first success in this process.

    The double check is not ceremony: gunicorn runs `--threads 4`, so four
    threads can reach first use together and would otherwise each issue the DDL
    concurrently — the exact contention the guard exists to remove.

    A call that raises, or returns False, is not cached. A transient failure must
    not leave a worker permanently convinced the tables exist.
    """
    lock = threading.Lock()
    state = {"done": False, "result": None}

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if state["done"]:
            return state["result"]
        with lock:
            if state["done"]:
                return state["result"]
            result = fn(*args, **kwargs)
            if result is not False:
                state["result"] = result
                state["done"] = True
            return result

    def reset():
        with lock:
            state["done"] = False
            state["result"] = None

    wrapper.reset = reset
    wrapper.__wrapped_ddl__ = fn
    _RESETTERS.append(reset)
    return wrapper


def register_resetter(fn):
    """Enrol a hand-rolled schema cache in ``reset_all``.

    Not every module can use ``run_once_per_process``: some own a cache with more
    states than done/not-done, or need a ``force`` argument. Those still have to
    be forgettable between tests for exactly the reason in ``reset_all`` below,
    and a module that keeps its cache private simply gets skipped — silently, and
    only visibly as "no such table" in whichever suite happens to run second.

    Returns ``fn`` so it can be used as a decorator or called directly.
    """
    _RESETTERS.append(fn)
    return fn


def reset_all():
    """Forget every cached creation.

    Production never calls this: one process, one database, one DDL pass. Tests
    do, because each builds a fresh in-memory SQLite database while the guard
    state persists for the whole session — without a reset the second test to run
    would skip creation and then find no tables.
    """
    for reset in _RESETTERS:
        reset()
