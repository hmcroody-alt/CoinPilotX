"""UNDX cost authority — what a call costs, what has been spent, and when to stop.

Before this module, `undx_router` measured spend and enforced nothing. Its own
comment said so: totals were "an observability figure, not an accounting
ledger", in memory, per process, reset by a restart. With four gunicorn workers
plus five background workers that is nine independent tallies, none of which is
the bill.

Three things had to be true before "budget" could mean anything.

1. One price list
-----------------
`PRICE_PER_MILLION_USD` lived in `undx_router`. It lives here now and the router
imports it, because the model IDs it is keyed on are the same IDs the provider
config, the retirement check and the drift check all need. Two copies of a price
table drift the same way two copies of a privacy ladder drift, and the second
one keeps working while meaning something else.

2. Money is not a float
-----------------------
Totals accumulate over thousands of calls, so they are stored as integer
**micro-USD** (1e-6). Cents — which is what the Sentinel financial tables use —
is the wrong unit here and would make the whole control vacuous: a measured
Perplexity token cost of $0.00006 rounds to zero cents, so a budget counting
cents would sit at $0.00 while real money left the account. Micro-USD holds that
call as 60 and the cheapest thing we have ever observed is still two orders of
magnitude above the floor.

3. A dollar budget cannot cover providers whose price is unknown
----------------------------------------------------------------
This is the part that decides whether this module is a control or a decoration.
`PRICE_PER_MILLION_USD` has two entries. Five of the seven providers return
`cost_usd: None`, by deliberate policy — an invented price "survives into a
budget decision looking like a measurement". So a dollar budget summed over
`cost_usd` would restrain Meta and *silently never fire* for OpenAI, Claude,
Gemini, DeepSeek or Groq, while reporting itself as enforced.

Three ways out, and the reason for the one chosen:

* Invent prices. Rejected — that is the thing the price table exists to refuse.
* Fail closed: refuse any unpriced provider while a dollar budget is set. That
  is the privacy rule's shape, but the harms are not the same shape. Disclosure
  is irreversible; overspend is visible, bounded and refundable. Failing closed
  here means setting a $50 budget instantly removes five of seven providers, so
  the cost control becomes the outage. Available as
  `UNDX_COST_BUDGET_STRICT=true` for anyone who genuinely needs a hard dollar
  cap and accepts that price.
* Budget the axis that is actually measured everywhere, and say plainly what
  the dollar axis does not cover. Chosen.

Tokens are reported by every adapter — `EveryAdapterReportsUsageTest` pins that
structurally, so a provider added later cannot quietly omit them. So
`UNDX_MONTHLY_TOKEN_BUDGET` is the budget that bites universally, and
`budget_state()` publishes `uncovered_providers` so nobody has to infer from a
quiet dashboard that the dollar ceiling was never watching them.

Where the numbers live
----------------------
One row per (month, provider) in `undx_cost_ledger`, incremented with a single
`ON CONFLICT DO UPDATE ... RETURNING`. Read-then-write would be a race between
nine processes, which is the bug this table exists to fix — the same argument,
and the same SQL shape, as `services/sentinel/rate_limit.py`.

No new infrastructure: there is no Redis in this project (verified against the
live Railway variables), so Postgres is the shared store, exactly as Sentinel
already assumes.

Unlike a rate limiter this does **not** default to off. Sentinel's limiter runs
on every HTTP request, so "costs nothing when unconfigured" is worth a lot
there. This runs once per LLM call — a 0.7 to 9.6 second network round trip. One
local upsert against that is noise, and a ledger that only starts recording when
someone sets a budget would have the budget fire from zero on a month already
half spent. `UNDX_COST_LEDGER_ENABLED=false` is the escape hatch.

Growth is ~7 rows a month, so there is no prune here. Sentinel needed one
because its key space is unbounded; copying that machinery would be cargo.

What this does not promise
--------------------------
A call's cost is known only after it returns, so enforcement is "do not start a
call when the month is already over budget". The overshoot is therefore bounded
by one in-flight call **per worker**, not one call — nine, on the current
Procfile. That is a property of checking before an unpriced action, not a bug to
be fixed with a tighter lock, and it is written down here so nobody reads a
$50 budget as a $50 guarantee.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from services import db as platform_db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- prices

#: USD per million tokens, as (input, output). Only models whose price was read
#: from the vendor's own console or pricing page appear here. A model that is
#: absent is reported with tokens and `cost_usd: None` rather than being costed
#: against a plausible-looking number - an invented price is worse than no
#: price, because it survives into a budget decision looking like a measurement.
#: Meta's figures are from the live console for project 1656198352782001; see
#: UNDX_META_MUSE_CONFIGURATION.md.
#:
#: Moved here from `undx_router` so there is one authority for model pricing.
#: `undx_router.PRICE_PER_MILLION_USD` is this object, re-exported.
PRICE_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.20),
}

#: 1 USD in the ledger's integer unit. See module docstring, point 2.
MICRO_PER_USD = 1_000_000


def price_for(model: str | None) -> tuple[float, float] | None:
    """Verified (input, output) price per million tokens, or None if unknown."""
    if not model:
        return None
    return PRICE_PER_MILLION_USD.get(str(model).strip())


def is_priced(model: str | None) -> bool:
    return price_for(model) is not None


def estimate_cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    """Token-derived cost, or None when the model has no verified price."""
    price = price_for(model)
    if price is None:
        return None
    return round((int(input_tokens) * price[0] + int(output_tokens) * price[1]) / 1_000_000, 6)


def to_micro_usd(usd: float | None) -> int:
    """USD to integer micro-USD, rounded. None becomes 0 — the caller is
    responsible for counting it as uncosted rather than as free."""
    if usd is None:
        return 0
    try:
        return int(round(float(usd) * MICRO_PER_USD))
    except (TypeError, ValueError):
        return 0


def from_micro_usd(micro: int) -> float:
    return round(int(micro) / MICRO_PER_USD, 6)


# ------------------------------------------------------------------ the ledger

LEDGER_TABLE = "undx_cost_ledger"

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    # `cost_micro_usd` is an integer on purpose; see module docstring, point 2.
    # `uncosted_calls` is the honesty column: it says how many calls the dollar
    # total does not include, which is what makes that total readable as a floor
    # rather than as a sum.
    f"""CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL,
        provider TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micro_usd INTEGER NOT NULL DEFAULT 0,
        uncosted_calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # Not decoration: `ON CONFLICT (month, provider) DO UPDATE` requires it, and
    # it is what turns increment-and-read into one atomic statement instead of a
    # read-modify-write race between nine processes.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{LEDGER_TABLE}_month_provider "
    f"ON {LEDGER_TABLE}(month, provider)",
)

