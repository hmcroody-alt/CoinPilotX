"""Stage #176 Stage 25 — what the worker is allowed to do with the sweeper.

These tests are about *scheduling and authority*, not about reservations. The
decision table, the Stripe reconciliation and the release path are already
covered by ``test_reservation_sweeper.py``; nothing here re-tests them. What is
tested here is the far smaller and far more dangerous surface that the worker
adds:

* that a worker with no configuration never mutates anything,
* that the flag which stands between a scheduling bug and released inventory
  fails closed against every unparseable value,
* that the sweep does not inherit the feed loop's 20-second cadence,
* that a sweep which raises neither kills the worker nor spins,
* and that the worker contains no second copy of the settlement logic.

``pulse_worker`` imports ``bot``, which is a 111k-line Flask monolith, so the
module is imported once at collection and the environment is manipulated around
it rather than re-imported per case. Every configuration reader in the worker
reads ``os.environ`` at call time precisely so that this is possible — and so
that a Railway variable change takes effect on the next sweep rather than
requiring a redeploy.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sqlite3
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services import marketplace_reservation_sweeper as sweeper  # noqa: E402

WORKER_PATH = os.path.join(REPO_ROOT, "pulse_worker.py")


def _load_worker_with_stubbed_monolith():
    """Import ``pulse_worker`` without booting the Flask monolith.

    ``pulse_worker`` imports ``bot``, and importing ``bot`` runs ``init_db()``
    against whatever database the environment points at. That is correct for a
    worker and useless here: these tests are about whether the sweep is
    scheduled and bounded, and none of that needs 1,538 routes or a populated
    database. Requiring one would make this suite fail for reasons that have
    nothing to do with the code it guards — which is exactly what happens today
    on a checkout whose local SQLite file carries legacy rows.

    The stubs are installed only for the duration of this import and
    ``sys.modules`` is restored immediately afterwards, so a later test module
    that wants the real ``bot`` still gets it regardless of collection order.
    The loaded module keeps its reference to the stub, which is what the
    fixtures below patch.
    """
    stub_bot = types.ModuleType("bot")
    stub_bot.sqlite3 = sqlite3
    stub_bot.PULSE_SPACES = {}

    def _unpatched_db():  # pragma: no cover - a test that reaches this is a bug
        raise AssertionError(
            "pulse_worker opened a real database connection; the test should "
            "have patched bot.db"
        )

    stub_bot.db = _unpatched_db
    stub_bot.init_db = lambda: None
    stub_bot.record_worker_heartbeat = lambda *a, **k: None

    stub_feed = types.ModuleType("services.pulse_feed_engine")
    stub_feed.process_pending_jobs = lambda *a, **k: {}
    stub_feed.create_post = lambda *a, **k: None
    stub_ai = types.ModuleType("services.pulse_ai")
    stub_ai.run_due_space_ai_posts = lambda *a, **k: {}

    replacements = {
        "bot": stub_bot,
        "services.pulse_feed_engine": stub_feed,
        "services.pulse_ai": stub_ai,
    }
    saved = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec = importlib.util.spec_from_file_location(
            "pulse_worker_under_test", WORKER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


pulse_worker = _load_worker_with_stubbed_monolith()

ALL_SWEEP_VARS = (
    pulse_worker.SWEEP_ENABLED_ENV_VAR,
    pulse_worker.SWEEP_DRY_RUN_ENV_VAR,
    pulse_worker.SWEEP_SECONDS_ENV_VAR,
    sweeper.BATCH_LIMIT_ENV_VAR,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every case starts from an unconfigured worker.

    Without this a value left by an earlier case would silently satisfy a later
    one, and the most important case in this file — "a worker with no
    configuration does not mutate" — is exactly the one that a leaked variable
    would turn into a false pass.
    """
    for var in ALL_SWEEP_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


