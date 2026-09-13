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


# --------------------------------------------------------------- call kinds

#: What sort of AI call the money was spent on (§21). A ledger keyed only on
#: provider can say "OpenAI cost $40" and cannot say whether that was chat,
#: embeddings or image generation - which is the one question a spend decision
#: turns on, because those three have different unit economics and different
#: owners. The census found four non-chat paid call sites (embeddings, images,
#: search, translation) that reached no ledger at all; this column is where they
#: land.
CALL_KIND_CHAT = "chat"
CALL_KIND_REASONING = "reasoning"
CALL_KIND_RESEARCH = "research"
CALL_KIND_EMBEDDING = "embedding"
CALL_KIND_IMAGE = "image"
CALL_KIND_TRANSCRIPTION = "transcription"
CALL_KIND_RERANK = "rerank"
CALL_KIND_MODERATION = "moderation"
CALL_KIND_TRANSLATION = "translation"

#: Where a call whose kind this module does not recognise is recorded. Not a
#: member of `KNOWN_CALL_KINDS`: it is the bucket for things that should not
#: exist, so a caller cannot select it and a query for it is a bug report.
CALL_KIND_UNKNOWN = "unknown"

KNOWN_CALL_KINDS: frozenset[str] = frozenset({
    CALL_KIND_CHAT, CALL_KIND_REASONING, CALL_KIND_RESEARCH,
    CALL_KIND_EMBEDDING, CALL_KIND_IMAGE, CALL_KIND_TRANSCRIPTION,
    CALL_KIND_RERANK, CALL_KIND_MODERATION, CALL_KIND_TRANSLATION,
})


def is_known_call_kind(kind: Any) -> bool:
    """Whether `kind` is a kind this module can account for.

    Separate from `normalize_call_kind` on the same reasoning as
    `undx_privacy.is_known`: a function that maps an unrecognised name onto a
    working default cannot also be used to *detect* that the name was
    unrecognised, and a caller that wants to refuse a typo needs the second
    question rather than the first.
    """
    return isinstance(kind, str) and kind.strip().lower() in KNOWN_CALL_KINDS


def normalize_call_kind(kind: Any) -> str:
    """Ledger value for a caller's declared kind.

    Absent and unrecognised are deliberately **different** answers, and the
    difference is the whole point of the function:

    * **Absent** (`None`, `""`) becomes `chat`. Every call site that existed
      before this column was chat, so `chat` is what the backfill writes and
      what the column defaults to; any other choice would make the historical
      rows disagree with the code that wrote them.
    * **Present but unrecognised** becomes `unknown`, never `chat`. Mapping a
      typo onto the largest existing bucket is how `embedding` spend would get
      laundered into the chat total and stay invisible - the failure this column
      exists to end. `unknown` is ugly in a report, which is the correct amount
      of ugly for spend nobody classified (§22).

    The default is a compatibility statement about history, not a licence for new
    callers to omit the argument: `test_no_ledger_write_omits_its_call_kind`
    fails on any `record()` call site that leaves it out.
    """
    if kind is None:
        return CALL_KIND_CHAT
    text = str(kind).strip().lower()
    if not text:
        return CALL_KIND_CHAT
    return text if text in KNOWN_CALL_KINDS else CALL_KIND_UNKNOWN


# ------------------------------------------------------------------- models

#: Kinds where the money is spent against a *named model*, so a missing name is a
#: gap. `research` and `translation` are absent deliberately: a Brave query or a
#: Google Translate request bills against an endpoint, and there is no model to
#: name. Their empty `model` is the accurate value, not a hole.
#:
#: This lives here rather than in `undx_capabilities.CAPABILITIES`, where a
#: per-kind declaration table already exists, for one reason: that module imports
#: this one. A flag consulted by `record()` cannot live on the far side of that
#: edge without inverting it.
MODEL_BEARING_CALL_KINDS: frozenset[str] = frozenset({
    CALL_KIND_CHAT, CALL_KIND_REASONING, CALL_KIND_EMBEDDING, CALL_KIND_IMAGE,
    CALL_KIND_TRANSCRIPTION, CALL_KIND_RERANK, CALL_KIND_MODERATION,
})

#: What goes in the column when a model-bearing call did not say which model. The
#: same shape as `CALL_KIND_UNKNOWN` and for the same reason: it is ugly in a
#: report, which is the correct amount of ugly for spend nobody attributed.
MODEL_UNDECLARED = "undeclared"

#: No model dimension at all, for the kinds that have none. Kept distinct from
#: `MODEL_UNDECLARED` because collapsing them would put every translation row in a
#: per-model report under a name that reads as a defect, and then the one name that
#: *is* a defect would stop standing out.
MODEL_NOT_APPLICABLE = ""

