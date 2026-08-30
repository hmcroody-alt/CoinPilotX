"""A health surface is only useful if it is believed, and belief is what these tests buy.

Stage 32 asks for a surface reporting worker liveness, build agreement and queue depth
with no secrets on it. Three specific ways such a surface goes wrong, one test class each.

**It lies by omission.** The field an operator most wants on a failing queue is
``last_error``, and that is the one field :mod:`services.undx_run_health` may not publish:
:func:`services.undx_agent_runs.execute_claimed` composes it from an exception message,
and an exception message can name a row. The route is unauthenticated, so publishing it
would turn a liveness check into an enumeration oracle. The test here is not "we removed
that key" — it is a sweep over the whole rendered payload for any value that could have
come from a run, asserted recursively, so a future field carrying the same string fails
too.

**It lies by collapsing.** Three pairs of distinct facts are individually tempting to
merge and each merge destroys the reading that mattered: a worker that is switched off
versus one that is switched on and dead; a heartbeat that is absent versus one whose age
is zero; two shas that disagree versus two shas that cannot be compared. Each pair gets a
test asserting the two states are distinguishable in the payload.

**It lies by agreeing with itself.** ``sha_match`` compares a value the worker wrote
against a value the web service computes, using two copies of the same environment read.
Two copies that drifted would report a mismatch between identical deploys — the alarm
that cries wolf — so a test reads both functions' source and asserts they consult the same
variables in the same order.

The route pack gets the same treatment the read pack got: its shape is asserted over the
Flask URL map rather than read off the source, because a decorator is a claim and the URL
map is the fact.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_FLAGS = {
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
}

A_READ = "activity.daily_summary"

#: The strings that must never appear anywhere in a rendered payload. Each is planted
#: into the fixture on a field the surface reads near, so its absence is evidence rather
#: than a coincidence of the test data.
SECRETS = (
    "sk_live_planted_secret",
    "alert:4242",
    "planted_error_detail",
)


def _iso(moment: datetime) -> str:
    return moment.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def _walk(payload) -> list:
    """Every scalar in a nested payload, so assertions can be made about all of them.

    Written rather than ``json.dumps``-and-substring because a test that greps the
    serialised form passes for a payload that base64s the secret, and the point is to
    know what is in there.
    """
    found = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(key)
            found.extend(_walk(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found.extend(_walk(value))
    else:
        found.append(payload)
    return found


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs, undx_run_health, undx_worker_runtime

        self.runs = undx_agent_runs
        self.health = undx_run_health
        self.worker_runtime = undx_worker_runtime
        self.runs.ensure_schema(self.fx.cur)
        self.fx.cur.execute(undx_worker_runtime.HEARTBEAT_DDL)
        self.fx.commit()
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, *, user_id=OWNER_ID, request_id="req_1"):
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=user_id, capability_id=A_READ, arguments={},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        return run_id

    def _heartbeat(self, *, age_seconds=5, sha="abc123", status="healthy",
                   metadata=None, name=None):
        seen = self.now - timedelta(seconds=age_seconds)
        payload = {"deployed_sha": sha, "agent_runs_enabled": True}
        payload.update(metadata or {})
        self.fx.cur.execute(
            "INSERT INTO worker_heartbeats "
            "(worker_name, status, last_seen_at, last_error, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (name or self.runs.WORKER_NAME, status, _iso(seen), "",
             json.dumps(payload, sort_keys=True)),
        )
        self.fx.commit()

    def _env(self, **overrides) -> dict:
        """The whole process environment plus overrides.

        ``env`` is one mapping standing for one process environment: the run flags and
        the build sha are both read from it. Passing only the sha would silently disable
        the agent and make every ``configured`` assertion below pass for the wrong
        reason — which is exactly what the first draft of this file did.
        """
        merged = dict(os.environ)
        merged.setdefault("RAILWAY_GIT_COMMIT_SHA", "abc123")
        merged.update(overrides)
        return merged

    def _snapshot(self, **env):
        return self.health.snapshot(self.fx.cur, env=self._env(**env), now=self.now)


class ThePayloadCarriesCountsAndNothingElse(_Base):
    """Stage 32's "no secrets" clause, asserted over the values rather than the keys."""

    def test_a_planted_error_message_does_not_reach_the_payload(self) -> None:
        """``last_error`` is the field this surface exists to not publish.

        Planted on a settled run and on the heartbeat row, which are the two places the
        snapshot reads from, so its absence is a property of the reader rather than of
        the fixture being empty.
        """
        run_id = self._queue()
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='failed', last_error=?, "
            "canonical_target_id=? WHERE run_id=?",
            ("planted_error_detail: alert:4242", "alert:4242", run_id),
        )
        self.fx.cur.execute(
            "INSERT INTO worker_heartbeats "
            "(worker_name, status, last_seen_at, last_error, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.runs.WORKER_NAME, "error", _iso(self.now), "planted_error_detail",
             json.dumps({"deployed_sha": "abc123", "api_key": "sk_live_planted_secret"})),
        )
        self.fx.commit()

        rendered = json.dumps(self._snapshot(), default=str)
        for secret in SECRETS:
            self.assertNotIn(secret, rendered,
                             f"{secret!r} reached an unauthenticated health payload")

    def test_no_run_id_or_user_id_appears_anywhere(self) -> None:
        """Counts, not rows. A health surface that names one run can be walked to name all."""
        first = self._queue(request_id="req_a")
        second = self._queue(user_id=OTHER_ID, request_id="req_b")
        self._heartbeat()

        values = {str(value) for value in _walk(self._snapshot())}
        self.assertNotIn(first, values)
        self.assertNotIn(second, values)
        # The owner ids are small integers that could collide with a genuine count, so
        # this is asserted on the serialised keys instead: no field names an owner.
        keys = json.dumps(self._snapshot(), default=str)
        self.assertNotIn("user_id", keys)
        self.assertNotIn("run_id", keys)

    def test_every_status_key_belongs_to_the_stored_vocabulary(self) -> None:
        """Depth keys come from :mod:`services.undx_agent_runs`, not from a restatement.

        A hand-written list here would drift the first time a status was added, and the
        symptom would be a run that exists and is counted nowhere.
        """
        self._heartbeat()
        depth = self._snapshot()["queue"]["depth"]
        known = set(self.runs.CLAIMABLE_STATUSES) | set(self.runs.TERMINAL_STATUSES)
        self.assertEqual(set(depth), known)

    def test_the_depth_total_equals_the_table(self) -> None:
        """If the two disagree, one of them is not the queue."""
        for index in range(4):
            self._queue(request_id=f"req_{index}")
        self._heartbeat()
        queue = self._snapshot()["queue"]
        self.assertEqual(queue["total"], 4)
        self.assertEqual(queue["queued"], 4)
        self.assertEqual(sum(queue["depth"].values()), 4)