class _RecordingSweep:
    """Stands in for ``run_reservation_expiry_sweep`` and records its calls."""

    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[dict] = []
        self.raises = raises

    def __call__(self, cur, **kwargs):
        self.calls.append(dict(kwargs))
        if self.raises is not None:
            raise self.raises
        return {
            "scanned": 0, "candidates": 0, "released": 0, "captured": 0,
            "deferred": 0, "skipped": 0, "reconciled": 0, "failed": 0,
            "would_release": 0, "would_defer": 0, "would_skip": 0,
            "provider_calls": 0, "needs_attention": 0,
            "dry_run": bool(kwargs.get("dry_run")), "limit": kwargs.get("limit"),
            "batch_exhausted": False, "duration_ms": 1,
        }


class _FakeConn:
    """A connection that satisfies the worker's usage without a database.

    The worker's contract with the database is narrow — open, set row_factory,
    take a cursor, commit, close — so faking it keeps these tests about
    scheduling instead of about SQLite.
    """

    def __init__(self):
        self.row_factory = None
        self.committed = False
        self.closed = False

    def cursor(self):
        return object()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


@pytest.fixture
def patched(monkeypatch):
    """Install a recording sweep and a fake connection; hand both back."""

    def _install(*, raises: Exception | None = None):
        recorder = _RecordingSweep(raises=raises)
        conns: list[_FakeConn] = []

        def _db():
            conn = _FakeConn()
            conns.append(conn)
            return conn

        monkeypatch.setattr(sweeper, "run_reservation_expiry_sweep", recorder)
        monkeypatch.setattr(pulse_worker.bot, "db", _db)
        return recorder, conns

    return _install


# --------------------------------------------------------------------------
# 01-03 — the flag decides whether anything happens, and in which mode
# --------------------------------------------------------------------------

def test_01_disabled_worker_never_calls_the_sweeper(patched, monkeypatch):
    """The default state of an unconfigured worker is: do nothing at all.

    Not "call the sweeper in dry run" — do not call it. A disabled sweep must
    not open a connection, must not read the reservations table and must not
    reach Stripe, because "enabled=false" has to be a real off switch during an
    incident rather than a mode.
    """
    recorder, conns = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "false")

    state: dict = {}
    assert pulse_worker.run_reservation_sweep_if_due(state) is None
    assert recorder.calls == []
    assert conns == []


def test_02_enabled_dry_run_calls_the_sweeper_in_dry_run(patched, monkeypatch):
    recorder, conns = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "true")

    outcome = pulse_worker.run_reservation_sweep_if_due({})

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["dry_run"] is True
    assert outcome["status"] == "ok"
    assert outcome["dry_run"] is True
    assert conns[0].committed and conns[0].closed


def test_03_enabled_mutating_calls_the_sweeper_in_mutate_mode(patched, monkeypatch):
    """The only configuration that mutates is an explicit pair of flags."""
    recorder, _ = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "false")

    pulse_worker.run_reservation_sweep_if_due({})

    assert recorder.calls[0]["dry_run"] is False


# --------------------------------------------------------------------------
# 04-06 — cadence: the sweep must not inherit the feed loop
# --------------------------------------------------------------------------

def _feed_sleep_default() -> int:
    """The feed loop's *default* cadence, read out of the worker's source.

    Deliberately not ``pulse_worker.SLEEP_SECONDS``: that is resolved from the
    environment at import time, so a sandbox that happens to export
    ``PULSE_WORKER_SLEEP_SECONDS=30`` would fail this case for a reason that has
    nothing to do with the code under test — and, worse, a sandbox exporting a
    small value would make it pass vacuously. Reading the literal keeps the
    assertion about the committed default while still noticing if that default
    is ever changed out from under the sweep cadence.
    """
    source = open(WORKER_PATH, "r", encoding="utf-8").read()
    match = re.search(r'PULSE_WORKER_SLEEP_SECONDS"\s*,\s*"(\d+)"', source)
    assert match, "could not read the feed loop's default cadence from source"
    return int(match.group(1))