#: A bound on a unique-index key reachable from a public entry point, not a guess
#: about how long model names get. The longest real name in `PRICE_PER_MILLION_USD`
#: is under 40 characters; some providers namespace theirs
#: (`accounts/fireworks/models/...`), so the cap is generous and its job is only to
#: stop `record({"model": <64KB>})` from becoming an index entry.
_MODEL_NAME_LIMIT = 120


def normalize_model(model: Any, kind: Any = CALL_KIND_CHAT) -> str:
    """Ledger value for a caller's declared model, given the kind of call.

    Three outcomes, and the distinction between the last two is the point:

    * A name becomes that name, **lowercased**. Not cosmetic. The `provider`
      column is already lowercased, and `GPT-4o` alongside `gpt-4o` would be two
      rows under the widened unique index - splitting one model's spend in half
      and making both halves look affordable. Casing is the difference most likely
      to vary between call sites writing the same model.
    * Absent on a kind that has no models (`research`, `translation`) becomes
      `""`. There is nothing to attribute; the column is empty because the
      question does not apply.
    * Absent on a kind that *does* have models becomes `"undeclared"`. Something
      chose a model and did not say which, which is a gap in the accounting and
      should read as one.
    """
    text = "" if model is None else str(model).strip().lower()[:_MODEL_NAME_LIMIT]
    if text:
        return text
    kind_text = normalize_call_kind(kind)
    if kind_text in MODEL_BEARING_CALL_KINDS:
        return MODEL_UNDECLARED
    return MODEL_NOT_APPLICABLE


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
        call_kind TEXT NOT NULL DEFAULT '{CALL_KIND_CHAT}',
        -- `NOT NULL DEFAULT ''` is load-bearing, not defensive. PostgreSQL treats
        -- NULLs as distinct in a unique index, so a nullable `model` would make
        -- every upsert for an unnamed model miss its own conflict target and
        -- INSERT a fresh row instead of incrementing - the ledger would grow one
        -- row per call and every total would still be right, so nothing would look
        -- broken until someone counted the rows. SQLite is the same for indexes.
        model TEXT NOT NULL DEFAULT '',
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micro_usd INTEGER NOT NULL DEFAULT 0,
        uncosted_calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # Not decoration: `ON CONFLICT (month, provider, call_kind, model) DO UPDATE`
    # requires it, and it is what turns increment-and-read into one atomic
    # statement instead of a read-modify-write race between nine processes.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{LEDGER_TABLE}_month_provider_kind_model "
    f"ON {LEDGER_TABLE}(month, provider, call_kind, model)",
    # The two narrower predecessors, dropped in the same run that creates their
    # replacement and **after** it. Each is strictly narrower than the index above,
    # and while one stands, the first row that differs only in the new column
    # collides and the write fails: `(month, provider)` blocked the first embedding
    # row for a provider that already had a chat row, and
    # `(month, provider, call_kind)` blocks the second *model* for a provider's
    # chat spend. So both have to go.
    #
    # They go last because if creating the wider index fails, the table must still
    # have *an* index - without one the upsert is a runtime error and every write
    # fails, which is worse than a write that fails only for the new dimension.
    # Order is asserted, not merely intended; see
    # `test_the_narrow_index_is_dropped_only_after_the_wide_one_exists`.
    f"DROP INDEX IF EXISTS ux_{LEDGER_TABLE}_month_provider_kind",
    f"DROP INDEX IF EXISTS ux_{LEDGER_TABLE}_month_provider",
)

#: Added by `ensure_schema` rather than by a statement in the tuple above,
#: because `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is Postgres-only - SQLite
#: raises on the second run - so the guard has to be an introspection, not a
#: keyword. `CREATE TABLE` above already carries the column, so this is a no-op
#: on a fresh database and the migration path only for tables that predate it.
_LEDGER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("call_kind", f"TEXT NOT NULL DEFAULT '{CALL_KIND_CHAT}'"),
    ("model", "TEXT NOT NULL DEFAULT ''"),
)

#: Every row that predates the `model` column was written by a call that had a
#: model and no column to put it in, so `''` - which the `ADD COLUMN` default gives
#: them - would claim the wrong thing: that those kinds have no model dimension.
#: Restricted to the model-bearing kinds so the `research` and `translation` rows
#: already in the table, which genuinely have no model, keep the empty string.
#:
#: Idempotent, and safe against the collision it looks like it might cause. New
#: writes never produce `''` for a model-bearing kind - `normalize_model` returns
#: `undeclared` instead - so a row this statement would move can only be a
#: pre-column row, and it can only be moving onto a name that is not yet taken for
#: that (month, provider, kind). After the first run there is nothing to match.
_LEDGER_BACKFILL: tuple[str, ...] = (
    f"UPDATE {LEDGER_TABLE} SET model = '{MODEL_UNDECLARED}' "
    f"WHERE model = '' AND call_kind IN ("
    + ", ".join(f"'{kind}'" for kind in sorted(MODEL_BEARING_CALL_KINDS))
    + ")",
)

