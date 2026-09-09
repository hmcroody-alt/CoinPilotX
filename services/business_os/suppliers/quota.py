"""Shared durable CJ egress/account pacing and conservative point admission.

All API workers sharing an egress IP MUST use the same database and configured
egress group. No in-memory limiter, IP rotation, or automatic account eviction.
PostgreSQL row locks and SQLite BEGIN IMMEDIATE serialize admissions across
processes. Unknown points admit costed operations at a reduced pace rather than
refusing them: CJ's zero-point endpoints do not reliably report a budget, so a
refusal there is a state nothing can leave. See `reserve_request`.

The two ceilings enforced here -- three CJ accounts and ten business calls per
second, both per outbound IP -- are the provider's, and live in `policy.py`.
This module is where they are made true, at a deliberate margin below the
stated rate; see `EGRESS_MIN_INTERVAL`. What it cannot do is verify that an
egress *group* is one egress *IP*: that is a human attestation, and
`policy.egress_ip_attested()` is where its absence is admitted.
"""
from __future__ import annotations

import math
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from services import db
from .errors import SupplierError
from .policy import MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP

# We pace below CJ's ceiling, not at it.
#
# CJ allows ten business calls per second per outbound IP. Admitting one every
# 0.1s hits that exactly, which is only safe if our clock and CJ's agree on
# where a second begins -- and they do not. Two calls we admit 0.1s apart can
# land inside the same second at CJ, and network jitter can reorder them into a
# tighter burst than we ever scheduled.
#
# The cost of being wrong is not one rejected call. `penalize` treats a 429 as
# scope-unknown and pauses the *whole* egress group, so a single call over the
# line stalls every merchant sharing this IP for at least thirty seconds. The
# headroom buys back a few percent of throughput to avoid that; it is derived
# from the provider ceiling rather than written as a second literal so the two
# cannot drift apart.
EGRESS_HEADROOM_CALLS_PER_SECOND = 1.5
EGRESS_TARGET_CALLS_PER_SECOND = (
    MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP - EGRESS_HEADROOM_CALLS_PER_SECOND
)
EGRESS_MIN_INTERVAL = 1 / EGRESS_TARGET_CALLS_PER_SECOND

