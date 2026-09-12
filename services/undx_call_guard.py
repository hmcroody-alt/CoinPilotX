"""Counts provider calls that leave this process without passing the router.

`undx_config_drift.scan_source` answers the same question by reading the
repository, and answers it better: it sees every call site whether or not it ran.
But it can only run where a checkout exists, and the deployment is a container
built from a checkout, not a checkout. So it proves a property of the *source* on
the machine that has the source, which is not the same claim as "nothing in this
running process is calling a vendor behind the fabric's back".

The gap is not theoretical in either direction:

  A call site can exist and never be scanned. The scanner skips `tests/`,
  `scripts/` and `mobile/`, and skips any file it cannot parse. A module
  installed as a dependency, or generated at build time, is not in the tree it
  walks at all.

  A call site can be scanned, pass, and still not be what runs. Optional route
  packs in `bot.py` are registered inside `except Exception` blocks, so the
  relationship between what is in the tree and what is serving traffic is looser
  here than in most codebases.

So this is the runtime half, and it is deliberately the weaker half: it sees only
what actually executes. A call site that never fires is invisible to it, exactly
as a file that never runs is invisible to a profiler. The two checks fail in
opposite directions, which is the reason for having both rather than picking the
better one.

What it does, and does not, do
------------------------------
It counts and it logs. It does not block. Blocking would mean deciding, from a
stack walk, that a provider call in production is illegitimate — and a stack walk
is a heuristic (a decorator, a thread pool, a `functools.partial` all move the
frame the guard is looking for). Getting that wrong fails a user's request to
enforce a policy about code layout, which is a worse outcome than the thing it
prevents. `enforce()` exists for tests and for a deployment that wants the
stricter posture, and is off by default.

Two counters, because they are two different facts
--------------------------------------------------
``undx_unrouted_provider_calls_total`` counts *chat* calls that bypassed the
router. Its target is zero and it is currently zero: §12's guarantee.

``undx_unmetered_capability_calls_total`` counts embeddings and image generation,
which bypass the router because the router has no such adapter yet — there is
nothing to route them to. That number is *not* zero today, by design. Keeping
them apart matters: folded together, the headline number could never be zero, and
a number that is never zero cannot be watched for becoming non-zero.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Callable

from services.undx_config_drift import _CHAT_PATHS, _PROVIDER_HOSTS

log = logging.getLogger(__name__)

UNROUTED_CHAT = "undx_unrouted_provider_calls_total"
UNMETERED_CAPABILITY = "undx_unmetered_capability_calls_total"

_lock = threading.Lock()
_counts: dict[str, int] = {UNROUTED_CHAT: 0, UNMETERED_CAPABILITY: 0}
_witnesses: dict[str, list[str]] = {UNROUTED_CHAT: [], UNMETERED_CAPABILITY: []}

#: Kept short on purpose. The counter is the alert; the witnesses are for the
#: person who then has to find the call, and the first few are as diagnostic as
#: the next thousand while costing bounded memory in a long-lived worker.
MAX_WITNESSES = 8

#: Modules whose frames mean the call was routed. Resolved from `sys.modules` by
#: `__file__` rather than by comparing basenames, because a basename match would
#: accept any file called `undx_router.py` anywhere on the path — the same
#: implicit wildcard `_ADAPTER_ALLOWLIST` had, and the reason it is now a path.
_ADAPTER_MODULES = ("undx_router",)

_enforcing = False


class UnroutedProviderCall(RuntimeError):
    """Raised instead of counting, when `enforce()` is on."""


def _adapter_files() -> frozenset[str]:
    """Absolute paths of the adapter modules that are currently imported.

    An adapter that is not imported cannot be on anyone's stack, so "not
    imported" and "not routed through" are the same answer here. That is why this
    reads `sys.modules` instead of importing: importing the router from a module
    the router's own callers import is a cycle, and a guard that cannot be
    imported is worse than one that is occasionally conservative.
    """
    found = set()
    for name in _ADAPTER_MODULES:
        module = sys.modules.get(name)
        path = getattr(module, "__file__", None)
        if path:
            found.add(os.path.realpath(path))
    return frozenset(found)


def _classify(url: str) -> str | None:
    """`UNROUTED_CHAT`, `UNMETERED_CAPABILITY`, or None for anything else.

    Deliberately the cheapest possible test, because it runs in front of every
    outbound HTTP call in the process. A lowercase copy and a substring scan over
    seven hosts is the whole cost on the overwhelming majority of calls, which
    name none of them and return here. Only a URL that already matched pays for
    the stack walk — and that call is about to spend hundreds of milliseconds
    talking to a vendor, so the walk is free in the only place it happens.
    """
    if not isinstance(url, str) or not url:
        return None
    lowered = url.lower()
    if not any(host in lowered for host in _PROVIDER_HOSTS):
        return None
    if any(part in lowered for part in _CHAT_PATHS):
        return UNROUTED_CHAT
    return UNMETERED_CAPABILITY


def _routed(adapters: frozenset[str]) -> bool:
    if not adapters:
        return False
    frame = sys._getframe(1)
    while frame is not None:
        if os.path.realpath(frame.f_code.co_filename) in adapters:
            return True
        frame = frame.f_back
    return False


def observe(url: str, *, caller: str = "") -> str | None:
    """Record one outbound URL. Returns the counter charged, or None.

    Fail-open in every branch that is not the counter itself: this sits in front
    of 67 `requests.post` call sites that have nothing to do with AI, and a bug
    here must not be able to take out a webhook.
    """
    try:
        kind = _classify(url)
        if kind is None:
            return None
        adapters = _adapter_files()
        if _routed(adapters):
            return None
        where = caller or _describe_caller(adapters)
        with _lock:
            _counts[kind] += 1
            if len(_witnesses[kind]) < MAX_WITNESSES:
                _witnesses[kind].append(where)
        # The URL is not logged. A provider URL can carry the key in a query
        # parameter — Gemini's does — and this line would then write a live
        # credential into a log aggregator that has a different retention policy
        # and a different audience than the environment the key lives in.
        log.error("%s: a provider call bypassed the router, from %s", kind, where)
        if _enforcing:
            raise UnroutedProviderCall(f"{kind}: unrouted provider call from {where}")
        return kind
    except UnroutedProviderCall:
        raise
    except Exception:  # pragma: no cover - the guard must not break the caller
        log.exception("undx_call_guard failed while observing an outbound call")
        return None


def _describe_caller(adapters: frozenset[str]) -> str:
    """First frame outside this module and outside the HTTP library."""
    frame = sys._getframe(1)
    here = os.path.realpath(__file__)
    while frame is not None:
        path = os.path.realpath(frame.f_code.co_filename)
        if path != here and "site-packages" not in path and path not in adapters:
            return f"{os.path.basename(path)}:{frame.f_lineno}"
        frame = frame.f_back
    return "unknown"


def counters() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def witnesses() -> dict[str, list[str]]:
    with _lock:
        return {key: list(value) for key, value in _witnesses.items()}


def snapshot() -> dict[str, Any]:
    """The shape `undx_fabric_health` folds in.

    `ok` is about the chat counter alone. The capability counter is expected to
    be non-zero until the router grows embedding and image adapters, and a health
    surface that reported "not ok" for a known, planned, documented gap would be
    reporting the plan as a fault — which is how a red light stops being read.
    """
    counts = counters()
    return {
        "ok": counts[UNROUTED_CHAT] == 0,
        "enforcing": _enforcing,
        "installed": _installed,
        "counters": counts,
        "witnesses": witnesses(),
    }


def reset() -> None:
    """For tests. Not exported to any route."""
    global _enforcing
    with _lock:
        for key in _counts:
            _counts[key] = 0
            _witnesses[key] = []
    _enforcing = False


def enforce(on: bool = True) -> None:
    global _enforcing
    _enforcing = bool(on)


# ------------------------------------------------------------------ installation

_installed = False

#: (owner, attribute, original) rather than a name-keyed dict. One of the wrapped
#: entry points is `requests.sessions.Session.request`, a *class* attribute, and a
#: name key cannot find its way back to the class to restore it — `uninstall`
#: would silently leave the wrapper in place and the next test would inherit it.
_original: list[tuple[Any, str, Callable[..., Any]]] = []

#: `requests.post(url)` reaches the network through `Session.request(url)`, and
#: both are wrapped, so one outbound call arrives here twice. Counting it twice
#: would make the number this metric exists to hold at zero depend on which of
#: two equivalent spellings a caller chose. Per-thread, not a lock: two threads
#: making two calls are two calls.
_reentry = threading.local()


def _url_of(args: tuple[Any, ...], kwargs: dict[str, Any], position: int) -> str:
    url = kwargs.get("url")
    if url is None and len(args) > position:
        url = args[position]
    if url is None:
        url = kwargs.get("fullurl")
    if not isinstance(url, str):
        # `urlopen(Request(...))` passes an object, and the URL is on it.
        url = getattr(url, "full_url", None) or getattr(url, "url", None)
    return url if isinstance(url, str) else ""


def _wrap(owner: Any, attribute: str, url_argument: int = 0) -> None:
    original = getattr(owner, attribute, None)
    if original is None or not callable(original):
        return
    if any(existing is owner and name == attribute
           for existing, name, _ in _original):
        return
    _original.append((owner, attribute, original))

    def guarded(*args: Any, **kwargs: Any) -> Any:
        if getattr(_reentry, "inside", False):
            return original(*args, **kwargs)
        _reentry.inside = True
        try:
            observe(_url_of(args, kwargs, url_argument))
            return original(*args, **kwargs)
        finally:
            _reentry.inside = False

    guarded.__name__ = getattr(original, "__name__", attribute)
    guarded.__doc__ = getattr(original, "__doc__", None)
    guarded.__wrapped__ = original
    setattr(owner, attribute, guarded)


def install() -> bool:
    """Wrap the HTTP entry points this deployment actually uses.

    The inventory is measured, not guessed: 67 `requests.post`, 32
    `requests.get`, 11 `urllib.request.urlopen` and 9 `urllib.request.Request` in
    non-test code, and no provider SDK anywhere. `requests.Session.request` is
    wrapped too, because `requests.post` routes through it and a caller holding
    its own `Session` never touches the module-level function.

    Idempotent, and a no-op if `requests` is absent — this must not be the reason
    a worker fails to boot.
    """
    global _installed
    if _installed:
        return True
    try:
        import urllib.request

        _wrap(urllib.request, "urlopen")
        try:
            import requests

            for verb in ("post", "get", "put", "patch", "delete", "head", "request"):
                _wrap(requests, verb)
            _wrap(requests.sessions.Session, "request", url_argument=2)
        except Exception:
            log.warning("undx_call_guard: requests unavailable, urllib only")
        _installed = True
        return True
    except Exception:  # pragma: no cover
        log.exception("undx_call_guard: install failed; no runtime guard is active")
        return False


def uninstall() -> None:
    """Restore the originals. For tests, so one does not leak into the next.

    Reversed, so that if a future `install` ever wraps the same attribute twice
    the tree unwinds in the order it was built rather than leaving the inner
    wrapper installed.
    """
    global _installed
    for owner, attribute, original in reversed(_original):
        try:
            setattr(owner, attribute, original)
        except Exception:  # pragma: no cover - a read-only attribute
            log.warning("undx_call_guard: could not restore %s.%s", owner, attribute)
    _original.clear()
    _installed = False


__all__ = [
    "UNROUTED_CHAT", "UNMETERED_CAPABILITY", "UnroutedProviderCall",
    "counters", "enforce", "install", "observe", "reset", "snapshot",
    "uninstall", "witnesses",
]