_UPSERT_SQL = f"""INSERT INTO {LEDGER_TABLE}
    (month, provider, call_kind, model, calls, input_tokens, output_tokens,
     reasoning_tokens, cost_micro_usd, uncosted_calls, updated_at)
    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (month, provider, call_kind, model) DO UPDATE SET
        calls = {LEDGER_TABLE}.calls + 1,
        input_tokens = {LEDGER_TABLE}.input_tokens + ?,
        output_tokens = {LEDGER_TABLE}.output_tokens + ?,
        reasoning_tokens = {LEDGER_TABLE}.reasoning_tokens + ?,
        cost_micro_usd = {LEDGER_TABLE}.cost_micro_usd + ?,
        uncosted_calls = {LEDGER_TABLE}.uncosted_calls + ?,
        updated_at = ?
    RETURNING calls, cost_micro_usd, uncosted_calls"""

_READ_SQL = f"""SELECT provider, call_kind, calls, input_tokens, output_tokens,
    reasoning_tokens, cost_micro_usd, uncosted_calls, model
    FROM {LEDGER_TABLE} WHERE month = ?"""


_LOCK = threading.Lock()
_schema_ready = False

#: Mirror of the shared ledger for this process only. It is not the source of
#: truth; it is what the budget is enforced against when the database is
#: unreachable, so a ledger outage degrades the control's *accuracy* instead of
#: turning into either an open door or an outage. Same trade, and the same
#: `degraded` counter, as `sentinel.rate_limit`.
_local: dict[str, Any] = {"month": "", "providers": {}, "kinds": {}}

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


def _ledger_columns(cur) -> set[str]:
    """Column names currently on the ledger table, lowercased."""
    if platform_db.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ?",
            (LEDGER_TABLE,),
        )
    else:
        cur.execute(f"PRAGMA table_info({LEDGER_TABLE})")
    found: set[str] = set()
    for row in cur.fetchall() or []:
        # PRAGMA puts the name at index 1; information_schema at index 0.
        name = row[0] if platform_db.IS_POSTGRES else row[1]
        found.add(str(name).lower())
    return found


def ensure_schema(conn=None) -> int:
    """Create the ledger table. Idempotent; safe at every boot.

    When no connection is supplied this opens and **commits** its own. That is
    not a style preference: handing this function a caller's open connection
    means the DDL rides that transaction, and if the caller never commits, the
    catalog lock is held while the next connection blocks behind it. That
    failure has already happened in this repo with `ensure_schema(conn)` — a
    route hung on Postgres, and it read as a query problem for a long time.

    Statement order matters and is asserted by
    `test_the_narrow_index_is_dropped_only_after_the_wide_one_exists`: the
    `call_kind` column is added before the index that references it, and the old
    narrow index is dropped only after the wide one is in place.
    """
    own = conn is None
    if own:
        conn = _connect()
    try:
        cur = conn.cursor()
        # The table first, so the column check below has something to inspect.
        cur.execute(_SCHEMA_STATEMENTS[0])
        existing = _ledger_columns(cur)
        for column, definition in _LEDGER_COLUMNS:
            if column in existing:
                continue
            cur.execute(f"ALTER TABLE {LEDGER_TABLE} ADD COLUMN {column} {definition}")
        for statement in _SCHEMA_STATEMENTS[1:]:
            cur.execute(statement)
        for statement in _LEDGER_BACKFILL:
            # Guarded on its own, and the only statement here that is. The rest of
            # this function is structural: if the table or the index is not there,
            # nothing can be recorded and the exception should reach the caller. The
            # backfill is historical attribution, so failing it leaves pre-column
            # rows reading as `''` instead of `undeclared` - a cosmetic
            # misattribution of history - and that is a strictly better outcome than
            # refusing to boot the accounting layer over it.
            try:
                cur.execute(statement)
            except Exception as exc:  # noqa: BLE001 - see comment above
                log.warning("UNDX cost ledger model backfill skipped error=%s",
                            type(exc).__name__)
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


def _empty_bucket() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
            "reasoning_tokens": 0, "cost_micro_usd": 0, "uncosted_calls": 0}