_UPSERT_SQL = f"""INSERT INTO {LEDGER_TABLE}
    (month, provider, calls, input_tokens, output_tokens, reasoning_tokens,
     cost_micro_usd, uncosted_calls, updated_at)
    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (month, provider) DO UPDATE SET
        calls = {LEDGER_TABLE}.calls + 1,
        input_tokens = {LEDGER_TABLE}.input_tokens + ?,
        output_tokens = {LEDGER_TABLE}.output_tokens + ?,
        reasoning_tokens = {LEDGER_TABLE}.reasoning_tokens + ?,
        cost_micro_usd = {LEDGER_TABLE}.cost_micro_usd + ?,
        uncosted_calls = {LEDGER_TABLE}.uncosted_calls + ?,
        updated_at = ?
    RETURNING calls, cost_micro_usd, uncosted_calls"""

_READ_SQL = f"""SELECT provider, calls, input_tokens, output_tokens,
    reasoning_tokens, cost_micro_usd, uncosted_calls
    FROM {LEDGER_TABLE} WHERE month = ?"""


_LOCK = threading.Lock()
_schema_ready = False

#: Mirror of the shared ledger for this process only. It is not the source of
#: truth; it is what the budget is enforced against when the database is
#: unreachable, so a ledger outage degrades the control's *accuracy* instead of
#: turning into either an open door or an outage. Same trade, and the same
#: `degraded` counter, as `sentinel.rate_limit`.
_local: dict[str, Any] = {"month": "", "providers": {}}

_STATS: dict[str, Any] = {
    "writes": 0,
    "write_failures": 0,
    "reads": 0,
    "read_failures": 0,
    "degraded": 0,
    "last_error": "",
}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def ledger_enabled() -> bool:
    return _flag("UNDX_COST_LEDGER_ENABLED", True)


def current_month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def _connect():
    return platform_db.connect()


def ensure_schema(conn=None) -> int:
    """Create the ledger table. Idempotent; safe at every boot.

    When no connection is supplied this opens and **commits** its own. That is
    not a style preference: handing this function a caller's open connection
    means the DDL rides that transaction, and if the caller never commits, the
    catalog lock is held while the next connection blocks behind it. That
    failure has already happened in this repo with `ensure_schema(conn)` — a
    route hung on Postgres, and it read as a query problem for a long time.
    """
    own = conn is None
    if own:
        conn = _connect()
    try:
        cur = conn.cursor()
        for statement in _SCHEMA_STATEMENTS:
            cur.execute(statement)
        if own:
            conn.commit()
        return len(_SCHEMA_STATEMENTS)
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # pragma: no cover - close failure is not our story
                pass


