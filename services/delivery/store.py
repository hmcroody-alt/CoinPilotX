"""The delivery domain's binding to ``services/cache_engine.py``.

Why this is a module and not two lambdas
----------------------------------------
``cache_engine`` almost satisfies the ``Store`` protocol already: ``cache_get``
and ``cache_set`` have the right shapes. What it does not do is behave the same
way on both of its backends, and the delivery domain's correctness depends on a
stored envelope coming back exactly as it went in — ``cache.read`` re-reads
``stored_at``, ``fresh_until`` and ``stale_until`` off the body and decides
between FRESH, STALE and MISS on their strength.

Two backends, two fidelities
----------------------------
``cache_engine`` keeps values **by reference** in its in-process dict and as
``json.dumps(value, default=str)`` in Redis. That ``default=str`` is the whole
problem: it does not fail on a type JSON cannot hold, it silently turns it into
a string. A ``Decimal`` freight cost stored locally comes back a ``Decimal`` and
stored in Redis comes back ``"12.34"``; a tuple comes back a tuple locally and a
list in Redis. Nothing raises in either place.

So the divergence is invisible where it is written, invisible where it is read,
and only appears as arithmetic on a string in the one environment that has Redis
configured — production. This is the same shape as a suite that passes on SQLite
and crashes on Postgres, and it is defended the same way: rather than test for
it, make it unrepresentable. Every write is JSON round-tripped **strictly** —
no ``default`` — and compared against the original. A value that would not
survive Redis is refused before it reaches the dict that would have tolerated
it, so local development fails on exactly what production would.

TTLs ``cache_engine`` will quietly rewrite
------------------------------------------
``ttl_seconds = max(1, int(ttl_seconds or 60))`` rewrites two inputs without
saying so. ``0`` — a caller asking for no caching at all — becomes **sixty
seconds**, and a float is truncated, which shortens the row's life below the
``stale_until`` its own body advertises and silently clips the tail off the
stale tier. Neither is reachable from ``cache.store_ttl`` today, which returns a
positive int. Both are one call site away, and neither would fail a test. So a
TTL that is not a positive whole number of seconds is refused here.

Keys are a shared, flat namespace
---------------------------------
One ``cache_engine`` serves presence, counters and whatever else across the
monolith. Reading a foreign key is harmless — ``cache.read`` classifies a body
without our ``version`` as a MISS — but *writing* one is not: it would evict
another subsystem's entry and replace it with a delivery envelope. That
asymmetry is why the guard is on the key rather than on the body, and why it is
derived from :data:`cache.VERSION` so a schema bump moves it along.

A cache fault is a miss, never an error
---------------------------------------
``cache_engine``'s own first line calls Redis "an accelerator, not a hard
dependency". That is only true end to end if nothing beneath this seam can
raise into a page render, so a store fault degrades to a miss on read and to
``False`` on write. Today ``cache_engine`` already swallows its own Redis
errors, so this catches nothing in practice — it is here because the guarantee
belongs to the seam and not to whichever store happens to be behind it.

A caller bug raises. A caller bug is a wrong key, an impossible TTL or a value
that cannot survive its own cache: all three are ours to fix and none of them
gets quieter for being hidden. ``quote.py`` contains them at its call sites, so
a bad cache cannot discard a delivery estimate that was computed correctly.

Known limitation in what is underneath
--------------------------------------
When ``REDIS_URL`` is set and Redis is unreachable, ``redis_client()`` leaves
its cached handle at ``None`` and therefore re-runs ``from_url`` plus ``ping``
on **every** call, each bounded by a 1.5-second connect timeout. On this read
path that adds up to ~1.5s per quote for as long as Redis is down, which is
precisely when a provider is also likely to be struggling. Fixing it means
changing a module the whole monolith shares, so it is recorded here rather than
done quietly as part of a delivery change.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import cache_engine

from . import cache

#: Only keys minted by :mod:`services.delivery.cache` may be written.
KEY_PREFIX = f"delivery|{cache.VERSION}|"


class StoreRejected(ValueError):
    """The caller asked for something the store must not do.

    Distinct from a store fault on purpose. A fault is the cache being
    unavailable, which is survivable and silent; this is a bug in the delivery
    code — a foreign key, a TTL that would be rewritten, or a value that would
    come back from Redis as something other than what it was.
    """


class CacheEngineStore:
    """``Store`` over :mod:`services.cache_engine`.

    ``engine`` is injected so the tier is swappable and so tests can drive a
    fault without a Redis; it must offer ``cache_get(key)`` and
    ``cache_set(key, value, ttl_seconds)``.
    """

    def __init__(self, *, engine: Any = cache_engine) -> None:
        if not (hasattr(engine, "cache_get") and hasattr(engine, "cache_set")):
            raise StoreRejected("engine must offer cache_get and cache_set")
        self._engine = engine

    def get(self, key: str) -> Optional[Any]:
        """The stored body, or ``None`` for both a miss and an unavailable store."""
        name = _key(key)
        try:
            return self._engine.cache_get(name)
        except Exception:  # noqa: BLE001 — a cache fault is a miss, see the docstring
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> bool:
        """Store ``value``; ``False`` if the store would not take it.

        Validation happens before the call so a caller bug raises rather than
        being absorbed by the fault handling next to it.
        """
        name, ttl = _key(key), _ttl(ttl_seconds)
        _storable(value)
        try:
            self._engine.cache_set(name, value, ttl)
        except Exception:  # noqa: BLE001
            return False
        return True


def _key(key: Any) -> str:
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        raise StoreRejected(
            f"a delivery cache key must be built by services.delivery.cache "
            f"and begin {KEY_PREFIX!r}; got {key!r}")
    return key


def _ttl(ttl_seconds: Any) -> int:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise StoreRejected(
            "ttl_seconds must be a whole number of seconds; a float is truncated "
            "and the row then dies before its own stale deadline")
    if ttl_seconds < 1:
        raise StoreRejected(
            f"ttl_seconds must be at least a second; {ttl_seconds} is rewritten to 60")
    return ttl_seconds


def _storable(value: Any) -> Any:
    """Refuse a value the Redis backend would hand back as something else.

    Two checks, and the division of labour between them is not the obvious one.
    The **round trip** is what catches the divergence: a tuple encodes fine and
    comes back a list, and with ``default=str`` in play a ``Decimal`` encodes
    fine and comes back a string, so comparing the parsed result against the
    original is the only thing that sees either. The **except** contributes the
    error *class* rather than the detection — without it an unencodable value
    leaves here as a raw ``TypeError`` from the encoder, and ``quote.py`` treats
    anything that is not ``StoreRejected`` as a cache fault to be swallowed. A
    caller bug would then be a silent no-op instead of a stack trace.
    """
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as unencodable:
        raise StoreRejected(
            f"this value cannot be held as JSON, so the Redis tier would keep a "
            f"str() of it while the in-process tier kept the real thing: "
            f"{unencodable}") from unencodable
    if json.loads(encoded) != value:
        raise StoreRejected(
            "this value does not survive a JSON round trip unchanged, so the "
            "two cache backends would disagree about what was stored")
    return value
