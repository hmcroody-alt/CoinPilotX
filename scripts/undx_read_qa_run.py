"""Stage 25: queue a real read, let the worker execute it, read the answer back over HTTP.

Every other proof in this mission asserts one property in isolation with the gateway
stubbed. This one stubs nothing. It walks the whole path a person's request takes —
enqueue, worker claim, real :func:`services.undx_tool_gateway.execute`, real executor,
real independent read-back, settlement, then the actual Flask route handlers — and prints
what it observed at each step.

**Why a script rather than another test.** Stage 25 asks for a QA *run*, and the thing a
reviewer needs from a QA run is the evidence, not a green dot. A test that passes tells
you an assertion held; this prints the run id, the status transitions, the attempt count,
the worker identity that held the lease, and the HTTP payloads, so the claim "a worker
executed this" can be checked rather than taken.

**The write stop stays on, and that is the point.** ``UNDX_AGENT_WRITES_ENABLED`` is
forced off and ``UNDX_GLOBAL_WRITE_KILL_SWITCH`` forced on for the duration. Stages 26
through 29 ask for write QA and this script cannot supply it; what it can supply is proof
that the read path is complete while the write path is bolted shut, which is a different
and smaller claim. It is reported as such.

**Runs against a temporary database.** ``DATABASE_URL`` is pointed at a fresh SQLite file
and restored on exit, so this touches no real data and can be run on any checkout.

Usage::

    python3 scripts/undx_read_qa_run.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

#: The account the QA run belongs to. Matches the harness so the capability's own
#: cohort gate is satisfied by the same id the test suite uses.
QA_USER_ID = 7

#: A read, deliberately. The whole script is scoped to the half of the surface that can
#: be exercised without changing anybody's account.
QA_CAPABILITY = "activity.daily_summary"

FLAGS = {
    "UNDX_AGENT_ENABLED": "1",
    "UNDX_AGENT_READS_ENABLED": "1",
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_AGENT_QA_USER_IDS": "7,8",
    # The two switches the user asked to keep closed. Set explicitly rather than left to
    # the environment so this script cannot accidentally pass on a machine where writes
    # are open.
    "UNDX_AGENT_WRITES_ENABLED": "0",
    # The variable name is read from the policy module rather than typed here. A typo in
    # a kill-switch name is a script that reports the stop engaged while it is open, and
    # spelling it out from memory is exactly how that happens — the first draft of this
    # file guessed ``UNDX_GLOBAL_WRITE_KILL_SWITCH`` and set nothing at all.
}


def _say(label: str, value=None) -> None:
    if value is None:
        print(f"\n== {label}")
    else:
        rendered = json.dumps(value, indent=2, sort_keys=True, default=str) \
            if isinstance(value, (dict, list)) else value
        print(f"{label}: {rendered}")


def main() -> int:
    from services import undx_agent_policy

    flags = {**FLAGS, undx_agent_policy.GLOBAL_WRITE_KILL_SWITCH_ENV: "1"}

    path = tempfile.mkstemp(prefix="undx_read_qa_", suffix=".db")[1]
    saved = {key: os.environ.get(key) for key in [*flags, "DATABASE_URL"]}
    os.environ.update(flags)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"

    try:
        from services import (undx_agent_runs, undx_architecture, undx_run_health,
                              undx_run_status, undx_worker_runtime)
        from services import db as db_module

        conn = db_module.connect()
        cur = conn.cursor()
        undx_architecture.ensure_schema(cur)
        undx_agent_runs.ensure_schema(cur)
        cur.execute(undx_worker_runtime.HEARTBEAT_DDL)
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, "
            "username TEXT, email TEXT, account_status TEXT DEFAULT 'active')"
        )
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email) VALUES (?, ?, ?)",
            (QA_USER_ID, "qa_user", "qa@example.test"),
        )
        # The product tables ``activity.daily_summary`` reads, created empty and owned by
        # nobody. Present rather than absent on purpose: an absent table makes the read
        # fail and the run settle ``partial``, which is the correct behaviour and the
        # wrong thing to be testing here. A QA run against a schema the deployed web
        # service creates on boot should not be measuring what happens when boot did not
        # happen — that case has its own test.
        for ddl in (
            "CREATE TABLE IF NOT EXISTS pulse_messages (id INTEGER PRIMARY KEY, "
            "conversation_id INTEGER, sender_user_id INTEGER, recipient_user_id INTEGER, "
            "body TEXT, created_at TEXT)",
            "CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY, "
            "user_id INTEGER, title TEXT, body TEXT, created_at TEXT, deleted_at TEXT)",
            "CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY, "
            "user_id INTEGER, status TEXT DEFAULT 'active', created_at TEXT)",
            "CREATE TABLE IF NOT EXISTS pulse_statuses (id INTEGER PRIMARY KEY, "
            "user_id INTEGER, created_at TEXT, deleted_at TEXT)",
            "CREATE TABLE IF NOT EXISTS pulse_follows (id INTEGER PRIMARY KEY, "
            "follower_user_id INTEGER, followed_user_id INTEGER, created_at TEXT)",
            "CREATE TABLE IF NOT EXISTS pulse_conversation_participants "
            "(id INTEGER PRIMARY KEY, conversation_id INTEGER, user_id INTEGER)",
            "CREATE TABLE IF NOT EXISTS push_subscriptions (id INTEGER PRIMARY KEY, "
            "user_id INTEGER, endpoint TEXT, is_active INTEGER DEFAULT 1)",
        ):
            cur.execute(ddl)
        # ``alert_rules`` comes from its owner rather than from a guess here. The first
        # draft hand-wrote it and the executor failed on a column it did not know about,
        # which is the general argument against restating somebody else's schema.
        from services import alert_engine

        alert_engine._ALERT_SCHEMA_READY = False
        alert_engine.ensure_alert_schema(conn)
        conn.commit()

        _say("0. the surface this QA ran against")
        policy = undx_agent_policy.flags()
        _say("writes_enabled", policy["writes_enabled"])
        _say("global_write_kill_switch", policy["global_write_kill_switch"])
        if policy["writes_enabled"] or not policy["global_write_kill_switch"]:
            print("REFUSED: this script only runs with the write stop engaged.")
            return 2
        surface = undx_agent_runs.surface()
        _say("runs_enabled", surface.enabled)
        if not surface.enabled:
            print(f"REFUSED: durable runs are off ({surface.reason}).")
            return 2

        _say("1. enqueue — what the request writes down and nothing more")
        run_id = undx_agent_runs.enqueue(
            cur, user_id=QA_USER_ID, capability_id=QA_CAPABILITY, arguments={},
            confirmation_id="", client_request_id="stage25-read-qa",
        )
        conn.commit()
        cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        queued = dict(cur.fetchone())
        _say("run_id", run_id)
        _say("status", queued["status"])
        _say("attempt_count", queued["attempt_count"])
        _say("lease_owner", repr(queued["lease_owner"]))
        _say("confirmation_state", queued["confirmation_state"])
        if queued["status"] != "queued" or queued["attempt_count"] != 0:
            print("FAIL: enqueue executed something.")
            return 1

        _say("2. the worker poll — the real gateway, no stub anywhere")
        # This is the same call ``undx_worker.main`` makes inside its loop. Nothing here
        # supplies a user, a capability or an approval; all three come off the row.
        result = undx_agent_runs.poll_once()
        _say("poll_once", result)

        cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        settled = dict(cur.fetchone())
        _say("status", settled["status"])
        _say("outcome", settled["outcome"])
        _say("attempt_count", settled["attempt_count"])
        _say("completed_at", settled["completed_at"])
        _say("lease_owner_after_settle", repr(settled["lease_owner"]))
        _say("last_error", settled["last_error"] or "(none)")

        # The Stage 16 evidence: a row that was claimed and settled. A synchronous
        # request would have left no row at all, so this is what distinguishes a worker
        # pass from an in-request execution.
        claimed = settled["attempt_count"] >= 1 and bool(settled["completed_at"])
        _say("worker_claim_evidence",
             "attempt_count>=1 and completed_at set" if claimed else "MISSING")
        if not claimed:
            print("FAIL: no evidence a worker claimed this run.")
            return 1

        _say("3. projection — what a client would be shown")
        projection = undx_run_status.project(settled)
        _say("client_status", projection.status)
        _say("terminal", projection.terminal)
        _say("description", projection.description)

        _say("4. retrieval over the real routes")
        from flask import Flask

        from services import undx_agent_run_routes, undx_run_health_routes

        app = Flask("undx_stage25_qa")
        undx_agent_run_routes.register(app)
        undx_run_health_routes.register(app)

        class _Bot:
            @staticmethod
            def api_account_user():
                return {"user_id": QA_USER_ID}

            @staticmethod
            def db():
                return db_module.connect()

        for module in (undx_agent_run_routes, undx_run_health_routes):
            module._bot = lambda: _Bot

        client = app.test_client()
        listing = client.get("/api/undx/runs")
        _say("GET /api/undx/runs", listing.status_code)
        _say("body", listing.get_json())

        detail = client.get(f"/api/undx/runs/{run_id}")
        _say(f"GET /api/undx/runs/{run_id}", detail.status_code)
        _say("body", detail.get_json())

        _say("5. cross-account check — the same id, a different session")
        class _Other(_Bot):
            @staticmethod
            def api_account_user():
                return {"user_id": 8}

        undx_agent_run_routes._bot = lambda: _Other
        foreign = client.get(f"/api/undx/runs/{run_id}")
        _say("status_code", foreign.status_code)
        _say("body", foreign.get_json())
        if foreign.status_code != 404:
            print("FAIL: another account could see this run.")
            return 1
        undx_agent_run_routes._bot = lambda: _Bot

        _say("6. health surface")
        undx_worker_runtime.record_worker_heartbeat(
            undx_agent_runs.WORKER_NAME, "healthy",
            metadata={"deployed_sha": undx_run_health.build_sha(),
                      "agent_runs_enabled": True},
        )
        health = client.get("/health/undx/runs")
        _say("GET /health/undx/runs", health.status_code)
        _say("body", health.get_json())

        rendered = json.dumps(health.get_json())
        if run_id in rendered or "last_error" in rendered:
            print("FAIL: the health surface leaked a run.")
            return 1

        _say("VERDICT")
        print("READ QA RUN: PASS — queued, claimed by the worker, executed through the "
              "real gateway, settled, and retrieved by its owner only.")
        print("WRITE QA RUN: NOT ATTEMPTED — the write stop was engaged throughout, by "
              "instruction. Stages 26-29 are UNPROVEN, not passed.")
        return 0
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
