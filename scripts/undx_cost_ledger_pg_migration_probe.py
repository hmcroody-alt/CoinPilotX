"""Verify the `call_kind` and `model` ledger migrations on a real Postgres.

Why this exists as a script and not a test: the pytest suite runs on SQLite, so
it exercises `PRAGMA table_info` and SQLite's `ON CONFLICT`. Production is
Postgres, where three things differ and none of them are visible from the SQLite
run:

  * `_ledger_columns` takes the `information_schema` branch, which passes a `?`
    placeholder that something has to rewrite to `%s`;
  * `ON CONFLICT (month, provider, call_kind, model)` resolves against a real
    unique index rather than SQLite's more forgiving matching;
  * `INTEGER PRIMARY KEY AUTOINCREMENT` and `datetime('now')` are rewritten by
    `services/db.py` on the way out.

The `model` column adds a fourth, and it is the reason this script grew a second
half. Two hazards, and running this file with `NOT NULL DEFAULT ''` removed shows
which one is which - eight checks fail and none of them is a row count:

  * **Measured.** `ADD COLUMN model TEXT` with no default leaves every row that
    predates the column NULL. The backfill's `WHERE model = ''` cannot match a
    NULL, so those rows are stranded, and `month_snapshot`'s `str(row[8] or "")`
    then maps NULL to `''` - which is the value that *means* "this kind has no
    model", as research and translation do. Missing history quietly becomes
    asserted absence.
  * **Latent.** Postgres treats NULLs as distinct in a unique index, so a writer
    that put a real NULL here would miss its own conflict target and INSERT on
    every call, growing one row per call while every total stayed exactly correct.
    Not currently reachable - `normalize_model` never returns None - so it needs a
    writer that bypasses it: raw SQL, a migration, another service.

Neither is visible from the SQLite suite. SQLite's unique indexes treat NULLs as
distinct too, but the pytest fixtures always build the table fresh from
`CREATE TABLE`, so the migration path where the first hazard lives is never taken
there at all. So the assertion belongs here, against a real Postgres, or nowhere.

Three migration origins are exercised, not one:

  * **original** - no `call_kind`, no `model`, `(month, provider)` index. The
    shape production had before any of this work.
  * **intermediate** - `call_kind` present, `model` absent,
    `(month, provider, call_kind)` index. The shape production has *right now*,
    and therefore the transition that is actually about to happen. It is also the
    only origin where the backfill's `call_kind IN (...)` predicate does any
    work, because it is the only one that can already hold a `research` row.
  * **fresh** - `CREATE TABLE` straight from `_SCHEMA_STATEMENTS[0]`.

Run against a throwaway container. Never against production:
    DATABASE_URL=postgresql://postgres:probe@127.0.0.1:55433/probe \
        python3 scripts/undx_cost_ledger_pg_migration_probe.py
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True

DB_URL = os.environ.get("DATABASE_URL", "")
if "127.0.0.1" not in DB_URL and "localhost" not in DB_URL:
    raise SystemExit(
        "refusing to run: DATABASE_URL must point at a local throwaway Postgres, "
        f"got {DB_URL!r}"
    )

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as platform_db  # noqa: E402
from services import undx_cost  # noqa: E402

assert platform_db.IS_POSTGRES, "probe is pointless unless the driver is Postgres"

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {actual!r}"
          + ("" if ok else f"  (expected {expected!r})"))
    if not ok:
        FAILURES.append(f"{label}: got {actual!r}, expected {expected!r}")


def q(sql, params=()):
    conn = platform_db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except Exception:
            return []
    finally:
        conn.commit()
        conn.close()


def exec_(sql, params=()):
    q(sql, params)


T = undx_cost.LEDGER_TABLE


def build_old_shaped_table() -> None:
    """The original schema, verbatim minus `call_kind` and minus `model`."""
    exec_(f"DROP TABLE IF EXISTS {T}")
    exec_(f"""CREATE TABLE {T} (
        id SERIAL PRIMARY KEY,
        month TEXT NOT NULL,
        provider TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micro_usd INTEGER NOT NULL DEFAULT 0,
        uncosted_calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    exec_(f"CREATE UNIQUE INDEX ux_{T}_month_provider ON {T}(month, provider)")
    # The one row production holds, as measured (census finding R-e).
    exec_(f"INSERT INTO {T} (month, provider, calls, input_tokens, output_tokens,"
          f" reasoning_tokens, cost_micro_usd, uncosted_calls, updated_at)"
          f" VALUES ('2026-09', 'openai', 13, 0, 0, 0, 0, 13, CURRENT_TIMESTAMP)")


def build_intermediate_shaped_table() -> None:
    """The shape production is in *today*: `call_kind` landed, `model` has not.

    Seeded with one row per interesting kind rather than one row total, because
    this is the only origin that can distinguish the backfill working from the
    backfill running: `research` genuinely has no model and must keep `''`, while
    `embedding` and `chat` had a model and nowhere to record it, so they must move
    to `undeclared`. A backfill with no `call_kind` predicate passes every other
    check in this file and fails only here.
    """
    exec_(f"DROP TABLE IF EXISTS {T}")
    exec_(f"""CREATE TABLE {T} (
        id SERIAL PRIMARY KEY,
        month TEXT NOT NULL,
        provider TEXT NOT NULL,
        call_kind TEXT NOT NULL DEFAULT 'chat',
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micro_usd INTEGER NOT NULL DEFAULT 0,
        uncosted_calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    exec_(f"CREATE UNIQUE INDEX ux_{T}_month_provider_kind "
          f"ON {T}(month, provider, call_kind)")
    for provider, kind, calls in (
        ("openai", "chat", 13),
        ("openai", "embedding", 41),
        ("brave", "research", 7),
        ("google", "translation", 2),
    ):
        exec_(f"INSERT INTO {T} (month, provider, call_kind, calls, input_tokens,"
              f" output_tokens, reasoning_tokens, cost_micro_usd, uncosted_calls,"
              f" updated_at) VALUES ('2026-09', %s, %s, %s, 0, 0, 0, 0, %s,"
              f" CURRENT_TIMESTAMP)", (provider, kind, calls, calls))


def indexes() -> set[str]:
    return {r[0] for r in q(
        "SELECT indexname FROM pg_indexes WHERE tablename = %s", (T,))}


def columns() -> list[str]:
    return [r[0] for r in q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (T,))]


def column_facts(name: str) -> tuple:
    rows = q("SELECT is_nullable, column_default FROM information_schema.columns "
             "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
             (T, name))
    if not rows:
        return ("MISSING", "MISSING")
    return (rows[0][0], rows[0][1])


#: `model` left at its default is a named model. `model=NO_KEY` omits the key
#: entirely - a caller that never knew about the dimension. `model=None` passes it
#: explicitly as nothing. Those last two are different code paths into
#: `normalize_model` and must land on the same row; both are checked below.
NO_KEY = object()


def usage(provider="openai", kind=None, cost_usd=None, inp=10, out=5,
          model="muse-spark-1.3"):
    u = {"provider": provider,
         "input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
         "reasoning_tokens": 0, "cached_tokens": 0,
         "cost_usd": cost_usd, "cost_reported": False}
    if model is not NO_KEY:
        u["model"] = model
    if kind is not None:
        u["call_kind"] = kind
    return u


STATEMENTS = len(undx_cost._SCHEMA_STATEMENTS)  # noqa: SLF001 - probe, not product
WIDE = f"ux_{T}_month_provider_kind_model"
NARROW_KIND = f"ux_{T}_month_provider_kind"
NARROW = f"ux_{T}_month_provider"

print("\n== origin 'original': no call_kind, no model, narrow (month, provider) ==")
build_old_shaped_table()
check("call_kind absent before migration", "call_kind" in columns(), False)
check("model absent before migration", "model" in columns(), False)
check("narrow index present before migration", NARROW in indexes(), True)

print("\n== ensure_schema on Postgres (information_schema branch) ==")
n = undx_cost.ensure_schema()
check("statements reported", n, STATEMENTS)
cols = columns()
check("both columns appended, in declaration order", cols[-2:], ["call_kind", "model"])
check("wide index created", WIDE in indexes(), True)
check("narrow kind index dropped", NARROW_KIND in indexes(), False)
check("narrow index dropped", NARROW in indexes(), False)

print("\n== the production row survived and was backfilled ==")
rows = q(f"SELECT month, provider, call_kind, model, calls, uncosted_calls FROM {T}")
check("row count", len(rows), 1)
# Read by integer index, one field at a time, and NOT via `tuple(row)`.
# `services.db.CompatRow` is a Mapping that also accepts integer subscripts, so
# on Postgres `tuple(row)` returns the column *names* while on SQLite it returns
# the values - a difference that reads as a data bug and is not one. The product
# code is safe here because `month_snapshot` and `record` both index positionally
# (`row[0]`..`row[8]`); anything that unpacks or casts a whole row would not be.
check("backfilled row",
      tuple(rows[0][i] for i in range(6)),
      ("2026-09", "openai", undx_cost.CALL_KIND_CHAT, undx_cost.MODEL_UNDECLARED,
       13, 13))

print("\n== the column is NOT NULL with an empty-string default ==")
# Asserted on the catalog and not on behaviour, deliberately. The behaviour a
# nullable column produces is checked further down (the backfill rows), but these
# two are the *cause*, and the cause is worth pinning separately because the
# consequence is reachable only through the migration path - which a fresh
# deployment never takes. See the module docstring for which hazard is measured
# and which is latent.
check("model is_nullable", column_facts("model")[0], "NO")
check("model default", column_facts("model")[1], "''::text")

print("\n== idempotent: a second and third run change nothing ==")
check("second run", undx_cost.ensure_schema(), STATEMENTS)
check("third run", undx_cost.ensure_schema(), STATEMENTS)
check("still one row", len(q(f"SELECT 1 FROM {T}")), 1)
check("columns not duplicated", (cols.count("call_kind"), cols.count("model")), (1, 1))
check("model still undeclared after re-running the backfill",
      q(f"SELECT model FROM {T}")[0][0], undx_cost.MODEL_UNDECLARED)

print("\n== origin 'intermediate': the transition production will actually take ==")
build_intermediate_shaped_table()
check("call_kind already present", "call_kind" in columns(), True)
check("model absent before migration", "model" in columns(), False)
check("narrow kind index present", NARROW_KIND in indexes(), True)
check("intermediate run", undx_cost.ensure_schema(), STATEMENTS)
check("wide index created", WIDE in indexes(), True)
check("narrow kind index dropped", NARROW_KIND in indexes(), False)
migrated = {(r[0], r[1]): (r[2], r[3]) for r in q(
    f"SELECT provider, call_kind, model, calls FROM {T} ORDER BY provider, call_kind")}
check("every row survived", len(migrated), 4)
check("chat backfilled to undeclared",
      migrated[("openai", "chat")], (undx_cost.MODEL_UNDECLARED, 13))
check("embedding backfilled to undeclared",
      migrated[("openai", "embedding")], (undx_cost.MODEL_UNDECLARED, 41))
check("research keeps the empty model - it never had one",
      migrated[("brave", "research")], ("", 7))
check("translation keeps the empty model too",
      migrated[("google", "translation")], ("", 2))

print("\n== ON CONFLICT (month, provider, call_kind, model) on a real unique index ==")
month = undx_cost.current_month()
exec_(f"DELETE FROM {T}")
undx_cost.record(usage(kind="chat", cost_usd=0.001))
undx_cost.record(usage(kind="chat", cost_usd=0.001))       # must UPDATE, not insert
undx_cost.record(usage(kind="embedding", cost_usd=0.002))  # must INSERT beside it
undx_cost.record(usage(kind="embedding", cost_usd=None))   # uncosted
rows = {(r[0], r[1]): (r[2], r[3]) for r in q(
    f"SELECT provider, call_kind, calls, uncosted_calls FROM {T} WHERE month = %s",
    (month,))}
check("one row per (provider, kind)", sorted(rows), [("openai", "chat"), ("openai", "embedding")])
check("chat upserted in place", rows[("openai", "chat")], (2, 0))
check("embedding accumulated separately", rows[("openai", "embedding")], (2, 1))

print("\n== snapshot: providers still sums across kinds ==")
snap = undx_cost.month_snapshot(month)
check("source", snap["source"], "ledger")
check("provider total sums both kinds", snap["providers"]["openai"]["calls"], 4)
check("kinds axis present", sorted(snap["kinds"]), ["chat", "embedding"])
check("chat kind calls", snap["kinds"]["chat"]["calls"], 2)
check("embedding kind calls", snap["kinds"]["embedding"]["calls"], 2)
check("uncosted surfaced on the provider axis",
      snap["providers"]["openai"]["uncosted_calls"], 1)

print("\n== an unrecognised kind lands under 'unknown', not merged into chat ==")
undx_cost.record(usage(kind="emmbedding", cost_usd=0.005))
snap = undx_cost.month_snapshot(month)
check("unknown bucket exists", undx_cost.CALL_KIND_UNKNOWN in snap["kinds"], True)
check("chat did not absorb the typo", snap["kinds"]["chat"]["calls"], 2)

print("\n== an omitted kind lands under chat (old-worker-mid-deploy shape) ==")
undx_cost.record(usage(kind=None, cost_usd=0.001))
snap = undx_cost.month_snapshot(month)
check("chat absorbed the omission", snap["kinds"]["chat"]["calls"], 3)

print("\n== two models under one (provider, kind) are two rows, one total ==")
exec_(f"DELETE FROM {T}")
undx_cost.record(usage(kind="chat", cost_usd=0.001, model="gpt-4o"))
undx_cost.record(usage(kind="chat", cost_usd=0.001, model="gpt-4o"))
undx_cost.record(usage(kind="chat", cost_usd=0.002, model="gpt-4o-mini"))
per_model = {r[0]: r[1] for r in q(
    f"SELECT model, calls FROM {T} WHERE month = %s AND call_kind = 'chat'", (month,))}
check("two rows, split by model", per_model, {"gpt-4o": 2, "gpt-4o-mini": 1})
snap = undx_cost.month_snapshot(month)
check("the provider total is unchanged by the split",
      snap["providers"]["openai"]["calls"], 3)
check("the kind total is unchanged by the split", snap["kinds"]["chat"]["calls"], 3)
check("models axis is keyed provider/model",
      sorted(snap["models"]), ["openai/gpt-4o", "openai/gpt-4o-mini"])
check("per-model calls", snap["models"]["openai/gpt-4o"]["calls"], 2)
check("per-model cost is in micro-USD",
      snap["models"]["openai/gpt-4o"]["cost_micro_usd"], 2000)

print("\n== casing does not split one model's spend into two affordable halves ==")
exec_(f"DELETE FROM {T}")
undx_cost.record(usage(kind="chat", cost_usd=0.001, model="GPT-4o"))
undx_cost.record(usage(kind="chat", cost_usd=0.001, model="gpt-4o"))
check("one row", [(r[0], r[1]) for r in q(
    f"SELECT model, calls FROM {T} WHERE month = %s", (month,))], [("gpt-4o", 2)])

print("\n== NULL-distinctness: an unnamed model upserts, it does not accumulate rows ==")
# On a nullable column every one of these four writes would INSERT, giving four
# rows whose `calls` sum to 4 - so the totals below would *still* pass and only
# the row count would betray it. That is why the row count is asserted first.
exec_(f"DELETE FROM {T}")
undx_cost.record(usage(kind="embedding", cost_usd=0.001, model=NO_KEY))
undx_cost.record(usage(kind="embedding", cost_usd=0.001, model=None))
undx_cost.record(usage(kind="embedding", cost_usd=0.001, model=""))
undx_cost.record(usage(kind="embedding", cost_usd=0.001, model="   "))
check("four writes, one row", len(q(f"SELECT 1 FROM {T} WHERE month = %s", (month,))), 1)
undeclared = q(f"SELECT model, calls FROM {T} WHERE month = %s", (month,))[0]
check("all four normalised to the same model",
      (undeclared[0], undeclared[1]), (undx_cost.MODEL_UNDECLARED, 4))

print("\n== a kind with no models records '' and stays off the models axis ==")
exec_(f"DELETE FROM {T}")
undx_cost.record(usage(provider="brave", kind="research", cost_usd=0.005, model=NO_KEY))
undx_cost.record(usage(provider="brave", kind="research", cost_usd=0.005, model=NO_KEY))
check("empty model, one row", [(r[0], r[1]) for r in q(
    f"SELECT model, calls FROM {T} WHERE month = %s", (month,))], [("", 2)])
snap = undx_cost.month_snapshot(month)
check("research is on the provider axis", snap["providers"]["brave"]["calls"], 2)
check("research is on the kind axis", snap["kinds"]["research"]["calls"], 2)
check("research is NOT on the models axis", snap["models"], {})

exec_(f"DROP TABLE IF EXISTS {T}")

print("\n" + ("=" * 60))
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print("  - " + f)
    raise SystemExit(1)
print("ALL POSTGRES MIGRATION CHECKS PASSED")
