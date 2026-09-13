"""Durable, conservative model-call admission, not another workflow store.

Reserve before HTTP and commit before leaving the process. A crash or lost model
response NEVER releases a reservation: the provider may already have billed it.
All amounts are integer micro-USD. No float arithmetic and no guessed pricing.
The existing caller-owned DB connector supplies SQLite/PostgreSQL translation.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
        return default
    value = int(raw)
    return value if minimum <= value <= maximum else default


def ensure_schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS pulse_ai_model_budget_buckets (
        bucket_key TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
        tokens BIGINT NOT NULL DEFAULT 0, reserved_microusd BIGINT NOT NULL DEFAULT 0,
        CHECK (calls >= 0 AND tokens >= 0 AND reserved_microusd >= 0))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS pulse_ai_model_circuits (
        circuit_key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
        open_until DOUBLE PRECISION NOT NULL DEFAULT 0,
        probe_until DOUBLE PRECISION NOT NULL DEFAULT 0)""")


class SQLAdmission:
    """Atomic shared quotas and fenced half-open probes using the existing DB.

    Callers must permit a commit of their existing request transaction before the
    network call. The Messenger caller has already stored its user message/plan;
    no canonical tool mutation takes place in this conversational branch.
    """
    def __init__(self, conn, cur, tenant_id: int, goal_id: str):
        self.conn, self.cur = conn, cur
        self.tenant_id, self.goal_id = tenant_id, goal_id

    def reserve(self, *, tokens: int, microusd: int) -> bool:
        if type(tokens) is not int or tokens <= 0 or type(microusd) is not int or microusd <= 0:
            return False
        day = datetime.now(timezone.utc).date().isoformat()
        # One stable goal bucket survives retries/restarts and is tenant-bound.
        limits = [
            (f"global:{day}", 10000,
             integer("UNDX_MODEL_GLOBAL_DAILY_TOKENS", 1_000_000, 1, 100_000_000),
             integer("UNDX_MODEL_GLOBAL_DAILY_MICROUSD", 5_000_000, 1, 1_000_000_000)),
            (f"tenant:{self.tenant_id}:{day}", 1000,
             integer("UNDX_MODEL_TENANT_DAILY_TOKENS", 100000, 1, 10_000_000),
             integer("UNDX_MODEL_TENANT_DAILY_MICROUSD", 500000, 1, 100_000_000)),
            (f"goal:{self.tenant_id}:{self.goal_id}",
             integer("UNDX_MODEL_MAX_CALLS", 3, 1, 6),
             integer("UNDX_MODEL_GOAL_TOKENS", 40000, 1, 200000),
             integer("UNDX_MODEL_GOAL_MICROUSD", 100000, 1, 1_000_000)),
        ]
        self.cur.execute("SAVEPOINT undx_model_admission")
        try:
            for key, calls, token_limit, cost_limit in limits:
                self.cur.execute("""INSERT INTO pulse_ai_model_budget_buckets (bucket_key)
                    VALUES (?) ON CONFLICT (bucket_key) DO NOTHING""", (key,))
                self.cur.execute("""UPDATE pulse_ai_model_budget_buckets
                    SET calls=calls+1, tokens=tokens+?, reserved_microusd=reserved_microusd+?
                    WHERE bucket_key=? AND calls<? AND tokens<=? AND reserved_microusd<=?""",
                    (tokens, microusd, key, calls, token_limit-tokens, cost_limit-microusd))
                if self.cur.rowcount != 1:
                    self.cur.execute("ROLLBACK TO SAVEPOINT undx_model_admission")
                    self.cur.execute("RELEASE SAVEPOINT undx_model_admission")
                    return False
            self.cur.execute("RELEASE SAVEPOINT undx_model_admission")
            self.conn.commit()
            return True
        except Exception:
            # No model call on DB failure; caller converts this to a fixed error.
            self.cur.execute("ROLLBACK TO SAVEPOINT undx_model_admission")
            self.cur.execute("RELEASE SAVEPOINT undx_model_admission")
            raise

    def circuit_admit(self, key: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self.cur.execute("""INSERT INTO pulse_ai_model_circuits (circuit_key)
            VALUES (?) ON CONFLICT (circuit_key) DO NOTHING""", (key,))
        # A single conditional UPDATE fences HALF_OPEN across backend processes.
        self.cur.execute("""UPDATE pulse_ai_model_circuits
            SET probe_until=CASE WHEN failures>=3 THEN ? ELSE 0 END
            WHERE circuit_key=? AND open_until<=? AND probe_until<=?""",
            (now+120, key, now, now))
        accepted = self.cur.rowcount == 1
        self.conn.commit()
        return accepted

    def circuit_result(self, key: str, *, ok: bool, retry_after: int = 0):
        if ok:
            self.cur.execute("""UPDATE pulse_ai_model_circuits SET failures=0,
                open_until=0, probe_until=0 WHERE circuit_key=?""", (key,))
        else:
            now = time.time()
            self.cur.execute("""UPDATE pulse_ai_model_circuits SET failures=failures+1,
                open_until=CASE WHEN failures>=2 OR ?>0 THEN ? ELSE open_until END,
                probe_until=0 WHERE circuit_key=?""",
                (retry_after, now+max(60, min(3600, retry_after)), key))
        self.conn.commit()
