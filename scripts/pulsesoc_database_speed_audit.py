#!/usr/bin/env python3
"""Audit core PulseSoc indexes and enforce the Mission Control query budget."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from scripts.site_functional_audit import ensure_smoke_accounts  # noqa: E402
from services import db as db_service  # noqa: E402


INDEX_TARGETS = {
    "messages": ("conversation_id", "sender_id", "created_at"),
    "conversation_participants": ("conversation_id", "user_id"),
    "notifications": ("user_id", "created_at"),
    "comments": ("post_id", "user_id", "created_at"),
    "post_reactions": ("post_id", "user_id"),
    "pulse_reels": ("user_id", "created_at"),
    "push_delivery_jobs": ("status", "user_id", "created_at"),
    "notification_delivery_jobs": ("status", "user_id", "created_at"),
    "intelligence_signals": ("stream", "created_at"),
    "communication_calls": ("status", "created_at"),
    "pulse_growth_accounts": ("user_id",),
    "admin_audit_logs": ("created_at",),
}


def emit(status: str, item: str, detail: str) -> None:
    print(f"{status}\t{item}\t{detail}")


def sqlite_index_columns(cur, table: str) -> list[tuple[str, ...]]:
    rows = cur.execute(f"PRAGMA index_list('{table}')").fetchall()
    indexes: list[tuple[str, ...]] = []
    for row in rows:
        name = row[1]
        columns = cur.execute(f"PRAGMA index_info('{name}')").fetchall()
        indexes.append(tuple(str(column[2]) for column in columns))
    return indexes


def postgres_index_columns(cur, table: str) -> list[tuple[str, ...]]:
    cur.execute(
        """
        SELECT array_agg(a.attname ORDER BY x.ordinality)
        FROM pg_class t
        JOIN pg_index i ON t.oid = i.indrelid
        JOIN unnest(i.indkey) WITH ORDINALITY AS x(attnum, ordinality) ON TRUE
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.attnum
        WHERE t.relname = %s
        GROUP BY i.indexrelid
        """,
        (table,),
    )
    return [tuple(row[0] or ()) for row in cur.fetchall()]


def main() -> int:
    bot.init_db()
    failures = 0
    warnings = 0
    conn = db_service.connect()
    cur = conn.cursor()
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {str(row[0]) for row in cur.fetchall()}

    for table, expected_columns in INDEX_TARGETS.items():
        if table not in tables:
            emit("SKIP", table, "table not present in this deployment")
            continue
        indexes = (
            postgres_index_columns(cur, table)
            if db_service.IS_POSTGRES
            else sqlite_index_columns(cur, table)
        )
        covered = {
            column
            for column in expected_columns
            if any(column in index for index in indexes)
        }
        missing = [column for column in expected_columns if column not in covered]
        if len(missing) == len(expected_columns):
            failures += 1
            emit("FAIL", table, f"no target columns indexed: {', '.join(missing)}")
        elif missing:
            warnings += 1
            emit("WARN", table, f"uncovered columns: {', '.join(missing)}")
        else:
            emit("PASS", table, f"{len(indexes)} indexes cover target columns")

    if "notifications" in tables:
        started = time.perf_counter()
        cur.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=?",
            (ensure_smoke_accounts()[0],),
        )
        cur.fetchone()
        elapsed_ms = (time.perf_counter() - started) * 1000
        emit("PASS" if elapsed_ms < 100 else "WARN", "notification lookup", f"{elapsed_ms:.2f}ms")
        warnings += int(elapsed_ms >= 100)
    conn.close()

    user_id, admin_id = ensure_smoke_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["admin_user_id"] = admin_id
    response = client.get("/dashboard")
    dashboard_queries = int(response.headers.get("X-DB-Query-Count") or 0)
    if response.status_code >= 500 or dashboard_queries > 30:
        failures += 1
        emit("FAIL", "/dashboard query budget", f"HTTP {response.status_code} queries={dashboard_queries}")
    else:
        emit("PASS", "/dashboard query budget", f"HTTP {response.status_code} queries={dashboard_queries}")

    print(f"SUMMARY\tfailures={failures}\twarnings={warnings}\ttables={len(tables)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