def _cost_fields(usage: dict[str, Any]) -> tuple[int, int]:
    """`(cost_micro_usd, uncosted)` for one call, from whichever form it arrived in.

    Two forms exist because the two kinds of caller genuinely know different things.
    A chat provider reports a price in dollars and `cost_usd` is the honest field for
    it. Non-chat spend is priced from `undx_capabilities`, which computes in integer
    micro-USD already, and routing that back through a float only to convert it again
    would add a rounding step for nothing.

    Extracted into one function rather than written twice because the ledger and the
    process mirror both have to answer this question, and answering it separately is
    how the degraded path would start disagreeing with the durable one about what a
    call cost. Before this, `_apply` and `record` each derived the pair from
    `cost_usd` independently, so adding the micro form to one of them would have
    silently created that split.

    Absent is uncosted, and uncosted is not free — a call whose price nobody knows
    contributes 0 to the dollar total and 1 to the count of calls that total excludes.
    An explicit `cost_micro_usd=0` is a *known* zero (a keyless free endpoint) and is
    not counted as uncosted, which is the distinction §34 turns on.

    A *malformed* price is an unknown price, in both forms. This is why the dollar
    branch does its own float conversion instead of leaning on :func:`to_micro_usd`,
    which returns 0 for junk by documented contract and leaves the uncosted decision
    to its caller. Deferring to it here would have made the two forms disagree about
    the same bad input: `cost_micro_usd="?"` recorded as unknown while
    `cost_usd="?"` recorded as free. Nothing produces junk today — `_normalise_usage`
    already rejects a provider-reported cost it cannot parse — but `record()` is a
    public entry point, and "unparseable therefore $0.00" is exactly the reading §34
    forbids.
    """
    micro = usage.get("cost_micro_usd")
    if micro is not None:
        try:
            return int(micro), 0
        except (TypeError, ValueError):
            return 0, 1
    usd = usage.get("cost_usd")
    if usd is None:
        return 0, 1
    try:
        return int(round(float(usd) * MICRO_PER_USD)), 0
    except (TypeError, ValueError):
        return 0, 1


def _apply(bucket: dict[str, int], usage: dict[str, Any]) -> None:
    cost_micro, uncosted = _cost_fields(usage)
    bucket["calls"] += 1
    bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    bucket["reasoning_tokens"] += int(usage.get("reasoning_tokens") or 0)
    bucket["cost_micro_usd"] += cost_micro
    bucket["uncosted_calls"] += uncosted