def test_04_sweep_interval_is_independent_of_the_feed_loop(monkeypatch):
    """The two cadences are separate settings and the sweep's is far slower.

    This is the assertion that would fail if someone later "simplified" the
    worker by running the sweep every cycle. The feed loop's period is the
    thing the sweep was deliberately decoupled from, so the relationship is
    asserted rather than assumed.
    """
    feed_sleep = _feed_sleep_default()
    assert pulse_worker.DEFAULT_SWEEP_SECONDS == 300
    assert pulse_worker.SWEEP_SECONDS_ENV_VAR != "PULSE_WORKER_SLEEP_SECONDS"
    assert pulse_worker.sweep_interval_seconds() >= 15 * feed_sleep

    # Even the fastest configuration a typo can produce stays several feed
    # cycles apart, so the clamp — not just the default — carries the property.
    assert pulse_worker.MIN_SWEEP_SECONDS >= 3 * feed_sleep

    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "120")
    assert pulse_worker.sweep_interval_seconds() == 120


def test_05_the_sweep_declines_cycles_until_its_own_deadline(patched, monkeypatch):
    """Fourteen feed cycles in a row must produce exactly one sweep.

    The feed loop will call this function roughly every 20 seconds. If the
    deadline were not honoured, a five-minute cadence would become a
    twenty-second one and provider traffic would rise fifteenfold — the precise
    failure the separate cadence exists to prevent.
    """
    recorder, conns = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "300")

    state: dict = {}
    for _ in range(15):
        pulse_worker.run_reservation_sweep_if_due(state)

    assert len(recorder.calls) == 1, "the sweep ran more than once inside one interval"
    assert len(conns) == 1
    assert state["reservation_sweep_due_at"] > 0


def test_06_the_sweep_runs_again_once_the_interval_has_elapsed(patched, monkeypatch):
    """Scheduling that never fires twice is not scheduling.

    The deadline is rewound rather than the clock advanced, because the worker
    reads ``time.monotonic()`` and a test that patched the clock would be
    asserting against its own patch instead of against the deadline arithmetic.
    """
    recorder, _ = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "300")

    state: dict = {}
    pulse_worker.run_reservation_sweep_if_due(state)
    assert len(recorder.calls) == 1

    state["reservation_sweep_due_at"] = 0.0  # interval has elapsed
    pulse_worker.run_reservation_sweep_if_due(state)
    assert len(recorder.calls) == 2


def test_07_scheduling_is_monotonic_not_a_cycle_counter():
    """The deadline must come from a monotonic clock.

    A cycle counter would drift with feed load — the loop's true period is
    ``sleep + work`` — and a wall-clock deadline would jump on an NTP
    correction. Asserted at the source level because the alternative is
    asserting on timing, which is flaky.
    """
    source = open(WORKER_PATH, "r", encoding="utf-8").read()
    body = source.split("def run_reservation_sweep_if_due")[1].split("\ndef ")[0]
    assert "time.monotonic()" in body
    assert "loop_count" not in body
    assert "datetime.now" not in body, "the sweep deadline must not use wall clock"


# --------------------------------------------------------------------------
# 08-09 — failure containment
# --------------------------------------------------------------------------

def test_08_a_sweeper_exception_does_not_kill_the_worker(patched, monkeypatch):
    """A sweep that raises must be an incident for the sweep only.

    The feed is the worker's primary job and predates the sweep entirely. A
    marketplace bug taking the feed offline would be a strictly worse outcome
    than the stranded inventory the sweep exists to fix.
    """
    recorder, conns = patched(raises=RuntimeError("stripe exploded"))
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")

    outcome = pulse_worker.run_reservation_sweep_if_due({})  # must not raise

    assert outcome["status"] == "error"
    assert "stripe exploded" in outcome["error"]
    assert conns[0].closed, "the connection must be closed even when the sweep raises"
    assert not conns[0].committed