class AbsentIsNotUnhealthyAndUnknownIsNotMismatched(_Base):
    """The three collapses that would each destroy a reading somebody needs."""

    def test_a_worker_that_never_wrote_a_heartbeat_reads_as_absent_not_as_fresh(self) -> None:
        """The failure this forecloses: an age of zero on a service that has never run."""
        snapshot = self._snapshot()
        worker = snapshot["worker"]
        self.assertFalse(worker["heartbeat_present"])
        self.assertFalse(worker["online"])
        self.assertIsNone(worker["heartbeat_age_seconds"],
                          "a missing heartbeat must not read as an age of zero")

    def test_configured_and_online_are_separate_facts(self) -> None:
        """``configured: true, online: false`` is the production outage this surface is for.

        Merged into one boolean it would be indistinguishable from a feature switched off
        on purpose, which is the state nobody should be paged for.
        """
        self._heartbeat(age_seconds=self.health.ONLINE_WINDOW_SECONDS + 60)
        worker = self._snapshot()["worker"]
        self.assertTrue(worker["configured"])
        self.assertTrue(worker["heartbeat_present"])
        self.assertFalse(worker["online"], "a stale heartbeat is not online")
        self.assertFalse(self._snapshot()["ok"])

    def test_a_worker_switched_off_on_purpose_is_not_an_alarm(self) -> None:
        """``ok`` reports "something is wrong", not "the queue is idle"."""
        # Establish that the flag is what moves this, so the assertion below is not
        # satisfied by a snapshot that reports every worker as unconfigured.
        self.assertTrue(self._snapshot()["worker"]["configured"])
        self.fx.set_flags(UNDX_AGENT_RUNS_ENABLED="0")
        snapshot = self._snapshot()
        self.assertFalse(snapshot["worker"]["configured"])
        self.assertTrue(snapshot["ok"],
                        "a deliberately disabled worker must not read as an outage")

    def test_a_fresh_heartbeat_reads_as_online_with_its_age(self) -> None:
        self._heartbeat(age_seconds=12)
        worker = self._snapshot()["worker"]
        self.assertTrue(worker["online"])
        self.assertEqual(worker["heartbeat_age_seconds"], 12)
        self.assertTrue(self._snapshot()["ok"])

    def test_two_unknown_shas_are_not_a_match(self) -> None:
        """Stage 13's SHA_MATCH must be computed, and absence is not agreement.

        ``unknown == unknown`` is true in Python and false as a claim about a deployment.
        """
        self._heartbeat(sha="unknown")
        snapshot = self.health.snapshot(
            self.fx.cur,
            env=self._env(RAILWAY_GIT_COMMIT_SHA="", APP_BUILD_SHA=""),
            now=self.now,
        )
        self.assertIsNone(snapshot["sha_match"])
        self.assertEqual(snapshot["web"]["sha"], "unknown")
        self.assertEqual(snapshot["worker"]["sha"], "unknown")

    def test_matching_and_diverging_shas_are_distinguishable(self) -> None:
        self._heartbeat(sha="abc123")
        self.assertTrue(self._snapshot()["sha_match"])

        self.fx.cur.execute("DELETE FROM worker_heartbeats")
        self.fx.commit()
        self._heartbeat(sha="def456")
        self.assertFalse(self._snapshot()["sha_match"],
                         "two real shas that differ is the Stage 13 failure")

    def test_a_heartbeat_from_a_different_worker_is_not_this_worker(self) -> None:
        """Scoped by name, so another service's liveness cannot stand in for this one."""
        self._heartbeat(name="coinpilotx-media-worker")
        worker = self._snapshot()["worker"]
        self.assertFalse(worker["heartbeat_present"])
        self.assertEqual(worker["name"], self.runs.WORKER_NAME)