def _ensure_schema_once() -> None:
    global _schema_ready
    if _schema_ready:
        return
    ensure_schema()
    with _LOCK:
        _schema_ready = True


def _bump_local(month: str, provider: str, usage: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        if _local["month"] != month:
            _local["month"] = month
            _local["providers"] = {}
        bucket = _local["providers"].setdefault(provider, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "reasoning_tokens": 0, "cost_micro_usd": 0, "uncosted_calls": 0,
        })
        bucket["calls"] += 1
        bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
        bucket["reasoning_tokens"] += int(usage.get("reasoning_tokens") or 0)
        bucket["cost_micro_usd"] += to_micro_usd(usage.get("cost_usd"))
        bucket["uncosted_calls"] += 0 if usage.get("cost_usd") is not None else 1
        return dict(bucket)


def record(usage: dict[str, Any]) -> dict[str, Any]:
    """Add one call to the shared ledger. Never raises.

    A failure to record must not fail the request that already succeeded —
    the money is spent either way, and turning a bookkeeping fault into a user
    error would be the worse outcome. But it must not vanish either, so the
    process-local mirror is always bumped and `stats()["degraded"]` counts how
    often the shared copy was not reached.
    """
    provider = str(usage.get("provider") or "").strip().lower()
    if not provider:
        return {}
    month = current_month()
    local = _bump_local(month, provider, usage)
    if not ledger_enabled():
        return local

    cost_micro = to_micro_usd(usage.get("cost_usd"))
    uncosted = 0 if usage.get("cost_usd") is not None else 1
    inputs = int(usage.get("input_tokens") or 0)
    outputs = int(usage.get("output_tokens") or 0)
    reasoning = int(usage.get("reasoning_tokens") or 0)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    conn = None
    try:
        _ensure_schema_once()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(_UPSERT_SQL, (
            month, provider, inputs, outputs, reasoning, cost_micro, uncosted, stamp,
            inputs, outputs, reasoning, cost_micro, uncosted, stamp,
        ))
        row = cur.fetchone()
        # Committed immediately: holding this row open would block every other
        # worker recording the same provider for the rest of the request.
        conn.commit()
        with _LOCK:
            _STATS["writes"] += 1
        if row:
            return {"calls": int(row[0]), "cost_micro_usd": int(row[1]),
                    "uncosted_calls": int(row[2])}
        return local
    except Exception as exc:  # noqa: BLE001 - see docstring
        with _LOCK:
            _STATS["write_failures"] += 1
            _STATS["degraded"] += 1
            _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("UNDX cost ledger write failed provider=%s error=%s",
                    provider, type(exc).__name__)
        return local
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass


def _local_snapshot(month: str) -> dict[str, dict[str, int]]:
    with _LOCK:
        if _local["month"] != month:
            return {}
        return {name: dict(bucket) for name, bucket in _local["providers"].items()}


def month_snapshot(month: str | None = None) -> dict[str, Any]:
    """Per-provider totals for a month, read once so the routing loop does not
    ask the database the same question seven times.

    `source` is `"ledger"` or `"process"`. A caller that cannot tell which one it
    got cannot tell a $0 month from an unreachable ledger, and those two demand
    opposite reactions.
    """
    month = month or current_month()
    if ledger_enabled():
        conn = None
        try:
            _ensure_schema_once()
            conn = _connect()
            cur = conn.cursor()
            cur.execute(_READ_SQL, (month,))
            providers: dict[str, dict[str, int]] = {}
            for row in cur.fetchall() or []:
                providers[str(row[0])] = {
                    "calls": int(row[1]), "input_tokens": int(row[2]),
                    "output_tokens": int(row[3]), "reasoning_tokens": int(row[4]),
                    "cost_micro_usd": int(row[5]), "uncosted_calls": int(row[6]),
                }
            with _LOCK:
                _STATS["reads"] += 1
            return {"month": month, "providers": providers, "source": "ledger"}
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATS["read_failures"] += 1
                _STATS["degraded"] += 1
                _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("UNDX cost ledger read failed error=%s", type(exc).__name__)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover
                    pass
    return {"month": month, "providers": _local_snapshot(month), "source": "process"}