def test_09_a_failing_sweep_waits_a_full_interval_instead_of_hot_looping(
        patched, monkeypatch):
    """The deadline advances in ``finally``, so failure does not cause a spin.

    Without this, a persistently failing sweep would retry on every feed cycle:
    three attempts a minute against a provider that is already unhealthy, which
    is how a small outage becomes a rate-limit incident.
    """
    recorder, _ = patched(raises=RuntimeError("boom"))
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "300")

    state: dict = {}
    for _ in range(15):
        pulse_worker.run_reservation_sweep_if_due(state)

    assert len(recorder.calls) == 1, "a failing sweep retried inside its own interval"


# --------------------------------------------------------------------------
# 10-12 — configuration safety
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["", "   ", "maybe", "TRUE_ISH", "yes please",
                                 "0.5", "null", "None", "-1", "٢"])
def test_10_unparseable_flags_resolve_to_the_safe_direction(raw, monkeypatch):
    """Garbage in either flag must never enable mutation.

    Both flags are set to the same nonsense simultaneously, because the
    dangerous configuration is the *pair* — a value that reads as "on" for
    enabled and "off" for dry-run is what turns a typo into released inventory.
    """
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, raw)
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, raw)

    assert pulse_worker.sweep_enabled() is False
    assert pulse_worker.sweep_dry_run() is True


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("  Yes  ", True), ("on", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False), ("off", False),
])
def test_11_recognised_flag_spellings_parse_both_ways(raw, expected, monkeypatch):
    """Both flags, both directions — the dry-run flag especially.

    ``sweep_dry_run()`` defaults to ``True``, so a parser bug that ignored its
    input entirely would still satisfy every safety case in this file while
    making the flag impossible to turn *off*. Exercising it against recognised
    spellings is what distinguishes "fails closed" from "is stuck".
    """
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, raw)
    assert pulse_worker.sweep_enabled() is expected

    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, raw)
    assert pulse_worker.sweep_dry_run() is expected


def test_12_the_default_configuration_is_disabled_and_non_mutating():
    """With nothing set at all, the worker neither sweeps nor mutates.

    This is the state of any environment that has not been deliberately
    configured — including a fresh deploy, a rolled-back deploy, and a new
    Railway service someone clones from this one.
    """
    assert pulse_worker.sweep_enabled() is False
    assert pulse_worker.sweep_dry_run() is True
    assert pulse_worker.sweep_interval_seconds() == pulse_worker.DEFAULT_SWEEP_SECONDS


@pytest.mark.parametrize("raw,expected", [
    ("1", 60), ("0", 60), ("-500", 60), ("30", 60),          # clamped up
    ("100000", 300), ("301", 300),                            # clamped down
    ("60", 60), ("120", 120), ("300", 300),                   # in range
    ("banana", 300), ("", 300), ("5.5", 300),                 # unparseable
])
def test_13_the_interval_is_clamped_so_a_typo_cannot_make_a_hot_loop(
        raw, expected, monkeypatch):
    """A missing zero must not turn a five-minute sweep into a five-second one."""
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, raw)
    assert pulse_worker.sweep_interval_seconds() == expected


# --------------------------------------------------------------------------
# 14 — the worker passes a bounded limit
# --------------------------------------------------------------------------

def test_14_the_worker_always_passes_a_bounded_limit(patched, monkeypatch):
    """An unbounded sweep is a full table scan plus one Stripe read per row.

    The limit is not optional and not left to the service's default: the worker
    states it explicitly, so the batch size visible in the worker's own
    configuration is the batch size that runs.
    """
    recorder, _ = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")

    pulse_worker.run_reservation_sweep_if_due({})
    limit = recorder.calls[0]["limit"]

    assert isinstance(limit, int) and 0 < limit <= 500

    monkeypatch.setenv(sweeper.BATCH_LIMIT_ENV_VAR, "10")
    pulse_worker.run_reservation_sweep_if_due({})
    assert recorder.calls[1]["limit"] == 10


# --------------------------------------------------------------------------
# 15-17 — the worker owns scheduling; the service owns decisions
# --------------------------------------------------------------------------