class TheQueueDepthMeansWhatItSays(_Base):
    """Depth is only actionable next to the two numbers that give it a time dimension."""

    def test_an_expired_lease_is_not_an_active_worker(self) -> None:
        """A dead container leaves ``status='running'`` behind until its lease lapses.

        Counting that as active would report a busy worker on a service that is not
        running, which is the single most misleading thing this surface could say.
        """
        live = self._queue(request_id="req_live")
        dead = self._queue(request_id="req_dead")
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='running', lease_owner=?, "
            "lease_expires_at=? WHERE run_id=?",
            ("worker_alpha", _iso(self.now + timedelta(seconds=120)), live),
        )
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='running', lease_owner=?, "
            "lease_expires_at=? WHERE run_id=?",
            ("worker_beta", _iso(self.now - timedelta(seconds=120)), dead),
        )
        self.fx.commit()

        queue = self._snapshot()["queue"]
        self.assertEqual(queue["depth"]["running"], 2, "both rows say running")
        self.assertEqual(queue["active_leases"], 1, "only one is actually held")

    def test_the_oldest_queued_age_distinguishes_a_burst_from_a_stall(self) -> None:
        """Ten runs that arrived this second and one stuck since Tuesday have the same depth."""
        self.assertIsNone(self._snapshot()["queue"]["oldest_queued_age_seconds"],
                          "an empty queue has no oldest run")

        run_id = self._queue()
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET created_at=? WHERE run_id=?",
            (_iso(self.now - timedelta(seconds=900)), run_id),
        )
        self.fx.commit()
        self.assertEqual(self._snapshot()["queue"]["oldest_queued_age_seconds"], 900)

    def test_outstanding_counts_only_the_claimable_states(self) -> None:
        queued = self._queue(request_id="req_q")
        done = self._queue(request_id="req_done")
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='succeeded' WHERE run_id=?", (done,))
        self.fx.commit()

        queue = self._snapshot()["queue"]
        self.assertEqual(queue["outstanding"], 1)
        self.assertEqual(queue["total"], 2)
        self.assertEqual(queue["depth"]["succeeded"], 1)
        self.assertTrue(queued)

    def test_partial_is_counted_as_needing_attention(self) -> None:
        """A run whose read-back could not confirm it is the state most worth surfacing.

        It is not a failure, so filing it under success is defensible and wrong: nobody
        would ever look at it again.
        """
        run_id = self._queue()
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='partial' WHERE run_id=?", (run_id,))
        self.fx.commit()
        self.assertIn("partial", self.health.FAILURE_STATUSES)
        self.assertEqual(self._snapshot()["queue"]["settled_needing_attention"], 1)

    def test_an_unrecognised_stored_status_is_still_counted(self) -> None:
        """Dropping it would make the depth disagree with the table it is a depth of."""
        run_id = self._queue()
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='from_the_future' WHERE run_id=?", (run_id,))
        self.fx.commit()
        queue = self._snapshot()["queue"]
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["depth"].get("from_the_future"), 1)