def _bump_local(month: str, provider: str, kind: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Bump the process mirror twice: once per provider, once per kind.

    Two flat tallies rather than the provider x kind cross-product, because the
    mirror is the *degraded* path - what it exists to do is keep a budget
    enforceable when the ledger is unreachable, and every budget in this module
    is per-provider or global. The cross-product lives in the ledger, which is
    where a spend report reads from. Keeping the `providers` shape byte-identical
    also means no budget, refusal or dashboard changes meaning when the ledger is
    down, which is the moment to be changing the fewest things.
    """
    with _LOCK:
        if _local["month"] != month:
            _local["month"] = month
            _local["providers"] = {}
            _local["kinds"] = {}
        bucket = _local["providers"].setdefault(provider, _empty_bucket())
        _apply(bucket, usage)
        _apply(_local.setdefault("kinds", {}).setdefault(kind, _empty_bucket()), usage)
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
    kind = normalize_call_kind(usage.get("call_kind"))
    if kind == CALL_KIND_UNKNOWN:
        # Loud, because this is §22's failure mode arriving: spend that reached
        # the ledger without a classification anyone chose. It is still recorded
        # - dropping the row would trade an unclassified dollar for a missing
        # one - but it is recorded under a name that reads as a defect.
        log.warning("UNDX cost ledger unrecognised call_kind provider=%s declared=%r",
                    provider, usage.get("call_kind"))
    model = normalize_model(usage.get("model"), kind)
    if model == MODEL_UNDECLARED:
        # Quieter than the `unknown` kind above, and deliberately so. An
        # unclassified *kind* is spend nobody categorised; an unnamed model is
        # spend correctly categorised whose attribution is missing. Both are gaps,
        # only the first is §22. Logged at all because every model-bearing call
        # site in this repo does pass a model today, so this line firing means a
        # new one arrived without one.
        log.info("UNDX cost ledger model undeclared provider=%s kind=%s", provider, kind)
    month = current_month()
    local = _bump_local(month, provider, kind, usage)
    if not ledger_enabled():
        return local

    cost_micro, uncosted = _cost_fields(usage)
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
            month, provider, kind, model,
            inputs, outputs, reasoning, cost_micro, uncosted, stamp,
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


def _local_snapshot(month: str, axis: str = "providers") -> dict[str, dict[str, int]]:
    with _LOCK:
        if _local["month"] != month:
            return {}
        return {name: dict(bucket) for name, bucket in (_local.get(axis) or {}).items()}


def month_snapshot(month: str | None = None) -> dict[str, Any]:
    """Per-provider totals for a month, read once so the routing loop does not
    ask the database the same question seven times.

    `source` is `"ledger"` or `"process"`. A caller that cannot tell which one it
    got cannot tell a $0 month from an unreachable ledger, and those two demand
    opposite reactions.

    `providers` is summed **across** call kinds and models, and so means exactly
    what it meant before either column existed - every budget, refusal and
    dashboard reading it keeps working untouched, and a provider's total does not
    silently shrink the day embeddings start being recorded or the day one
    provider's spend splits across two model names. `kinds` and `models` are
    reported alongside rather than instead: adding a dimension to a measurement
    must not change the measurement.

    That invariant is the entire risk of widening the key. Each new column turns
    one row into several, so a reader that assigned instead of accumulating would
    report the *last* row it happened to see as the provider's total. This loop
    accumulates, and `test_a_providers_total_survives_the_model_split` is what keeps
    it accumulating.

    `models` is keyed `provider/model` and omits rows whose `model` is empty - the
    kinds that have no model to name. `undeclared` *is* included, because a
    per-model report that hides unattributed spend is the report that lets it stay
    unattributed.
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
            kinds: dict[str, dict[str, int]] = {}
            models: dict[str, dict[str, int]] = {}
            for row in cur.fetchall() or []:
                values = {
                    "calls": int(row[2]), "input_tokens": int(row[3]),
                    "output_tokens": int(row[4]), "reasoning_tokens": int(row[5]),
                    "cost_micro_usd": int(row[6]), "uncosted_calls": int(row[7]),
                }
                axes = [(providers, str(row[0])), (kinds, str(row[1]))]
                model = str(row[8] or "")
                if model:
                    # Keyed `provider/model`, not `model`. Model names are not
                    # globally unique - an open-weights model is served by several
                    # providers at different prices - and merging them would produce
                    # a per-model total that belongs to no bill anyone receives.
                    axes.append((models, f"{row[0]}/{model}"))
                for target, key in axes:
                    bucket = target.setdefault(key, _empty_bucket())
                    for field, amount in values.items():
                        bucket[field] += amount
            with _LOCK:
                _STATS["reads"] += 1
            return {"month": month, "providers": providers, "kinds": kinds,
                    "models": models, "source": "ledger"}
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
    # `models` is empty rather than absent, and empty rather than populated. The
    # process mirror does not carry a per-model tally on the same reasoning as
    # `_bump_local`: the mirror exists to keep a *budget* enforceable during an
    # outage, and no budget in this module is per-model. Present-and-empty keeps the
    # dict shape identical in both branches so a caller cannot get an AttributeError
    # only during an incident; `source` is how it tells empty from unavailable.
    return {"month": month, "providers": _local_snapshot(month),
            "kinds": _local_snapshot(month, axis="kinds"), "models": {},
            "source": "process"}


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
        _local["kinds"] = {}
        for key in _STATS:
            _STATS[key] = "" if key == "last_error" else 0
        _schema_ready = False


__all__ = [
    "CALL_KIND_CHAT", "CALL_KIND_REASONING", "CALL_KIND_RESEARCH",
    "CALL_KIND_EMBEDDING", "CALL_KIND_IMAGE", "CALL_KIND_TRANSCRIPTION",
    "CALL_KIND_RERANK", "CALL_KIND_MODERATION", "CALL_KIND_TRANSLATION",
    "CALL_KIND_UNKNOWN", "KNOWN_CALL_KINDS", "is_known_call_kind",
    "normalize_call_kind",
    "MODEL_BEARING_CALL_KINDS", "MODEL_UNDECLARED", "MODEL_NOT_APPLICABLE",
    "normalize_model",
    "PRICE_PER_MILLION_USD", "MICRO_PER_USD", "LEDGER_TABLE", "BUDGET_ENV_VARS",
    "price_for", "is_priced", "estimate_cost_usd", "to_micro_usd", "from_micro_usd",
    "ensure_schema", "record", "month_snapshot", "stats", "current_month",
    "ledger_enabled", "global_cost_budget_usd", "provider_cost_budgets_usd",
    "global_token_budget", "provider_token_budgets", "strict_cost_budget",
    "budgets_configured", "refusal", "uncovered_providers", "budget_state",
    "reset_for_tests",
]