def _worker_tree() -> ast.Module:
    with open(WORKER_PATH, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _executable_strings(tree: ast.Module) -> str:
    """Every string the module can execute, with docstrings removed.

    Docstrings are excluded because this file's own prose names the things it
    forbids. A guard that cannot tell a prohibition from a violation fails on
    its own documentation and gets deleted, which is worse than having no guard.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        first = body[0] if body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
    return "\n".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ).lower()


def test_15_the_worker_contains_no_settlement_logic_of_its_own():
    """No SQL, no Stripe interpretation, no release reasons in the worker.

    Stages 5-6 of the reservation mission collapsed several private copies of
    "release the stock and mark the transaction" into one shared path, because
    every copy was a place where the two halves could drift apart and oversell
    a paid order. A scheduler is exactly where the next copy would appear.
    """
    literals = _executable_strings(_worker_tree())

    for forbidden in ("update marketplace_listings", "update seller_transactions",
                      "insert into marketplace_", "delete from marketplace_",
                      "reservation_expired", "payment_intent", "succeeded",
                      "requires_action", "stripe.paymentintent"):
        assert forbidden not in literals, (
            f"pulse_worker.py contains {forbidden!r} — settlement logic belongs "
            "in the service, not in the scheduler"
        )

    # The scheduler issues no SQL at all, which is a stronger and much harder
    # property to evade than "does not contain this exact table name". Checking
    # for bare verbs also survives the split-literal dodge — a table name
    # assembled from ``"marketplace_" + "listings"`` defeats the checks above,
    # but the statement still needs the verb, and the verb has nowhere to hide.
    for verb in ("update ", "insert into", "delete from", "select ",
                 "alter table", "drop table"):
        assert verb not in literals, (
            f"pulse_worker.py contains the SQL fragment {verb!r} — the worker "
            "owns scheduling and must never write a statement of its own"
        )


#: Names that only exist to perform settlement. Any of them appearing in the
#: worker means the scheduler has grown a second opinion about reservations.
FORBIDDEN_SETTLEMENT_NAMES = (
    "release_inventory_reservation", "capture_inventory_reservation",
    "settle_failed_transactions", "note_reservation_deferral",
    "decide_from_status", "decide_for_reservation",
)


def test_16_the_worker_calls_only_the_sweep_entry_point():
    """The worker may schedule the sweep; it may not reach past it.

    Calling ``release_inventory_reservation`` or ``settle_failed_transactions``
    directly from here would bypass the sweep's own decision table — including
    the Stripe check that stands between an expired timer and a paid order.

    Both call shapes are collected. Reading only ``node.func.attr`` would see
    ``cart.settle_failed_transactions()`` but be blind to the bare-name call
    that a ``from ... import settle_failed_transactions`` makes possible, which
    is the more likely shape for someone reaching for the function directly.
    """
    called = set()
    for node in ast.walk(_worker_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            called.add(node.func.id)

    assert "run_reservation_expiry_sweep" in called
    for forbidden in FORBIDDEN_SETTLEMENT_NAMES:
        assert forbidden not in called, (
            f"pulse_worker.py calls {forbidden}() directly — the worker owns "
            "scheduling only"
        )


def test_17_the_worker_imports_no_settlement_surface_but_the_sweeper():
    """One seam, so there is one place to audit.

    Importing the reconciler or the cart routes into the worker would not be a
    bug today, but it is the first step of every drift: the import lands first,
    the direct call follows in a later change.

    Scoped to ``marketplace_`` rather than to the substring ``reservation``,
    because ``services.marketplace_cart_routes`` — which owns every write to a
    reservation row — contains no such substring and would otherwise be the one
    settlement module this test waved through. Imported *names* are checked as
    well as module names, so ``from ... import settle_failed_transactions``
    fails here rather than surviving to test 16.
    """
    modules, names = set(), set()
    for node in ast.walk(_worker_tree()):
        if isinstance(node, ast.ImportFrom):
            # Both spellings: ``from services import marketplace_x`` names the
            # module in the alias, while ``from services.marketplace_x import f``
            # names it in node.module. Recording the join covers both.
            modules.add(node.module or "")
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)

    marketplace_modules = {m for m in modules if "marketplace" in m or "reservation" in m}
    assert marketplace_modules == {"services.marketplace_reservation_sweeper"}, (
        "pulse_worker.py imports a marketplace module other than the sweeper"
    )

    for forbidden in FORBIDDEN_SETTLEMENT_NAMES:
        assert forbidden not in names, (
            f"pulse_worker.py imports {forbidden} — importing the settlement "
            "surface is the first half of calling it"
        )


# --------------------------------------------------------------------------
# 18-19 — the metrics an operator reads during the rollout
# --------------------------------------------------------------------------

def test_18_sweep_metrics_survive_the_cycles_between_sweeps(patched, monkeypatch):
    """``last_sweep_at`` must be readable at any moment, not 1 cycle in 15.

    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale rather than
    merging it, so reporting only the current cycle's sweep would blank these
    fields on the fourteen cycles in between. An operator checking during the
    dry-run window would then see an empty heartbeat, which reads exactly like
    a sweep that never ran — the one thing the rollout needs to distinguish.
    """
    recorder, _ = patched()
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "300")

    state: dict = {}
    pulse_worker.run_reservation_sweep_if_due(state)

    for _ in range(14):  # cycles where the sweep declines to run
        pulse_worker.run_reservation_sweep_if_due(state)
        meta = pulse_worker.sweep_heartbeat_metadata(state)
        assert meta["reservation_sweep_enabled"] is True
        assert meta["last_sweep_at"], "last_sweep_at was blanked between sweeps"
        assert meta["last_sweep_status"] == "ok"
        assert meta["last_sweep_dry_run"] is True

    assert len(recorder.calls) == 1


def test_19_a_failed_sweep_is_distinguishable_from_a_healthy_one(
        patched, monkeypatch):
    """Silence, health and failure must be three different readings.

    Asserting only that ``last_sweep_status`` exists and equals ``"error"``
    after a failure would pass against a field hardcoded to ``"error"``. The
    healthy and failing cases are therefore compared against each other in one
    test, which is the property an operator actually depends on: that the value
    discriminates.
    """
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")

    healthy_state: dict = {}
    patched()
    pulse_worker.run_reservation_sweep_if_due(healthy_state)
    healthy = pulse_worker.sweep_heartbeat_metadata(healthy_state)

    failing_state: dict = {}
    patched(raises=RuntimeError("boom"))
    pulse_worker.run_reservation_sweep_if_due(failing_state)
    failing = pulse_worker.sweep_heartbeat_metadata(failing_state)

    assert healthy["last_sweep_status"] == "ok"
    assert failing["last_sweep_status"] == "error"

    # A failure still reports every numeric field, so a dashboard reading them
    # does not have to special-case the shape — and reports them as zero rather
    # than carrying stale counts forward from the last successful sweep.
    for field in ("last_sweep_candidates", "last_sweep_released",
                  "last_sweep_deferred", "last_sweep_failed",
                  "last_sweep_duration_ms", "last_sweep_needs_attention"):
        assert field in failing
        assert failing[field] == 0

    # Silence is the third reading: nothing has run yet.
    assert "last_sweep_at" not in pulse_worker.sweep_heartbeat_metadata({})


def test_20_the_heartbeat_metadata_is_an_allowlist_not_a_passthrough(
        patched, monkeypatch):
    """Whatever the service returns, only known-safe fields reach the heartbeat.

    Heartbeat metadata is persisted and rendered to admins. Asserting merely
    that no secret appears would be near-vacuous, since the metadata builder
    reads no environment variables — it could not leak a key if it tried. The
    real property is the direction of the flow: the builder projects a fixed
    set of fields out of the sweep summary rather than splatting the summary
    in, so a field added to the service later cannot silently become an admin
    -visible field. The sweep summary here carries a buyer email, a card
    fingerprint and a live-looking key; none may survive the projection.
    """
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_do_not_leak_me")

    recorder, _ = patched()
    leaky = dict(recorder(None, dry_run=True, limit=50))
    leaky.update({
        "buyer_email": "someone@example.com",
        "card_fingerprint": "fp_abc123",
        "provider_key": "sk_live_should_never_appear",
        "payment_intent_id": "pi_3Nxxxxx",
    })
    monkeypatch.setattr(sweeper, "run_reservation_expiry_sweep",
                        lambda cur, **kw: leaky)

    state: dict = {}
    pulse_worker.run_reservation_sweep_if_due(state)
    meta = pulse_worker.sweep_heartbeat_metadata(state)
    blob = repr(meta).lower()

    for secret in ("sk_test", "sk_live", "do_not_leak_me", "whsec_", "bearer ",
                   "someone@example.com", "fp_abc123", "pi_3nxxxxx"):
        assert secret not in blob, f"{secret!r} reached the heartbeat metadata"

    assert all(key.startswith(("last_sweep_", "reservation_sweep_")) for key in meta), (
        f"unexpected heartbeat key: {sorted(meta)}"
    )


# --------------------------------------------------------------------------
# 21-23 — the failure paths around the connection, not inside the sweep
# --------------------------------------------------------------------------

def test_21_a_database_that_will_not_open_is_contained(monkeypatch):
    """``bot.db()`` raising must be a sweep incident, not a worker crash.

    Tests 08-09 cover the sweep *itself* raising. This is the earlier failure —
    the connection never exists — which takes a different path through the
    ``finally`` that closes it, and would be an ``UnboundLocalError`` or an
    ``AttributeError`` on ``None`` if that path were written carelessly.
    """
    def _refuse():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(pulse_worker.bot, "db", _refuse)
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_SECONDS_ENV_VAR, "300")

    state: dict = {}
    outcome = pulse_worker.run_reservation_sweep_if_due(state)  # must not raise

    assert outcome["status"] == "error"
    assert "unable to open database file" in outcome["error"]
    assert state["reservation_sweep_due_at"] > 0, "the deadline must still advance"
    assert pulse_worker.sweep_heartbeat_metadata(state)["last_sweep_status"] == "error"


def test_22_a_close_failure_after_a_mutating_sweep_keeps_its_counts(
        monkeypatch):
    """A broken teardown must not erase the number the rollout is gated on.

    ``released`` is what the dry-run-to-mutate decision reads. If a
    ``conn.close()`` failure — a dropped Postgres connection is the ordinary
    cause — collapsed a committed sweep to ``released: 0, status: error``, the
    heartbeat would under-report real mutation at exactly the moment an
    operator is deciding whether the sweep is behaving. The cycle is degraded,
    not empty, and must say so.
    """
    class _CloseFails(_FakeConn):
        def close(self):
            raise sqlite3.OperationalError("connection already closed")

    monkeypatch.setattr(pulse_worker.bot, "db", _CloseFails)
    monkeypatch.setattr(
        sweeper, "run_reservation_expiry_sweep",
        lambda cur, **kw: {"candidates": 4, "released": 3, "deferred": 1,
                           "failed": 0, "needs_attention": 0, "duration_ms": 12,
                           "would_release": 0, "dry_run": False},
    )
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "false")

    state: dict = {}
    outcome = pulse_worker.run_reservation_sweep_if_due(state)  # must not raise

    assert outcome["status"] == "degraded"
    assert outcome["released"] == 3, "a close failure erased the release count"
    assert "connection already closed" in outcome["error"]

    meta = pulse_worker.sweep_heartbeat_metadata(state)
    assert meta["last_sweep_status"] == "degraded"
    assert meta["last_sweep_released"] == 3
    assert meta["last_sweep_candidates"] == 4


def test_23_a_commit_failure_reports_nothing_released(monkeypatch):
    """The mirror of 22: uncommitted work must never be reported as done.

    Preserving counts through a teardown failure is only safe because the
    commit is what the preservation is conditioned on. If the commit is the
    thing that failed, the transaction rolled back, nothing was released, and
    reporting the service's optimistic summary would be a false record of
    mutation — the more dangerous direction of the same bug.
    """
    class _CommitFails(_FakeConn):
        def commit(self):
            raise sqlite3.OperationalError("deadlock detected")

    monkeypatch.setattr(pulse_worker.bot, "db", _CommitFails)
    monkeypatch.setattr(
        sweeper, "run_reservation_expiry_sweep",
        lambda cur, **kw: {"candidates": 4, "released": 3, "failed": 0,
                           "duration_ms": 12, "dry_run": False},
    )
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "false")

    state: dict = {}
    outcome = pulse_worker.run_reservation_sweep_if_due(state)

    assert outcome["status"] == "error"
    assert outcome.get("released", 0) == 0, (
        "a rolled-back sweep reported releases it did not make"
    )
    assert pulse_worker.sweep_heartbeat_metadata(state)["last_sweep_released"] == 0


# --------------------------------------------------------------------------
# Stage 176B — a schema block must reach the heartbeat as a schema block
# --------------------------------------------------------------------------

def test_24_a_degraded_sweep_status_survives_the_healthy_path(monkeypatch):
    """The sweep's own status must not be overwritten with an optimistic one.

    The healthy path builds ``{"status": "ok", **summary}``. The spread is what
    carries a degraded sweep through, and it only works because the summary now
    carries a status of its own — so the ordering is asserted rather than
    trusted. Reverse it and a worker whose database needs a migration reports a
    clean cycle indefinitely.
    """
    monkeypatch.setattr(pulse_worker.bot, "db", _FakeConn)
    monkeypatch.setattr(
        sweeper, "run_reservation_expiry_sweep",
        lambda cur, **kw: {"status": "degraded", "reason": "schema_missing",
                           "candidates": 0, "released": 0, "failed": 1,
                           "duration_ms": 3, "dry_run": True,
                           "schema_missing": ["expires_at"]},
    )
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "true")

    state: dict = {}
    outcome = pulse_worker.run_reservation_sweep_if_due(state)

    assert outcome["status"] == "degraded"
    assert pulse_worker.sweep_heartbeat_metadata(state)["last_sweep_status"] == "degraded"


def test_25_the_heartbeat_separates_a_migration_problem_from_a_bad_row(monkeypatch):
    """Both arrive as ``degraded`` with ``failed=1``. Only the reason differs.

    One needs an operator to touch the database; the other usually clears on
    the next interval. Collapsing them into one status is how a silent
    inventory leak reads as ordinary noise on a dashboard.
    """
    monkeypatch.setattr(pulse_worker.bot, "db", _FakeConn)
    monkeypatch.setenv(pulse_worker.SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(pulse_worker.SWEEP_DRY_RUN_ENV_VAR, "true")

    def _heartbeat_for(summary):
        monkeypatch.setattr(sweeper, "run_reservation_expiry_sweep",
                            lambda cur, **kw: summary)
        state: dict = {}
        pulse_worker.run_reservation_sweep_if_due(state)
        return pulse_worker.sweep_heartbeat_metadata(state)

    blocked = _heartbeat_for({"status": "degraded", "reason": "schema_missing",
                              "candidates": 0, "released": 0, "failed": 1,
                              "duration_ms": 3, "dry_run": True})
    bad_row = _heartbeat_for({"status": "degraded", "reason": None,
                              "candidates": 2, "released": 1, "failed": 1,
                              "duration_ms": 9, "dry_run": True})
    healthy = _heartbeat_for({"status": "ok", "reason": None, "candidates": 0,
                              "released": 0, "failed": 0, "duration_ms": 2,
                              "dry_run": True})

    assert blocked["last_sweep_status"] == bad_row["last_sweep_status"] == "degraded"
    assert blocked["last_sweep_reason"] == "schema_missing"
    assert bad_row["last_sweep_reason"] is None
    assert healthy["last_sweep_reason"] is None
    # And the counts still say which of the two actually managed to look.
    assert blocked["last_sweep_candidates"] == 0
    assert bad_row["last_sweep_candidates"] == 2
