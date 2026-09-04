"""Stages 34-38 — bootstrap, signals, health, performance, telemetry.

These five stages are one suite because they are one property: *an operator
looking at the Private Office can tell what is actually happening.* Split
across five files it would be possible for each half to pass while the whole
still lies — a health surface that reports zero, a metric that says the sweep
ran, a schema that was never created because nobody hit a web route.

The failure being defended against has a name in this repo. Stage 176B:
``services/marketplace_reservation_schema.py`` created its lifecycle columns
inside a cart route handler, so ``pulse_worker`` — which never serves a route —
found the columns absent every cycle, died, and reported
``{'scanned': 0, 'candidates': 0, 'released': 0, 'failed': 1}``. That payload
is indistinguishable from a healthy sweep of an empty queue, and it ran for
weeks. Every check below exists because of some part of that sentence.

What each stage is asked to prove
---------------------------------
34  The schema bootstraps from a process that has never served a request, and
    nothing in the package can reach a route to make it happen.
35  ``PRIVATE_SCHEMA_READY``, ``PRIVATE_SCHEMA_MISSING`` and
    ``PRIVATE_SCHEMA_ENSURE_FAILED`` are emitted by the code, not just spelled
    in it. Each is provoked for real.
36  ``health.private_office_health`` distinguishes the four failures it claims
    to distinguish, and leaks nothing about a member while doing it.
37  Owner, subject and edge reads are index-backed — checked by asking SQLite
    for its own query plan, not by reading the DDL — and retrieval does not
    issue one fact query per node.
38  No fact value, subject id, owner id or free-text field can reach a metric.
    Real member data is pushed through every write and read path and the
    emitted payloads are inspected.

On not writing DDL here
-----------------------
``test_private_write_boundary.py`` scans test files too, and refuses to
distinguish "I only meant it as a string" from a genuine bypass — correctly, as
it has no way to tell. So the degraded-schema cases below are provoked by
rebinding module state (``REQUIRED_COLUMNS``, ``table_columns``) rather than by
issuing DDL against a private table. That is also the more honest simulation:
it reproduces what a role without ``ALTER`` actually experiences.

Runs either way::

    python -m pytest tests/private_office/test_private_observability.py
    python tests/private_office/test_private_observability.py
"""