class TheTwoShaReadsCannotDrift(unittest.TestCase):
    """``sha_match`` is only meaningful while both sides read the environment identically."""

    def _source(self, path: str) -> str:
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
            return handle.read()

    def _env_reads(self, body: str) -> list[str]:
        return re.findall(r"(?:os\.getenv|source\.get)\(\s*[\"']([A-Z_]+)[\"']", body)

    def test_the_web_and_worker_read_the_same_variables_in_the_same_order(self) -> None:
        """Two drifted copies would report a mismatch between two identical deploys.

        That is worse than no check: an alarm that fires on healthy deployments is an
        alarm that gets disabled, taking the real Stage 13 signal with it.
        """
        worker = self._source("undx_worker.py")
        worker_body = worker[worker.index("def _build_sha("):]
        worker_body = worker_body[:worker_body.index("\n\n\ndef ")]

        health = self._source("services/undx_run_health.py")
        health_body = health[health.index("def build_sha("):]
        health_body = health_body[:health_body.index("\n\n\ndef ")]

        self.assertEqual(self._env_reads(worker_body), self._env_reads(health_body))
        self.assertIn("[:40]", worker_body)
        self.assertIn("[:40]", health_body)

    def test_the_health_module_does_not_import_the_web_application(self) -> None:
        """It is importable by the worker, which cannot import ``bot``.

        Asserted at the source level here rather than by denial; the subprocess proof
        lives in ``test_worker_substrate.py`` and covers the modules this one imports.
        """
        source = self._source("services/undx_run_health.py")
        self.assertNotRegex(source, r"^\s*(?:import|from)\s+(?:bot|flask|stripe)\b",
                            "the snapshot must stay usable from the worker process")

    def test_the_snapshot_reads_the_heartbeat_through_the_module_that_owns_it(self) -> None:
        """One reader for one table. A second SELECT here would be a second definition."""
        source = self._source("services/undx_run_health.py")
        self.assertIn("undx_worker_runtime.read_worker_heartbeat", source)
        self.assertNotIn("FROM worker_heartbeats", source)