# How slowly an account may make costed calls while its point budget is unknown.
# Deliberately far below any account's real pace: this is the rate at which we
# are willing to spend points we cannot yet count, and the first response that
# carries a readable `pointsInfo` ends it.
UNKNOWN_BUDGET_MIN_INTERVAL = 5.0


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_cj_egress_quota (
            egress_group TEXT PRIMARY KEY, next_at DOUBLE PRECISION NOT NULL DEFAULT 0,
            blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_cj_account_quota (
            egress_group TEXT NOT NULL, account_ref TEXT NOT NULL,
            next_at DOUBLE PRECISION NOT NULL DEFAULT 0,
            blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, qps INTEGER NOT NULL DEFAULT 1,
            remaining INTEGER, used_today INTEGER, total INTEGER,
            sequence INTEGER NOT NULL DEFAULT 0,
            observed_at DOUBLE PRECISION, PRIMARY KEY (egress_group, account_ref))""")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def budget_state(remaining, total=None, *, reserve=1000):
    if remaining is None:
        return "UNKNOWN"
    if remaining <= 0:
        return "EXHAUSTED"
    if remaining <= reserve:
        return "CRITICAL"
    if remaining <= max(reserve * 2, (total or 0) * .1):
        return "CONSTRAINED"
    return "NORMAL"


class DurableCJQuota:
    def __init__(self, *, egress_group=None, reserve=1000, clock=time.time):
        self.egress_group = egress_group or os.getenv("CJ_EGRESS_GROUP", "")
        self.reserve = max(0, int(reserve))
        self.clock = clock

    @contextmanager
    def _locked(self):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", self.egress_group):
            raise SupplierError("EGRESS_CONFIGURATION_REQUIRED", http_status=503)
        ensure_schema()
        conn = db.connect()
        try:
            if not db.IS_POSTGRES:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO business_os_cj_egress_quota (egress_group) VALUES (?) ON CONFLICT DO NOTHING", (self.egress_group,))
            suffix = " FOR UPDATE" if db.IS_POSTGRES else ""
            conn.execute("SELECT egress_group FROM business_os_cj_egress_quota WHERE egress_group=?" + suffix, (self.egress_group,)).fetchone()
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _account(self, conn, account_ref):
        if not isinstance(account_ref, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", account_ref):
            raise SupplierError("ACCOUNT_REFERENCE_REQUIRED", http_status=503)
        row = conn.execute("SELECT * FROM business_os_cj_account_quota WHERE egress_group=? AND account_ref=?", (self.egress_group, account_ref)).fetchone()
        if row is None:
            count = conn.execute("SELECT COUNT(*) FROM business_os_cj_account_quota WHERE egress_group=?", (self.egress_group,)).fetchone()[0]
            if count >= 3:
                raise SupplierError("EGRESS_ACCOUNT_CAPACITY", http_status=503)
            conn.execute("INSERT INTO business_os_cj_account_quota (egress_group,account_ref) VALUES (?,?)", (self.egress_group, account_ref))
            row = conn.execute("SELECT * FROM business_os_cj_account_quota WHERE egress_group=? AND account_ref=?", (self.egress_group, account_ref)).fetchone()
        return dict(row)

    def reserve_request(self, account_ref, *, cost=0, critical=False, authentication=False):
        if type(cost) is not int or cost < 0 or cost > 1000:
            raise SupplierError("INVALID_POINT_COST", http_status=400)
        now = self.clock()
        with self._locked() as conn:
            row = self._account(conn, account_ref)
            egress = dict(conn.execute("SELECT * FROM business_os_cj_egress_quota WHERE egress_group=?", (self.egress_group,)).fetchone())
            retry = max(row["next_at"], row["blocked_until"], egress["next_at"], egress["blocked_until"]) - now
            if retry > .000001:
                raise SupplierError("RATE_LIMITED", http_status=429, retry_after=retry)
            remaining = row["remaining"]
            unknown = cost and remaining is None
            if cost and not unknown and remaining - cost < (0 if critical else self.reserve):
                raise SupplierError("POINT_BUDGET_RESERVED", http_status=429, retry_after=60)
            seq = row["sequence"] + 1
            qps = 1 if authentication else min(6, max(1, row["qps"]))
            # An unknown budget paces; it no longer refuses.
            #
            # This used to raise QUOTA_UNKNOWN for any cost while `remaining` was
            # None, on the stated reasoning that zero-point health calls stay
            # admissible and the first of those resolves the budget. That premise
            # does not hold against a live account: CJ answered `setting/get` and
            # `shop/getShops` -- both zero-point -- with `0/0/0`, which
            # `_observe_points` correctly declines to record as a ceiling of
            # nothing. `remaining` therefore stays None forever, and the only
            # responses that carry a real budget are the costed ones this branch
            # refused. The endpoints that could answer the question were exactly
            # the endpoints gated behind knowing the answer.
            #
            # Note `usedToday` is 0 in that block too. Exhaustion reads
            # `remaining=0, usedToday=total>0`; a 0/0/0 block is a response not
            # reporting a budget, not an account out of points. So admitting here
            # is not spending a balance we were told was empty.
            #
            # Refusing was never the safer half either. CJ enforces its own
            # allowance with HTTP 429, which `penalize` already handles -- honored
            # Retry-After, exponential backoff to an hour, and a pause across the
            # whole egress group. What this guard added on top was the ability to
            # reach a state that could not end.
            #
            # Unknown is still degraded, so it is paced rather than trusted:
            # while the budget is unresolved an account gets one call per
            # UNKNOWN_BUDGET_MIN_INTERVAL instead of its usual qps. That bounds
            # what an account whose budget never resolves can spend, and it costs
            # nothing once a reading lands -- `observe` writes `remaining`, this
            # branch stops applying, and normal pacing resumes on the next call.
            interval = UNKNOWN_BUDGET_MIN_INTERVAL if unknown else 1 / qps
            conn.execute("UPDATE business_os_cj_egress_quota SET next_at=? WHERE egress_group=?", (now + EGRESS_MIN_INTERVAL, self.egress_group))
            conn.execute("UPDATE business_os_cj_account_quota SET next_at=?,remaining=?,sequence=? WHERE egress_group=? AND account_ref=?", (now + interval, None if remaining is None else remaining - cost, seq, self.egress_group, account_ref))
            return seq

    def rebind_account(self, old_ref, new_ref):
        """Promote a hashed pending key slot to its verified account slot atomically."""
        if old_ref == new_ref:
            return
        with self._locked() as conn:
            old = self._account(conn, old_ref)
            new = conn.execute("SELECT * FROM business_os_cj_account_quota WHERE egress_group=? AND account_ref=?", (self.egress_group, new_ref)).fetchone()
            if new is None:
                conn.execute("UPDATE business_os_cj_account_quota SET account_ref=? WHERE egress_group=? AND account_ref=?", (new_ref, self.egress_group, old_ref))
            else:
                # Multiple API keys cannot multiply an account's quota.
                conn.execute("UPDATE business_os_cj_account_quota SET next_at=?,blocked_until=?,remaining=NULL WHERE egress_group=? AND account_ref=?", (max(old["next_at"], new["next_at"]), max(old["blocked_until"], new["blocked_until"]), self.egress_group, new_ref))
                conn.execute("DELETE FROM business_os_cj_account_quota WHERE egress_group=? AND account_ref=?", (self.egress_group, old_ref))

    def observe(self, account_ref, points_info, *, sequence=None):
        if not isinstance(points_info, dict):
            return
        values = [points_info.get(k) for k in ("remaining", "usedToday", "total")]
        if any(type(v) is not int or v < 0 for v in values) or values[0] > values[2]:
            return
        # A ceiling of zero is not a budget, and `remaining` cannot exceed it.
        #
        # CJ's zero-point endpoints answer a live account with `0/0/0`. The
        # adapter already declines to forward that, but the guard belongs here
        # too, because the two states are no longer equally bad: unknown is
        # paced and self-healing, while a recorded zero reads EXHAUSTED and is
        # only ever lowered by the `min` below -- nothing else in this class
        # raises `remaining`. One call site forwarding a 0/0/0 would end an
        # account permanently, and it would look like the provider's answer.
        if values[2] <= 0:
            return
        with self._locked() as conn:
            row = self._account(conn, account_ref)
            remaining, used, total = values
            # A late response cannot replenish points reserved by a newer request.
            #
            # When we already hold a figure, taking the lower of the two is
            # right: the stale reading cannot raise it, and the debits the newer
            # request applied stay applied. When we hold nothing there is no
            # such arithmetic to do, and this used to record a hard zero -- a
            # number the provider never sent, and the stickiest kind of
            # invention. It reads as EXHAUSTED to the merchant on the strength
            # of a response that may have said the opposite, and it outlives the
            # unknown it replaced because it satisfies every `remaining is None`
            # guard while still failing every costed admission.
            #
            # Unknown is both the honest answer and the self-healing one. It
            # still holds costed calls to a crawl, but they go out, and the
            # first response whose sequence matches carries a reading that
            # resolves the budget instead of guessing it.
            if sequence is not None and row["sequence"] != sequence:
                if row["remaining"] is None:
                    return
                remaining = min(remaining, row["remaining"])
            now = self.clock()
            # Resolving the budget also ends the slow lane it was scheduled into.
            #
            # `reserve_request` pushes `next_at` out by UNKNOWN_BUDGET_MIN_INTERVAL
            # when it admits a costed call against an unknown budget. That delay
            # was priced against not knowing; the response now in hand is exactly
            # what it was waiting for. Leaving it in place would charge the
            # account five seconds for a question already answered -- and on the
            # merchant-visible path, the search that resolves the budget would be
            # followed by one that still stalls.
            #
            # Only the unknown-budget delay is pulled back, and only down to the
            # normal pace: `blocked_until` is a different column, so a provider
            # 429 recorded by `penalize` is untouched, and so is the shared
            # egress interval.
            paced = row["next_at"]
            if row["remaining"] is None:
                paced = min(paced, now + 1 / min(6, max(1, row["qps"])))
            conn.execute("UPDATE business_os_cj_account_quota SET remaining=?,used_today=?,total=?,observed_at=?,next_at=?,failures=0 WHERE egress_group=? AND account_ref=?", (remaining, used, total, now, paced, self.egress_group, account_ref))

    def penalize(self, account_ref, retry_after=None):
        now = self.clock()
        with self._locked() as conn:
            row = self._account(conn, account_ref)
            seconds = None
            try:
                seconds = float(retry_after)
            except (TypeError, ValueError):
                try:
                    seconds = parsedate_to_datetime(str(retry_after)).timestamp() - now
                except (ValueError, TypeError, OverflowError):
                    pass
            failures = min(10, row["failures"] + 1)
            if seconds is None or not math.isfinite(seconds) or seconds <= 0:
                seconds = min(3600, 30 * 2 ** (failures - 1))
            # Never shorten a valid provider Retry-After to our fallback ceiling.
            until = max(row["blocked_until"], now + seconds)
            conn.execute("UPDATE business_os_cj_account_quota SET blocked_until=?,failures=? WHERE egress_group=? AND account_ref=?", (until, failures, self.egress_group, account_ref))
            # 429 scope is not guaranteed; pause the complete shared egress group.
            conn.execute("UPDATE business_os_cj_egress_quota SET blocked_until=CASE WHEN blocked_until>? THEN blocked_until ELSE ? END WHERE egress_group=?", (until, until, self.egress_group))
            return until - now

    def snapshot(self, account_ref):
        with self._locked() as conn:
            row = self._account(conn, account_ref)
            return {"remaining": row["remaining"], "used_today": row["used_today"], "total": row["total"], "state": budget_state(row["remaining"], row["total"], reserve=self.reserve), "retry_after": max(0, row["blocked_until"] - self.clock()), "qps": row["qps"]}