def stats() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATS)


# ------------------------------------------------------------------- budgets

#: Env names, listed once so `.env.example`, the drift check and the tests all
#: read the same set rather than three hand-maintained copies.
BUDGET_ENV_VARS: tuple[str, ...] = (
    "UNDX_MONTHLY_COST_BUDGET_USD",
    "UNDX_PROVIDER_COST_BUDGET_USD",
    "UNDX_MONTHLY_TOKEN_BUDGET",
    "UNDX_PROVIDER_TOKEN_BUDGET",
    "UNDX_COST_BUDGET_STRICT",
    "UNDX_COST_LEDGER_ENABLED",
)


def _positive_number(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _parse_per_provider(raw: str) -> dict[str, float]:
    """`openai=5,meta=20.5` to a mapping. Silently drops malformed entries.

    Deliberately not JSON. An operator setting this in a Railway variable box
    gets one line with no quoting rules to lose, and a typo costs one provider's
    budget rather than the whole map — a malformed JSON document would parse to
    nothing and turn every configured budget off at once, which is the failure
    mode a cost control can least afford.
    """
    out: dict[str, float] = {}
    for chunk in raw.replace(";", ",").split(","):
        if "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip().lower()
        amount = _positive_number(value.strip())
        if name and amount > 0:
            out[name] = amount
    return out


def global_cost_budget_usd() -> float:
    return _positive_number(_env("UNDX_MONTHLY_COST_BUDGET_USD"))


def provider_cost_budgets_usd() -> dict[str, float]:
    return _parse_per_provider(_env("UNDX_PROVIDER_COST_BUDGET_USD"))


def global_token_budget() -> int:
    return int(_positive_number(_env("UNDX_MONTHLY_TOKEN_BUDGET")))


def provider_token_budgets() -> dict[str, int]:
    return {name: int(value) for name, value in
            _parse_per_provider(_env("UNDX_PROVIDER_TOKEN_BUDGET")).items()}


def strict_cost_budget() -> bool:
    """Refuse providers whose price is unknown while a dollar budget is set.

    Off by default. See module docstring, point 3: on, a $50 budget removes five
    of seven providers the moment it is set, because their spend cannot be
    measured. That is a defensible choice for someone who needs a hard cap and
    an indefensible default.
    """
    return _flag("UNDX_COST_BUDGET_STRICT", False)


def budgets_configured() -> bool:
    return bool(global_cost_budget_usd() or provider_cost_budgets_usd()
                or global_token_budget() or provider_token_budgets())


def _usd(value: float) -> str:
    """Enough decimals to be true, never fewer than two.

    `f"${limit:.2f}"` renders a $0.003 budget as "$0.00", so the refusal message
    would read "$0.0037 of $0.00 spent" — which says the limit was zero, is
    false, and is the sort of line that gets a real control dismissed as a bug.
    Fixed-width money formatting is right for invoices and wrong here, because
    the amounts this system deals in are genuinely sub-cent: the cheapest call
    measured cost $0.00006.
    """
    whole, _, frac = f"{float(value):.6f}".partition(".")
    return f"${whole}.{frac.rstrip('0').ljust(2, '0')}"


def _totals(snapshot: dict[str, Any]) -> tuple[int, int, int]:
    """(cost_micro_usd, tokens, uncosted_calls) across every provider."""
    cost = tokens = uncosted = 0
    for bucket in (snapshot.get("providers") or {}).values():
        cost += int(bucket.get("cost_micro_usd") or 0)
        tokens += int(bucket.get("input_tokens") or 0) + int(bucket.get("output_tokens") or 0)
        uncosted += int(bucket.get("uncosted_calls") or 0)
    return cost, tokens, uncosted


def refusal(snapshot: dict[str, Any], provider: str, model: str | None = None) -> str:
    """Empty if this provider may be called, else why not.

    Checked against a snapshot the caller already read, not against a fresh
    query per provider: a seven-provider plan would otherwise ask the same
    question seven times per request.

    The order of the checks is the order of how badly an operator wants to know:
    a global ceiling hit means everything stops, a provider ceiling means one
    lane stops, and the strict-mode refusal means the call was blocked for being
    unmeasurable rather than for being expensive. That last distinction is the
    whole reason it has its own sentence — "over budget" would be a lie.
    """
    name = str(provider or "").strip().lower()
    spent_micro, spent_tokens, _ = _totals(snapshot)
    bucket = (snapshot.get("providers") or {}).get(name) or {}
    provider_micro = int(bucket.get("cost_micro_usd") or 0)
    provider_tokens = (int(bucket.get("input_tokens") or 0)
                       + int(bucket.get("output_tokens") or 0))

    limit = global_cost_budget_usd()
    if limit and from_micro_usd(spent_micro) >= limit:
        return (f"cost_budget: {_usd(from_micro_usd(spent_micro))} of {_usd(limit)} "
                f"spent this month across all providers")

    per_provider = provider_cost_budgets_usd().get(name, 0.0)
    if per_provider and from_micro_usd(provider_micro) >= per_provider:
        return (f"cost_budget: {name} has spent {_usd(from_micro_usd(provider_micro))} "
                f"of its {_usd(per_provider)} monthly budget")

    token_limit = global_token_budget()
    if token_limit and spent_tokens >= token_limit:
        return (f"token_budget: {spent_tokens} of {token_limit} tokens used this "
                f"month across all providers")

    provider_token_limit = provider_token_budgets().get(name, 0)
    if provider_token_limit and provider_tokens >= provider_token_limit:
        return (f"token_budget: {name} has used {provider_tokens} of its "
                f"{provider_token_limit} monthly tokens")

    if strict_cost_budget() and (limit or per_provider) and not is_priced(model):
        return (f"cost_budget_strict: {name}/{model or 'unknown model'} has no "
                f"verified price, so its spend cannot be counted against a "
                f"dollar budget")

    return ""


def uncovered_providers(providers: Any) -> list[str]:
    """Those a dollar budget cannot restrain, because their model has no price.

    Takes the provider->model mapping from the caller rather than importing
    `undx_router`, which imports this module. The cycle is the point: the price
    table belongs to cost, the model choice belongs to routing, and neither
    should have to know the other's shape to answer this.
    """
    out = []
    for name, model in dict(providers or {}).items():
        if not is_priced(model):
            out.append(str(name))
    return sorted(out)


def budget_state(snapshot: dict[str, Any] | None = None,
                 providers: Any = None) -> dict[str, Any]:
    """What is configured, what has been spent, and what the dollar ceiling is
    not watching. The last of those is why this returns a dict rather than a
    number: a budget that reports only "spent / limit" reads as complete
    coverage, and here it is not."""
    snapshot = snapshot if snapshot is not None else month_snapshot()
    spent_micro, spent_tokens, uncosted = _totals(snapshot)
    limit = global_cost_budget_usd()
    token_limit = global_token_budget()
    uncovered = uncovered_providers(providers) if providers else []
    return {
        "month": snapshot.get("month"),
        "source": snapshot.get("source"),
        "spend_usd": from_micro_usd(spent_micro),
        "spend_is_a_floor": uncosted > 0,
        "uncosted_calls": uncosted,
        "tokens": spent_tokens,
        "cost_budget_usd": limit or None,
        "cost_budget_remaining_usd": (round(limit - from_micro_usd(spent_micro), 6)
                                      if limit else None),
        "token_budget": token_limit or None,
        "token_budget_remaining": (max(0, token_limit - spent_tokens)
                                   if token_limit else None),
        "provider_cost_budgets_usd": provider_cost_budgets_usd(),
        "provider_token_budgets": provider_token_budgets(),
        "strict": strict_cost_budget(),
        "enforced": budgets_configured(),
        # The honesty field. A dollar budget is blind to these providers unless
        # strict mode is on, and nobody should have to deduce that from a
        # dashboard that stays quiet.
        "uncovered_providers": uncovered,
        "providers": snapshot.get("providers") or {},
    }


def reset_for_tests() -> None:
    """Test-only: drop the process mirror, the stats and the schema latch."""
    global _schema_ready
    with _LOCK:
        _local["month"] = ""
        _local["providers"] = {}
        for key in _STATS:
            _STATS[key] = "" if key == "last_error" else 0
        _schema_ready = False


__all__ = [
    "PRICE_PER_MILLION_USD", "MICRO_PER_USD", "LEDGER_TABLE", "BUDGET_ENV_VARS",
    "price_for", "is_priced", "estimate_cost_usd", "to_micro_usd", "from_micro_usd",
    "ensure_schema", "record", "month_snapshot", "stats", "current_month",
    "ledger_enabled", "global_cost_budget_usd", "provider_cost_budgets_usd",
    "global_token_budget", "provider_token_budgets", "strict_cost_budget",
    "budgets_configured", "refusal", "uncovered_providers", "budget_state",
    "reset_for_tests",
]
