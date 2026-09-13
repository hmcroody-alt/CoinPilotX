"""Verify the `call_kind` ledger migration on a real Postgres.

Why this exists as a script and not a test: the pytest suite runs on SQLite, so
it exercises `PRAGMA table_info` and SQLite's `ON CONFLICT`. Production is
Postgres, where three things differ and none of them are visible from the SQLite
run:

  * `_ledger_columns` takes the `information_schema` branch, which passes a `?`
    placeholder that something has to rewrite to `%s`;
  * `ON CONFLICT (month, provider, call_kind)` resolves against a real unique
    index rather than SQLite's more forgiving matching;
  * `INTEGER PRIMARY KEY AUTOINCREMENT` and `datetime('now')` are rewritten by
    `services/db.py` on the way out.

It builds the table in its **old** shape - no `call_kind`, narrow unique index -
carrying a replica of the single row production actually holds, then migrates it.
That row is the thing this migration must not lose.

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
    """The pre-migration schema, verbatim minus `call_kind`."""
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


def indexes() -> set[str]:
    return {r[0] for r in q(
        "SELECT indexname FROM pg_indexes WHERE tablename = %s", (T,))}


def columns() -> list[str]:
    return [r[0] for r in q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (T,))]


def usage(provider="openai", kind=None, cost_usd=None, inp=10, out=5):
    u = {"provider": provider, "model": "muse-spark-1.3",
         "input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
         "reasoning_tokens": 0, "cached_tokens": 0,
         "cost_usd": cost_usd, "cost_reported": False}
    if kind is not None:
        u["call_kind"] = kind
    return u


print("\n== pre-migration: old-shaped table with the production row ==")
build_old_shaped_table()
check("call_kind absent before migration", "call_kind" in columns(), False)
check("narrow index present before migration", f"ux_{T}_month_provider" in indexes(), True)

print("\n== ensure_schema on Postgres (information_schema branch) ==")
n = undx_cost.ensure_schema()
check("statements reported", n, 3)
cols = columns()
check("call_kind appended last", cols[-1], "call_kind")
check("wide index created", f"ux_{T}_month_provider_kind" in indexes(), True)
check("narrow index dropped", f"ux_{T}_month_provider" in indexes(), False)

print("\n== the production row survived and was backfilled as chat ==")
rows = q(f"SELECT month, provider, call_kind, calls, uncosted_calls FROM {T}")
check("row count", len(rows), 1)
# Read by integer index, one field at a time, and NOT via `tuple(row)`.
# `services.db.CompatRow` is a Mapping that also accepts integer subscripts, so
# on Postgres `tuple(row)` returns the column *names* while on SQLite it returns
# the values - a difference that reads as a data bug and is not one. The product
# code is safe here because `month_snapshot` and `record` both index positionally
# (`row[0]`..`row[7]`); anything that unpacks or casts a whole row would not be.
check("backfilled row",
      tuple(rows[0][i] for i in range(5)),
      ("2026-09", "openai", undx_cost.CALL_KIND_CHAT, 13, 13))

print("\n== idempotent: a second and third run change nothing ==")
check("second run", undx_cost.ensure_schema(), 3)
check("third run", undx_cost.ensure_schema(), 3)
check("still one row", len(q(f"SELECT 1 FROM {T}")), 1)
check("call_kind not duplicated", cols.count("call_kind"), 1)

print("\n== ON CONFLICT (month, provider, call_kind) on a real unique index ==")
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

exec_(f"DROP TABLE IF EXISTS {T}")

print("\n" + ("=" * 60))
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print("  - " + f)
    raise SystemExit(1)
print("ALL POSTGRES MIGRATION CHECKS PASSED")
