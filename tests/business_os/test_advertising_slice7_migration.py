"""Advertising slice 7 — migration 0009 additivity + symmetry checks.

Applies migrations/business_os/0009_advertising_delivery.sql against a throwaway
SQLite database and asserts it creates EXACTLY the three slice-7 tables (delivery
instances + immutable impression/click event logs) and the indexes the delivery
code relies on — including the frequency-cap index the server-authoritative cap
derives from. Then applies the paired .down.sql and asserts it removes exactly
those objects (symmetric rollback), leaving the database empty. Also asserts the
migration text is additive-only: it never references the legacy pulse_ads_service
/ pulse_ad_* tables, the canonical ledger, or any slice 1-6 table, and performs no
destructive DDL in the up direction.

    python tests/business_os/test_advertising_slice7_migration.py   # no pytest needed
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_MIG_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "migrations", "business_os"))
_UP = os.path.join(_MIG_DIR, "0009_advertising_delivery.sql")
_DOWN = os.path.join(_MIG_DIR, "0009_advertising_delivery.down.sql")

UP_SQL = open(_UP, encoding="utf-8").read()
DOWN_SQL = open(_DOWN, encoding="utf-8").read()

SLICE7_TABLES = {
    "business_os_ad_delivery_instances",
    "business_os_ad_impression_events",
    "business_os_ad_click_events",
}
# The server-authoritative frequency cap depends on this exact composite index.
REQUIRED_INDEXES = {
    "idx_ad_impr_freq",       # (campaign_id, subject_ref, event_at) -> freq cap
    "idx_ad_delivery_subject",
    "idx_ad_click_delivery",
}


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _indexes(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name IS NOT NULL").fetchall()}


# 1 -- up creates exactly the three slice-7 tables on an empty DB ----------
def test_up_creates_only_slice7_tables():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        tables = _tables(conn)
        _assert(SLICE7_TABLES <= tables,
                f"up must create all slice-7 tables, got {tables}")
        extra = tables - SLICE7_TABLES - {"sqlite_sequence"}
        _assert(not extra, f"up created unexpected tables: {extra}")
    finally:
        conn.close()


# 2 -- required indexes (incl. the frequency-cap index) exist --------------
def test_up_creates_required_indexes():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        idx = _indexes(conn)
        for name in REQUIRED_INDEXES:
            _assert(name in idx, f"missing required index {name}: {sorted(idx)}")
    finally:
        conn.close()


# 3 -- the frequency-cap index is on (campaign_id, subject_ref, event_at) --
def test_freq_index_columns():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        cols = [r[2] for r in conn.execute(
            "PRAGMA index_info('idx_ad_impr_freq')").fetchall()]
        _assert(cols == ["campaign_id", "subject_ref", "event_at"],
                f"freq index columns/order wrong: {cols}")
    finally:
        conn.close()


# 4 -- dedup_key UNIQUE enforced on both event logs (idempotency backbone) --
def test_dedup_key_unique():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        for tbl in ("business_os_ad_impression_events",
                    "business_os_ad_click_events"):
            uniq = [r for r in conn.execute(f"PRAGMA index_list('{tbl}')").fetchall()
                    if r[2]]  # r[2] == unique flag
            found = False
            for u in uniq:
                cols = [c[2] for c in conn.execute(
                    f"PRAGMA index_info('{u[1]}')").fetchall()]
                if cols == ["dedup_key"]:
                    found = True
            _assert(found, f"{tbl} must enforce UNIQUE(dedup_key)")
    finally:
        conn.close()


# 5 -- down is a symmetric rollback: removes exactly the slice-7 objects ----
def test_down_is_symmetric():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        before_other = _tables(conn) - SLICE7_TABLES
        conn.executescript(DOWN_SQL)
        after = _tables(conn)
        _assert(not (SLICE7_TABLES & after),
                f"down must drop all slice-7 tables, still present: {SLICE7_TABLES & after}")
        _assert((after - {"sqlite_sequence"}) == (before_other - {"sqlite_sequence"}),
                f"down must not touch anything else: {after}")
    finally:
        conn.close()


# 6 -- migrations are additive-only: never reference legacy / ledger / prior -
def _strip_sql_comments(sql):
    """Drop ``-- ...`` comment lines so we only inspect executable DDL. The
    migrations legitimately NAME the legacy tables in prose ("never touches
    pulse_ads_service"); only an actual DDL reference should fail this test."""
    out = []
    for line in sql.splitlines():
        code = line.split("--", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


def test_additive_only_text():
    up_ddl = _strip_sql_comments(UP_SQL)
    down_ddl = _strip_sql_comments(DOWN_SQL)
    banned = ("pulse_ads_service", "pulse_ad_", "ledger_entries",
              "business_os_ledger", "business_os_ad_campaigns",
              "business_os_ad_ad_sets", "business_os_ad_creatives")
    for tok in banned:
        _assert(tok not in up_ddl, f"up migration DDL must not reference {tok!r}")
        _assert(tok not in down_ddl, f"down migration DDL must not reference {tok!r}")
    # the UP direction must be purely additive: no DROP/DELETE/ALTER/TRUNCATE
    up_upper = up_ddl.upper()
    for verb in ("DROP TABLE", "DROP INDEX", "DELETE FROM", "ALTER TABLE",
                 "TRUNCATE"):
        _assert(verb not in up_upper, f"up migration must not contain {verb}")


# 7 -- re-running up is idempotent (IF NOT EXISTS everywhere) ---------------
def test_up_is_idempotent():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(UP_SQL)
        conn.executescript(UP_SQL)  # must not raise
        _assert(SLICE7_TABLES <= _tables(conn), "re-run up must be a no-op, not an error")
    finally:
        conn.close()


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    tests = [
        test_up_creates_only_slice7_tables,
        test_up_creates_required_indexes,
        test_freq_index_columns,
        test_dedup_key_unique,
        test_down_is_symmetric,
        test_additive_only_text,
        test_up_is_idempotent,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