import ast
import inspect
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="private_office_observability_")
_TMP_DB = os.path.join(_TMP_DIR, "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import health  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import telemetry  # noqa: E402

USER_A = 8801
USER_B = 8802
USER_C = 8803

#: Strings planted in real member data. Every one is the kind of thing a
#: metric or a health payload must never contain: an address, a policy number,
#: a person's name, a money value. If any of these turns up in an emitted
#: telemetry payload or in the health JSON, the leak is real and specific
#: rather than theoretical.
SECRETS = (
    "221B Baker Street",
    "POL-99-ALPHA",
    "Marguerite Okonkwo",
    "4750000",
    "marguerite@example.invalid",
)

_FAILURES: list[str] = []
_EMITTED: list[tuple[str, dict]] = []
_REAL_EMIT = telemetry.emit


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def cursor():
    conn = db.connect()
    return conn, conn.cursor()


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------

def _recording_emit(event: str, **fields):
    """``telemetry.emit`` with a tap on it.

    Installed for the whole run rather than per stage, so the Stage 38 checks
    inspect what the *other* stages caused real code to emit. A privacy test
    that only inspects events it raised itself is testing its own fixtures.
    """
    payload = _REAL_EMIT(event, **fields)
    _EMITTED.append((event, dict(payload) if payload is not None else {}))
    return payload


class _LogCapture(logging.Handler):
    """Collects formatted records off one named logger."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):  # noqa: D102 — logging.Handler contract.
        try:
            self.lines.append(record.getMessage() % () if False else record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg))

    def saw(self, token: str) -> bool:
        return any(token in line for line in self.lines)


class _RecordingCursor:
    """Passes every statement through and keeps a copy of it.

    Wraps rather than subclasses because ``services.db`` may hand back either a
    ``sqlite3.Cursor`` or a driver cursor of its own, and this suite must not
    care which. Only ``execute`` is intercepted; everything else is delegated,
    including ``fetchone`` and the row factory, so the code under test cannot
    tell it is being watched.
    """

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((str(sql), tuple(params or ())))
        return self._inner.execute(sql, params) if params else self._inner.execute(sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def selects_against(self, table: str) -> list[tuple[str, tuple]]:
        return [
            (sql, params) for sql, params in self.statements
            if sql.lstrip().upper().startswith("SELECT") and table in sql
        ]


def _seed_owner(cur, owner: int, *, properties: int) -> dict:
    """A person owning ``properties`` properties, each with facts on it.

    The values carry :data:`SECRETS` on purpose — this fixture is what makes
    the Stage 36 and 38 leak checks meaningful rather than vacuous.
    """
    person = graph.upsert_node(
        cur, owner_user_id=owner, node_type=model.NODE_PERSON,
        external_ref=f"person:{owner}")
    node_ids = [person["node_id"]]
    for index in range(properties):
        prop = graph.upsert_node(
            cur, owner_user_id=owner, node_type=model.NODE_PROPERTY,
            external_ref=f"property:{owner}:{index}",
            domain=model.DOMAIN_FINANCIAL)
        graph.record_edge(
            cur, owner_user_id=owner, source=person["node_id"],
            relation_type=model.RELATION_OWNS, target=prop["node_id"],
            provenance_type=model.PROVENANCE_USER_ASSERTED)
        node_ids.append(prop["node_id"])
        facts.record_fact(
            cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
            subject_id=str(prop["node_id"]), fact_type="street_address",
            value=f"{SECRETS[0]} #{index}", value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            domain=model.DOMAIN_GENERAL)
        facts.record_fact(
            cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
            subject_id=str(prop["node_id"]), fact_type="estimated_value",
            value=SECRETS[3], value_type=model.VALUE_MONEY,
            provenance_type=model.PROVENANCE_PROVIDER_ASSERTED,
            domain=model.DOMAIN_FINANCIAL)
    facts.record_fact(
        cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id=str(person["node_id"]), fact_type="legal_name",
        value=SECRETS[2], value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=model.DOMAIN_GENERAL)
    facts.record_fact(
        cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id=str(person["node_id"]), fact_type="contact_email",
        value=SECRETS[4], value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=model.DOMAIN_GENERAL)
    return {"person": person["node_id"], "nodes": node_ids}


# ---------------------------------------------------------------------------
# Stage 34 — the bootstrap is worker-safe
# ---------------------------------------------------------------------------

def stage_worker_safe_bootstrap():
    """Stage 34. Nothing here may depend on a web route having been hit."""
    print("\n[stage 34 — bootstrap]")

    # Asked in a clean interpreter rather than in this one. Reading the ambient
    # `sys.modules` only answers the question when this file is the only thing
    # the process ever imported: run as a directory, the route-pack tests have
    # already put a stub `bot` and the real Flask in there, and the check
    # reports on *their* imports while appearing to report on the package's.
    # A subprocess that imports nothing else is both order-independent and a
    # stricter question than the one it replaces.
    probe = (
        "import sys;"
        "import services.private_office.schema;"
        "import services.private_office.facts;"
        "import services.private_office.graph;"
        "import services.private_office.retrieval;"
        "import services.private_office.telemetry;"
        "import services.private_office.health;"
        "leaked=sorted(m for m in ('bot','flask','werkzeug') if m in sys.modules);"
        "print(','.join(leaked))"
    )
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=repo_root)
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=repo_root, env=env,
        capture_output=True, text=True, timeout=120,
    )
    check("the package imports in a clean interpreter",
          completed.returncode == 0,
          completed.stderr.strip()[-300:])
    leaked = completed.stdout.strip()
    check("importing the package pulls in neither the monolith nor Flask",
          leaked == "", f"leaked: {leaked}")

    # Read from source rather than from `sys.modules`, because an import
    # buried in a function body would not have run yet and so would not show
    # up above — and a lazy import of `bot` inside `ensure_private_schema` is
    # precisely the shape of the Stage 176B defect.
    package_dir = os.path.dirname(inspect.getfile(schema))
    offenders = []
    for name in sorted(os.listdir(package_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(package_dir, name)
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=name)
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in ("bot", "flask", "werkzeug"):
                    # One narrow, named exception: `security.py` reads the
                    # office-unlock grant off the live request, so its
                    # `request_bindings`/`request_grant_token` import `flask`
                    # lazily inside a try/except and fail closed without it.
                    # That is the opposite shape from Stage 176B — the module
                    # works *without* Flask rather than requiring it — and the
                    # clean-interpreter probe above stays the enforcement that
                    # importing the package pulls in neither Flask nor the
                    # monolith. `bot` stays forbidden everywhere.
                    if name == "security.py" and root == "flask":
                        continue
                    offenders.append(f"{name}:{node.lineno} {root}")
    check("no module in the package imports bot, flask or werkzeug — at any depth",
          offenders == [], "; ".join(offenders[:3]))

    # A brand new database, from a process that declares itself a worker.
    schema.reset_schema_cache()
    schema.set_process_role("worker")
    check("an unrecognised process role is refused rather than stored",
          schema.set_process_role("definitely-not-a-role") == "worker")

    conn, cur = cursor()
    result = schema.ensure_private_schema(cur, force=True)
    check("a worker process can create the schema unaided",
          result["status"] == schema.STATUS_READY, str(result["missing"]))
    check("every private table exists after the worker's ensure",
          set(result["tables"]) == set(schema.TABLES),
          str(sorted(result["tables"])))

    # And can immediately use it — the bit Stage 176B's sweeper could not do.
    written = facts.record_fact(
        cur, owner_user_id=USER_C, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id="1", fact_type="bootstrap_probe", value="ok",
        value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    check("the worker can write through the schema it just created",
          written["status"] in (facts.STATUS_WRITTEN, facts.STATUS_REFRESHED),
          written["status"])
    read_back = facts.list_facts(
        cur, owner_user_id=USER_C, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id="1")
    check("and can read it back in the same process", len(read_back) == 1)

    check("the ensure is reported as this process's role, not anonymously",
          any(payload.get("process") == "worker"
              for event, payload in _EMITTED
              if event == telemetry.EVENT_SCHEMA_STATE),
          str([p for e, p in _EMITTED if e == telemetry.EVENT_SCHEMA_STATE][:1]))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Stage 35 — the three named signals
# ---------------------------------------------------------------------------

def stage_schema_signals():
    """Stage 35. Each signal is provoked, not grepped for."""
    print("\n[stage 35 — schema signals]")
    logger = logging.getLogger("private_office.schema")
    capture = _LogCapture()
    previous_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)

    conn, cur = cursor()
    try:
        schema.reset_schema_cache()
        schema.ensure_private_schema(cur, force=True)
        check("PRIVATE_SCHEMA_READY is emitted on a healthy ensure",
              capture.saw("PRIVATE_SCHEMA_READY"))

        # A role that can read the tables but cannot add a column sees exactly
        # this: the ensure completes, and a required column is still absent.
        capture.lines.clear()
        original_required = schema.REQUIRED_COLUMNS
        patched = dict(original_required)
        patched[schema.FACTS_TABLE] = original_required[schema.FACTS_TABLE] + (
            "column_this_deployment_never_got",)
        schema.REQUIRED_COLUMNS = patched
        try:
            schema.reset_schema_cache()
            missing = schema.ensure_private_schema(cur, force=True)
        finally:
            schema.REQUIRED_COLUMNS = original_required
        check("a required column that cannot be added yields status missing",
              missing["status"] == schema.STATUS_MISSING, missing["status"])
        check("PRIVATE_SCHEMA_MISSING is emitted with it",
              capture.saw("PRIVATE_SCHEMA_MISSING"))
        check("the missing column is named in the result so it can be migrated",
              "column_this_deployment_never_got"
              in (missing["missing"].get(schema.FACTS_TABLE) or []),
              str(missing["missing"]))

        # An unreachable or unreadable database: introspection itself fails.
        capture.lines.clear()
        original_columns = schema.table_columns

        def _explode(*_args, **_kwargs):
            raise RuntimeError("relation does not exist")

        schema.table_columns = _explode
        try:
            schema.reset_schema_cache()
            broken = schema.ensure_private_schema(cur, force=True)
        finally:
            schema.table_columns = original_columns
        check("an introspection failure yields status error, not missing",
              broken["status"] == schema.STATUS_ERROR, broken["status"])
        check("PRIVATE_SCHEMA_ENSURE_FAILED is emitted with it",
              capture.saw("PRIVATE_SCHEMA_ENSURE_FAILED"))
        check("error and missing are different states, so they can be routed apart",
              schema.STATUS_ERROR != schema.STATUS_MISSING)

        # The three states must also be distinguishable in the metric, or an
        # operator watching a dashboard rather than a log stream cannot tell.
        states = {
            payload.get("state") for event, payload in _EMITTED
            if event == telemetry.EVENT_SCHEMA_STATE
        }
        check("all three schema states reached the metric",
              {schema.STATUS_READY, schema.STATUS_MISSING, schema.STATUS_ERROR}
              <= states, str(sorted(states)))

        schema.reset_schema_cache()
        recovered = schema.ensure_private_schema(cur, force=True)
        check("the schema recovers once the fault is removed",
              recovered["status"] == schema.STATUS_READY)
        conn.commit()
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        conn.close()


# ---------------------------------------------------------------------------
# Stage 36 — the health surface
# ---------------------------------------------------------------------------

def stage_health_surface():
    """Stage 36. Four failures kept apart; nothing about a member on the way."""
    print("\n[stage 36 — health]")

    conn, cur = cursor()
    _seed_owner(cur, USER_A, properties=3)
    facts.record_fact(
        cur, owner_user_id=USER_A, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id="900", fact_type="policy_number", value=SECRETS[1],
        value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_PROVIDER_ASSERTED,
        domain=model.DOMAIN_LEGAL)
    conn.commit()
    conn.close()

    signature = inspect.signature(health.private_office_health)
    identifier_params = [
        name for name in signature.parameters
        if any(token in name.lower()
               for token in ("user", "owner", "actor", "member", "subject"))
    ]
    check("the health surface accepts no user identifier",
          identifier_params == [], str(identifier_params))
    check("every parameter is keyword-only, so nothing can be passed positionally",
          all(p.kind == inspect.Parameter.KEYWORD_ONLY
              for p in signature.parameters.values()))

    payload = health.private_office_health()
    blob = json.dumps(payload, default=str)

    leaked = [secret for secret in SECRETS if secret in blob]
    check("no member value appears anywhere in the health payload",
          leaked == [], str(leaked))
    for identifier in (USER_A, USER_B, USER_C):
        check(f"no owner id appears in the health payload ({identifier})",
              str(identifier) not in blob)

    check("the payload reports which engine it looked at",
          bool(payload.get("engine")))
    check("the schema section reports the process that bootstrapped it",
          payload["schema"]["bootstrapped_by"] == schema.process_role(),
          payload["schema"]["bootstrapped_by"])
    check("a schema error message is never published, only its presence",
          payload["schema"]["error_type"] == "",
          payload["schema"]["error_type"])
    check("the fact store reports itself live once the schema is usable",
          payload["substrate"]["implementation"] == health.IMPL_LIVE)
    check("the counts are real integers, not placeholders",
          all(isinstance(v, int) for v in payload["substrate"]["counts"].values()),
          str(payload["substrate"]["counts"]))
    check("the seeded facts are counted",
          payload["substrate"]["counts"]["facts"] > 0)
    check("the total is the sum when every part was counted",
          payload["substrate"]["total_rows"]
          == sum(payload["substrate"]["counts"].values()))
    check("overall state is healthy when nothing is wrong",
          payload["state"] == health.STATE_HEALTHY, payload["state"])

    # "Not counted" and "counted zero" must not share a shape — Stage 176B in
    # one assertion.
    cheap = health.private_office_health(include_counts=False,
                                         include_entitlement=False)
    check("skipping the counts reports None rather than zero",
          all(v is None for v in cheap["substrate"]["counts"].values()),
          str(cheap["substrate"]["counts"]))
    check("and says so, so a reader is not left to infer it",
          "include_counts=False" in cheap["substrate"]["count_note"],
          cheap["substrate"]["count_note"])
    check("a skipped count never produces a total",
          cheap["substrate"]["total_rows"] is None)
    check("skipping the counts does not itself make the system unhealthy",
          cheap["state"] == health.STATE_HEALTHY, cheap["state"])

    # Failure 2 of the four: the schema is not on this database.
    original_ensure = health._schema.ensure_private_schema
    health._schema.ensure_private_schema = lambda cur, **kw: {
        "status": schema.STATUS_MISSING, "tables": {}, "missing": {"private_facts": []},
        "added": [], "error": None,
    }
    try:
        degraded = health.private_office_health(include_entitlement=False)
    finally:
        health._schema.ensure_private_schema = original_ensure
    check("a missing schema degrades the overall state",
          degraded["state"] == health.STATE_DEGRADED, degraded["state"])
    check("a missing schema is reported as not ready, not as empty",
          degraded["substrate"]["implementation"] == health.IMPL_NOT_READY)
    check("nothing is counted against a schema that is not there",
          all(v is None for v in degraded["substrate"]["counts"].values()))
    check("and the reason given is the schema, not the caller",
          "schema not usable" in degraded["substrate"]["count_note"],
          degraded["substrate"]["count_note"])

    # Failure 1: the resolver is down. Distinct from a missing schema.
    original_status = health._status.subsystem_status

    def _resolver_down(**_kwargs):
        raise RuntimeError("entitlement backend unreachable")

    health._status.subsystem_status = _resolver_down
    try:
        no_resolver = health.private_office_health(include_counts=False)
    finally:
        health._status.subsystem_status = original_status
    check("a dead resolver degrades the overall state",
          no_resolver["state"] == health.STATE_DEGRADED, no_resolver["state"])
    check("a dead resolver leaves the schema reported as ready",
          no_resolver["schema"]["usable"] is True)
    check("the resolver failure is reported by type, not by message",
          no_resolver["entitlement"] == {"error_type": "RuntimeError"},
          str(no_resolver["entitlement"]))

    # Failure 4: retrieval is configured to refuse. Answerable from code, so
    # it survives every database failure above.
    check("the retrieval policy is reported even when nothing else works",
          set(degraded["retrieval"]["intents"]) == set(retrieval.INTENTS))
    check("the refusal vocabulary is published so a denial can be recognised",
          retrieval.DENIED_NOT_OWNER in degraded["retrieval"]["denial_reasons"])
    check("the traversal bounds are published",
          degraded["retrieval"]["bounds"]["max_depth"] == retrieval.MAX_DEPTH)
    check("the batch bound is published, so an N+1 can be reasoned about",
          degraded["retrieval"]["bounds"]["max_subject_batch"]
          == facts.MAX_SUBJECT_BATCH)

    # A count that fails is None, never 0 — the rule, checked at the seam.
    class _CountsExplode:
        def execute(self, sql, params=()):
            if "COUNT(" in str(sql).upper():
                raise RuntimeError("statement timeout")
            return None

        def fetchone(self):
            return None

    broken_counts = health._substrate_section(
        _CountsExplode(), schema_usable=True, include_counts=True)
    check("a count that raised is None, never zero",
          all(v is None for v in broken_counts["counts"].values()),
          str(broken_counts["counts"]))
    check("a partial count never becomes a total",
          broken_counts["total_rows"] is None)

    check("the health surface never raises, whatever it finds",
          isinstance(health.private_office_health(include_counts=False), dict))


# ---------------------------------------------------------------------------
# Stage 37 — index-backed reads, and no N+1
# ---------------------------------------------------------------------------

def _plan_for(sql: str, params: tuple) -> list[str]:
    """SQLite's own plan for ``sql``. Empty list if it could not be explained."""
    raw = sqlite3.connect(_TMP_DB)
    try:
        rows = raw.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
        return [str(row[-1]) for row in rows]
    except Exception:  # noqa: BLE001 — an unexplainable statement is reported, not fatal.
        return []
    finally:
        raw.close()


def stage_reads_are_index_backed():
    """Stage 37. The planner is asked, rather than the DDL being read.

    An index existing and an index being *used* are different claims, and only
    the second one is the one that matters at 3am. A composite index leading
    with ``owner_user_id`` is useless to a query that filters on something
    else first, and nothing in the DDL would tell you that.
    """
    print("\n[stage 37 — index use]")
    conn, cur = cursor()
    recorder = _RecordingCursor(cur)
    _seed_owner(recorder, USER_B, properties=6)
    conn.commit()

    recorder.statements.clear()
    result = retrieval.retrieve(
        recorder, owner_user_id=USER_B, actor_user_id=USER_B,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_types=[model.NODE_PERSON])
    check("the traversal returned something to have planned",
          not result["denied"] and len(result["relevant_nodes"]) > 1,
          f"denied={result['denied']} nodes={len(result['relevant_nodes'])}")

    scanned: list[str] = []
    explained = 0
    for table in schema.TABLES:
        for sql, params in recorder.selects_against(table):
            plan = _plan_for(sql, params)
            if not plan:
                continue
            explained += 1
            for line in plan:
                if f"SCAN {table}" in line and "USING" not in line:
                    scanned.append(f"{line} :: {sql.strip()[:90]}")
    check("the query plans were actually obtainable", explained > 0,
          f"explained={explained}")
    check("no read of a private table falls back to a full table scan",
          scanned == [], "; ".join(scanned[:2]))

    # N+1: the number of fact queries must not track the number of subjects.
    # A per-node read would be correct and would also be 100 round trips.
    small = _RecordingCursor(conn.cursor())
    _seed_owner(small, USER_C + 10, properties=1)
    conn.commit()
    small.statements.clear()
    retrieval.retrieve(
        small, owner_user_id=USER_C + 10, actor_user_id=USER_C + 10,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_types=[model.NODE_PERSON])
    small_reads = len(small.selects_against(schema.FACTS_TABLE))

    large = _RecordingCursor(conn.cursor())
    _seed_owner(large, USER_C + 11, properties=24)
    conn.commit()
    large.statements.clear()
    big_result = retrieval.retrieve(
        large, owner_user_id=USER_C + 11, actor_user_id=USER_C + 11,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_types=[model.NODE_PERSON])
    large_reads = len(large.selects_against(schema.FACTS_TABLE))

    check("the larger graph really was larger",
          len(big_result["relevant_nodes"]) >= 20,
          str(len(big_result["relevant_nodes"])))
    check("24x the subjects does not mean 24x the fact queries",
          large_reads == small_reads, f"{small_reads} -> {large_reads}")
    check("and the number of fact queries is small in absolute terms",
          large_reads <= 4, str(large_reads))
    check("the batch read is the mechanism, and it is bounded",
          facts.MAX_SUBJECT_BATCH > 0 and facts.MAX_SUBJECT_BATCH <= 1000,
          str(facts.MAX_SUBJECT_BATCH))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Stage 38 — no member data reaches a metric
# ---------------------------------------------------------------------------

def stage_telemetry_carries_no_member_data():
    """Stage 38. Real data is pushed through every path; payloads are read."""
    print("\n[stage 38 — telemetry]")
    check("the event spec is internally sound",
          telemetry.spec_is_sound() == [], str(telemetry.spec_is_sound()))

    conn, cur = cursor()

    # Provoke the two events the stages above do not: a refusal, and a
    # detected contradiction.
    denied = retrieval.retrieve(
        cur, owner_user_id=USER_A, actor_user_id=USER_B,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO)
    check("a cross-account read is refused", denied["denied"] == retrieval.DENIED_NOT_OWNER,
          str(denied["denied"]))

    for value, source in (("35", model.PROVENANCE_USER_ASSERTED),
                          ("40", model.PROVENANCE_PROVIDER_ASSERTED)):
        facts.record_fact(
            cur, owner_user_id=USER_A, subject_type=retrieval.SUBJECT_TYPE_NODE,
            subject_id="910", fact_type="ownership_share", value=value,
            value_type=model.VALUE_PERCENT, provenance_type=source,
            observed_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_to="2026-12-31T00:00:00+00:00")
    conflicts = contradictions.detect_conflicts(
        cur, owner_user_id=USER_A, subject_id="910")
    check("the contradiction was detected, so its metric fired",
          len(conflicts) == 1, str(len(conflicts)))

    # And the three record events, through the real writer, the real closer,
    # and the real typed view — with a planted secret in the title so the
    # payload inspection below has something concrete to catch.
    obligation = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title=f"Premium for {SECRETS[0]}", obligation_type="TAX",
        due_at="2026-12-01T00:00:00+00:00", domain="FINANCIAL")
    check("a record write fired its metric through the canonical writer",
          obligation["status"] == records.STATUS_CREATED,
          str(obligation["status"]))
    closed = records.update_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        record_id=obligation["record_id"], status="RESOLVED")
    check("closing the obligation fired the closure metric",
          (closed["record"] or {}).get("status") == "RESOLVED",
          str((closed["record"] or {}).get("status")))
    listed = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, actor_user_id=USER_A,
        view=retrieval.VIEW_OBLIGATIONS,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO)
    check("the typed record view read back the row and fired its metric",
          listed["denied"] == "" and len(listed["records"]) >= 1,
          f"denied={listed['denied']!r} count={len(listed['records'])}")

    conn.commit()
    conn.close()

    seen_events = {event for event, _ in _EMITTED}
    check("every declared event was emitted by real code, not by a fixture",
          set(telemetry.EVENTS) <= seen_events,
          str(sorted(set(telemetry.EVENTS) - seen_events)))
    check("no undeclared event was emitted",
          seen_events <= set(telemetry.EVENTS),
          str(sorted(seen_events - set(telemetry.EVENTS))))

    # The central claim. Every payload from every path above, inspected.
    leaks: list[str] = []
    undeclared: list[str] = []
    wrong_kind: list[str] = []
    for event, payload in _EMITTED:
        spec = telemetry.EVENTS[event]
        for key, value in payload.items():
            if key not in spec:
                undeclared.append(f"{event}.{key}")
                continue
            kind, vocab = spec[key]
            if kind == telemetry.KIND_COUNT and not isinstance(value, int):
                wrong_kind.append(f"{event}.{key}={value!r}")
            elif kind == telemetry.KIND_FLAG and not isinstance(value, bool):
                wrong_kind.append(f"{event}.{key}={value!r}")
            elif kind == telemetry.KIND_ENUM:
                if not isinstance(value, str):
                    wrong_kind.append(f"{event}.{key}={value!r}")
                elif vocab is not None and value not in vocab and value != telemetry.OTHER:
                    wrong_kind.append(f"{event}.{key}={value!r} not in vocabulary")
            text = str(value)
            for secret in SECRETS:
                if secret in text:
                    leaks.append(f"{event}.{key}")

    check("no member value reached any emitted metric", leaks == [], str(leaks[:3]))
    check("no metric carried a field its spec does not declare",
          undeclared == [], str(undeclared[:3]))
    check("every emitted field matched its declared kind",
          wrong_kind == [], str(wrong_kind[:3]))
    check("no declared field is one of the identifiers this package never publishes",
          all(not (set(spec) & telemetry.FORBIDDEN_FIELDS)
              for spec in telemetry.EVENTS.values()))

    # Every emitted enum is drawn from a closed set, so no member-influenced
    # string can become a new metric dimension.
    for event, payload in _EMITTED:
        for key, (kind, vocab) in telemetry.EVENTS[event].items():
            if kind == telemetry.KIND_ENUM and vocab is not None:
                if payload.get(key) not in vocab and payload.get(key) != telemetry.OTHER:
                    check(f"{event}.{key} stays inside its vocabulary", False,
                          str(payload.get(key)))
    check("emitted enums stay inside their closed vocabularies", True)

    # Now the hostile direct calls: what happens when a future caller does the
    # wrong thing on purpose.
    hostile = _REAL_EMIT(
        telemetry.EVENT_FACT_WRITE,
        outcome="written",
        domain=SECRETS[0],
        sensitivity=SECRETS[1],
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        superseded=False,
    )
    check("an address passed as a domain becomes 'other', not the address",
          hostile["domain"] == telemetry.OTHER, str(hostile["domain"]))
    check("a policy number passed as a sensitivity becomes 'other'",
          hostile["sensitivity"] == telemetry.OTHER, str(hostile["sensitivity"]))

    smuggled = _REAL_EMIT(
        telemetry.EVENT_CONTEXT_RETRIEVED,
        intent=retrieval.INTENT_GENERAL,
        owner_user_id=USER_A,
        subject_id="910",
        typed_value=SECRETS[3],
        note=SECRETS[2],
        fact_count=2,
    )
    check("undeclared fields are dropped rather than passed through",
          set(smuggled) == set(telemetry.EVENTS[telemetry.EVENT_CONTEXT_RETRIEVED]),
          str(sorted(set(smuggled) - set(telemetry.EVENTS[telemetry.EVENT_CONTEXT_RETRIEVED]))))
    check("nothing smuggled survives into the payload",
          not any(secret in json.dumps(smuggled) for secret in SECRETS),
          json.dumps(smuggled))
    check("a declared field is always present, even when the caller omits it",
          "conflict_count" in smuggled and smuggled["conflict_count"] == 0)

    # A policy number is digits. A count field that parsed strings would turn
    # it into a metric value, which is a leak wearing an integer's clothes.
    numeric = _REAL_EMIT(
        telemetry.EVENT_CONTEXT_RETRIEVED,
        intent=retrieval.INTENT_GENERAL, fact_count="4750000")
    check("a digit string offered as a count is refused, not parsed",
          numeric["fact_count"] == 0, str(numeric["fact_count"]))
    huge = _REAL_EMIT(
        telemetry.EVENT_CONTEXT_RETRIEVED,
        intent=retrieval.INTENT_GENERAL, fact_count=10 ** 12)
    check("a count is bounded, so an id cannot ride in as a magnitude",
          huge["fact_count"] <= telemetry.MAX_COUNT, str(huge["fact_count"]))

    check("an unknown event is dropped rather than logged with its fields",
          _REAL_EMIT("private_office.not_a_real_event", value=SECRETS[0]) is None)
    check("emit never raises, whatever it is handed",
          _REAL_EMIT(telemetry.EVENT_FACT_WRITE,
                     outcome=object(), domain=None, superseded=object()) is not None)


# ---------------------------------------------------------------------------

def main() -> int:
    print("PRIVATE OFFICE OBSERVABILITY — Stages 34-38")
    print(f"database: {_TMP_DB}")
    # Order-dependent and non-reentrant, like the substrate and retrieval
    # suites: Stage 36 reads what Stage 34 bootstrapped, and Stage 38 inspects
    # what all of the stages above it caused to be emitted. That is deliberate
    # — a privacy check that only sees its own fixtures proves nothing about
    # the code. Hence `stage_*` rather than `test_*`, so pytest collects only
    # the single entry point below and runs them in this order.
    _FAILURES.clear()
    _EMITTED.clear()
    for suffix in ("", "-wal", "-shm"):
        path = _TMP_DB + suffix
        if os.path.exists(path):
            os.remove(path)
    schema.reset_schema_cache()
    telemetry.emit = _recording_emit
    try:
        stage_worker_safe_bootstrap()
        stage_schema_signals()
        stage_health_surface()
        stage_reads_are_index_backed()
        stage_telemetry_carries_no_member_data()
    finally:
        telemetry.emit = _REAL_EMIT

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_observability_suite():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