class TheRouteIsOneUnauthenticatedGet(unittest.TestCase):
    """Asserted over the Flask URL map, because a decorator is a claim and the map is the fact."""

    def setUp(self) -> None:
        from flask import Flask

        from services import undx_run_health_routes

        self.module = undx_run_health_routes
        self.app = Flask(__name__)
        self.module.register(self.app)

    def _rules(self):
        return [rule for rule in self.app.url_map.iter_rules()
                if str(rule.rule).startswith("/health/undx")]

    def test_the_pack_registers_exactly_one_route(self) -> None:
        rules = self._rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(str(rules[0].rule), self.module.ROUTE_PATH)

    def test_that_route_accepts_no_verb_that_could_change_anything(self) -> None:
        """A health surface with a POST on it is a control plane nobody authenticated."""
        methods = set(self._rules()[0].methods) - {"HEAD", "OPTIONS"}
        self.assertEqual(methods, {"GET"})

    def test_the_response_is_not_cacheable(self) -> None:
        """A cached liveness answer is a liveness answer about the past."""
        fx = AgentFixture(**RUN_FLAGS).start()
        try:
            from services import undx_agent_runs, undx_worker_runtime

            undx_agent_runs.ensure_schema(fx.cur)
            fx.cur.execute(undx_worker_runtime.HEARTBEAT_DDL)
            fx.commit()

            import services.db as db_module

            saved = self.module._bot
            self.module._bot = lambda: type("_Bot", (), {"db": staticmethod(db_module.connect)})
            try:
                client = self.app.test_client()
                response = client.get(self.module.ROUTE_PATH)
            finally:
                self.module._bot = saved

            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            self.assertEqual(response.get_json()["surface"], "undx-run-health-1")
        finally:
            fx.stop()

    def test_a_failing_read_reports_a_class_and_never_a_message(self) -> None:
        """This route has no session behind it, so a database error must not narrate itself.

        A statement carries values and an error carries the statement.
        """
        saved = self.module._bot

        def _explode():
            raise RuntimeError("relation undx_agent_runs does not exist for user 4242")

        self.module._bot = lambda: type("_Bot", (), {"db": staticmethod(_explode)})
        try:
            response = self.app.test_client().get(self.module.ROUTE_PATH)
        finally:
            self.module._bot = saved

        self.assertEqual(response.status_code, 503)
        body = json.dumps(response.get_json())
        self.assertNotIn("4242", body)
        self.assertNotIn("undx_agent_runs", body)
        self.assertEqual(response.get_json()["reason"], "unavailable")


class TheRegistrationIsAdditive(unittest.TestCase):
    """Stage 37's do-not-touch rule, asserted on the seam rather than promised in a report."""

    def test_bot_registers_the_pack_through_the_existing_loader(self) -> None:
        with open(os.path.join(ROOT, "bot.py"), "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(
            '_load_route_pack("undx_run_health", "services.undx_run_health_routes")',
            source,
        )

    def test_the_undx_footprint_in_bot_is_three_loader_lines_and_nothing_else(self) -> None:
        """Every UNDX run route reaches the app through ``_load_route_pack`` and no other way.

        The directive says prefer zero edits to ``bot.py``; three additive lines in an
        existing list is the smallest seam available, and this test is what stops it
        growing into a fourth kind of change.
        """
        with open(os.path.join(ROOT, "bot.py"), "r", encoding="utf-8") as handle:
            source = handle.read()
        packs = re.findall(r'_load_route_pack\("(undx_[a-z_]+)"', source)
        self.assertEqual(
            packs,
            ["undx_agent_runs", "undx_agent_run_control", "undx_run_health"],
        )
        self.assertNotIn("import services.undx_run_health_routes", source)
        self.assertNotIn("from services.undx_run_health", source)


if __name__ == "__main__":
    unittest.main()
