"""Cross-process rate counters — Stage 6.

The one property that matters
-----------------------------

Every claim this module makes reduces to a single question: **is the count
shared between processes?** Everything else in the file — modes, windows,
degradation — is refinement. If the count is not shared, this is
``security_guard.BUCKETS`` with a longer docstring, which is the exact failure
the module was written to fix.

A single-process test cannot answer that question. It cannot even fail on it: a
module-level dict looks perfectly shared inside one interpreter.
:func:`test_the_counter_is_shared_across_real_operating_system_processes`
therefore forks real subprocesses against a real database file, which is the
only arrangement in which "distributed" is falsifiable. Its paired test,
:func:`test_a_process_local_counter_would_fail_the_multiprocess_test`,
demonstrates on a stand-in that the multi-process harness genuinely detects
non-shared state — without it, a green result would prove only that the harness
runs.

The other tests worth reading before changing anything here:

* :func:`test_off_costs_nothing` — the default must not touch the database.
* :func:`test_shadow_never_blocks_but_still_notices` and its enforce pair. A
  shadow mode that reported nothing would be indistinguishable from ``off``.
* :func:`test_degrades_to_process_memory_and_admits_it` — a database failure
  must not turn into an outage *or* into a silent open door.
* :func:`test_boundary_burst_is_not_double_the_limit` — the seam a plain fixed
  window leaves open.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap

import pytest

from services.sentinel import bootstrap, rate_limit, store

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def wired(monkeypatch):
    """Limiter in enforce mode against one real SQLite database."""
    db = sqlite3.connect(":memory:")
    store.ensure_schema(db)

    class Handle:
        """A connection the limiter may close as often as it likes."""

        def __init__(self, inner):
            self._inner = inner

        def cursor(self):
            return self._inner.cursor()

        def commit(self):
            return self._inner.commit()

        def close(self):
            pass

    opens = {"count": 0}

    def connect():
        opens["count"] += 1
        return Handle(db)

    monkeypatch.setattr(store.platform_db, "connect", connect)
    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "enforce")

    bootstrap.reset_for_tests()
    bootstrap.ensure_schema(db, force=True)
    rate_limit.reset_for_tests()

    yield {"db": db, "opens": opens}

    rate_limit.reset_for_tests()
    bootstrap.reset_for_tests()
    db.close()


def rows(db):
    cur = db.cursor()
    cur.execute("SELECT scope, subject, window_start, hits "
                "FROM sentinel_rate_counters ORDER BY id")
    return cur.fetchall()


# =============================================================================
# The property that distinguishes this module from what already exists
# =============================================================================

_CHILD = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {root!r})
    os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
    os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "shadow"
    from services.sentinel import bootstrap, rate_limit
    bootstrap.ensure_schema(force=True)
    last = None
    for _ in range({n}):
        last = rate_limit.check("probe", "subject-a", limit=10_000,
                                window_seconds=3600, now={now!r})
    print(int(last.count), int(last.distributed), sep=",")
    """
)


def _run_child(db_path, n, now):
    return subprocess.run(
        [sys.executable, "-c",
         _CHILD.format(root=REPO_ROOT, db=db_path, n=n, now=now)],
        capture_output=True, text=True, timeout=180,
    )


def test_the_counter_is_shared_across_real_operating_system_processes():
    """The whole point of Stage 6, and the only test that can prove it.

    Four separate interpreters — the same arrangement gunicorn creates with
    ``--workers 4`` — each count 25 actions against the same subject. If the
    counter is shared, the last process sees a number near 100. If it is process
    memory, every process sees 25 and production's limits are silently
    quadrupled, which is the bug.

    Limits are set absurdly high and the mode is ``shadow`` so the children
    count without any of them short-circuiting on the local lower bound; this
    test is about the arithmetic of sharing, not about blocking.
    """
    handle, db_path = tempfile.mkstemp(suffix=".db", prefix="sentinel_rl_")
    os.close(handle)
    try:
        now = 1_800_000_000.0
        results = [_run_child(db_path, 25, now) for _ in range(4)]
        for r in results:
            assert r.returncode == 0, f"child failed: {r.stderr[-2000:]}"

        counts = [int(r.stdout.strip().split(",")[0]) for r in results]
        distributed = [int(r.stdout.strip().split(",")[1]) for r in results]

        assert all(distributed), "a child answered from process memory"
        # Children run sequentially here, so the counts must be exactly
        # cumulative. Any process-local implementation yields [25, 25, 25, 25].
        assert counts == [25, 50, 75, 100], counts

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT hits FROM sentinel_rate_counters "
                        "WHERE scope = 'probe' AND subject = 'subject-a'")
            assert [r[0] for r in cur.fetchall()] == [100]
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_concurrent_processes_do_not_lose_increments_to_a_race():
    """Sharing is necessary but not sufficient — the increment must be atomic.

    The test above runs its children one after another, so a naive
    ``SELECT hits`` / ``UPDATE hits = ?`` implementation would pass it. This one
    starts four processes at once against the same row. Under read-modify-write
    they interleave and increments are silently lost, which in production means
    an attacker gets more attempts than the limit says — the failure is on the
    permissive side, and invisible.

    ``ON CONFLICT (...) DO UPDATE SET hits = sentinel_rate_counters.hits + ?``
    is evaluated by the database against the current row, so the total is exact.
    """
    handle, db_path = tempfile.mkstemp(suffix=".db", prefix="sentinel_rlc_")
    os.close(handle)
    try:
        now = 1_800_000_000.0
        per_child, children = 60, 4
        procs = [
            subprocess.Popen(
                [sys.executable, "-c",
                 _CHILD.format(root=REPO_ROOT, db=db_path, n=per_child, now=now)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(children)
        ]
        outs = []
        for p in procs:
            out, err = p.communicate(timeout=300)
            assert p.returncode == 0, f"child failed: {err[-2000:]}"
            outs.append(out)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT hits FROM sentinel_rate_counters "
                        "WHERE scope = 'probe' AND subject = 'subject-a'")
            total = cur.fetchone()[0]
        finally:
            conn.close()

        assert total == per_child * children, (
            f"lost {per_child * children - total} increments to a race")
    finally:
        os.unlink(db_path)


def test_a_process_local_counter_would_fail_the_multiprocess_test():
    """Anti-vacuity partner for the test above.

    A green multi-process test proves sharing only if the harness could have
    caught the absence of it. This runs the same four-process shape against a
    deliberately process-local counter and asserts the harness reports the
    telltale ``[25, 25, 25, 25]`` — so the assertion above is known to be
    load-bearing rather than merely satisfied.
    """
    child = textwrap.dedent(
        """
        _BUCKET = {}
        for _ in range(25):
            _BUCKET["k"] = _BUCKET.get("k", 0) + 1
        print(_BUCKET["k"], 1, sep=",")
        """
    )
    counts = []
    for _ in range(4):
        r = subprocess.run([sys.executable, "-c", child],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        counts.append(int(r.stdout.strip().split(",")[0]))
    assert counts == [25, 25, 25, 25]
    assert counts != [25, 50, 75, 100], (
        "the two implementations are indistinguishable — the real test proves nothing")


# =============================================================================
# Modes
# =============================================================================


def test_off_costs_nothing(wired, monkeypatch):
    """Default configuration must be free: no connection, no row, no state."""
    monkeypatch.delenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", raising=False)
    assert rate_limit.mode() == "off"
    for _ in range(50):
        decision = rate_limit.check("/forgot-password", "ip:abc",
                                    limit=6, window_seconds=300)
        assert decision.allowed is True
        assert decision.reason == "mode:off"
    assert wired["opens"]["count"] == 0
    assert rows(wired["db"]) == []
    assert rate_limit.stats()["checks"] == 0


def test_an_unrecognised_mode_is_off_not_enforce(wired, monkeypatch):
    """A typo in a Railway variable must not start rejecting production
    traffic on a client that cannot be updated (Hard Rule #3)."""
    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "enfroce")
    assert rate_limit.mode() == "off"
    assert rate_limit.check("/signup", "ip:abc", limit=8,
                            window_seconds=300).reason == "mode:off"


def test_the_emergency_switch_stops_the_limiter(wired, monkeypatch):
    monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
    assert rate_limit.mode() == "off"
    assert rate_limit.check("/signup", "ip:abc", limit=8,
                            window_seconds=300).allowed is True
    assert wired["opens"]["count"] == 0


def test_shadow_never_blocks_but_still_notices(wired, monkeypatch, caplog):
    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "shadow")
    decisions = [rate_limit.check("probe", "ip:abc", limit=3, window_seconds=600,
                                  now=1_800_000_000.0)
                 for _ in range(6)]
    assert all(d.allowed for d in decisions), "shadow mode changed a response"
    assert [d.limited for d in decisions] == [False, False, False, True, True, True]
    assert all(d.enforced is False for d in decisions)

    stats = rate_limit.stats()
    assert stats["shadow_limited"] == 3
    assert stats["enforced_blocks"] == 0
    assert stats["limited"] == 3


def test_enforce_blocks_where_shadow_only_watched(wired):
    """The pair that gives the shadow test meaning: identical input, and the
    only difference is the mode."""
    decisions = [rate_limit.check("probe", "ip:abc", limit=3, window_seconds=600,
                                  now=1_800_000_000.0)
                 for _ in range(6)]
    assert [d.allowed for d in decisions] == [True, True, True, False, False, False]
    assert rate_limit.stats()["enforced_blocks"] == 3
    assert rate_limit.stats()["shadow_limited"] == 0


# =============================================================================
# Counting
# =============================================================================


def test_the_limit_is_the_number_allowed(wired):
    now = 1_800_000_000.0
    allowed = [rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                                now=now).allowed for _ in range(7)]
    assert allowed == [True] * 5 + [False, False]


def test_subjects_are_counted_separately(wired):
    now = 1_800_000_000.0
    for _ in range(5):
        rate_limit.check("probe", "ip:a", limit=5, window_seconds=600, now=now)
    fresh = rate_limit.check("probe", "ip:b", limit=5, window_seconds=600, now=now)
    assert fresh.allowed is True
    assert fresh.count == 1


def test_scopes_are_counted_separately(wired):
    now = 1_800_000_000.0
    for _ in range(5):
        rate_limit.check("scope-a", "ip:a", limit=5, window_seconds=600, now=now)
    fresh = rate_limit.check("scope-b", "ip:a", limit=5, window_seconds=600, now=now)
    assert fresh.allowed is True


def test_a_later_window_starts_clean(wired):
    now = 1_800_000_000.0
    for _ in range(5):
        rate_limit.check("probe", "ip:x", limit=5, window_seconds=600, now=now)
    assert rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                            now=now).allowed is False
    # Two full windows later: the previous window carries no weight at all.
    later = now + 1200
    assert rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                            now=later).allowed is True


def test_boundary_burst_is_not_double_the_limit(wired):
    """The seam a plain fixed window leaves open.

    Spend the whole limit in the last moment of one window, then step just over
    the boundary. A fixed-window counter resets to zero and cheerfully permits
    the limit again — 2x across a millisecond. The weighted window carries the
    previous count forward, so the very next request is still refused.
    """
    window = 600
    end_of_window = 1_800_000_000.0 + window - 0.001
    for _ in range(5):
        rate_limit.check("probe", "ip:x", limit=5, window_seconds=window,
                         now=end_of_window)

    just_after = 1_800_000_000.0 + window + 0.001
    rate_limit.reset_for_tests()  # forget the process-local short circuit
    decision = rate_limit.check("probe", "ip:x", limit=5, window_seconds=window,
                                now=just_after)
    assert decision.distributed is True
    assert decision.allowed is False, (
        "a fixed window would have allowed a second full burst here")


def test_the_carried_weight_decays_rather_than_blocking_forever(wired):
    """The pair for the test above: carrying the previous window forward must
    not become a permanent penalty, or one burst locks a subject out for good."""
    window = 600
    first = 1_800_000_000.0
    for _ in range(5):
        rate_limit.check("probe", "ip:x", limit=5, window_seconds=window, now=first)

    rate_limit.reset_for_tests()
    # Most of the way through the next window: the previous window's weight has
    # decayed to near nothing.
    late = first + window + (window * 0.95)
    decision = rate_limit.check("probe", "ip:x", limit=5, window_seconds=window,
                                now=late)
    assert decision.allowed is True
    assert decision.count < 1.5


def test_cost_lets_one_action_count_as_several(wired):
    now = 1_800_000_000.0
    assert rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                            cost=5, now=now).allowed is True
    assert rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                            now=now).allowed is False


def test_the_limit_must_be_supplied_and_cannot_be_defaulted(wired):
    """Every caller states its own limit; the module has no fallback to reach for.

    This is the shape that keeps the policy in one place. If ``check()`` could be
    called without a limit it would need a default, a default is a policy, and a
    policy here is a second one — ``bot.basic_abuse_guard`` already holds the
    deployed table for these paths.
    """
    with pytest.raises(TypeError):
        rate_limit.check("probe", "ip:x")
    with pytest.raises(TypeError):
        rate_limit.check("probe", "ip:x", limit=5)


def test_this_module_grows_no_policy_table_of_its_own():
    """Regression guard against re-adding the registry that was removed.

    The first draft of this module carried a ``RULES`` table naming password
    reset, registration and so on with limits of its own. ``basic_abuse_guard``
    in ``bot.py`` already carries exactly those paths with exactly those limits,
    deployed. Two tables for one route is the state where they eventually
    disagree and nobody can say which number production applied — the duplicate
    security policy Hard Rule #6 forbids. A future edit that reintroduces one
    should fail here rather than in production.
    """
    for banned in ("RULES", "Rule", "LIMITS", "DEFAULT_LIMIT", "DEFAULT_WINDOW"):
        assert not hasattr(rate_limit, banned), (
            f"rate_limit.{banned} is a rate-limit policy inside the counting "
            f"module; the policy belongs to the caller")


# =============================================================================
# The local short circuit
# =============================================================================


def test_a_flood_stops_querying_the_database(wired):
    """Under sustained abuse the limiter must get *cheaper*, not more expensive.

    Once this worker alone has counted past the limit, its own count is already
    a proof that the global count is over — no round trip can change the answer.
    """
    now = 1_800_000_000.0
    for _ in range(200):
        rate_limit.check("probe", "ip:flood", limit=5, window_seconds=600, now=now)

    stats = rate_limit.stats()
    assert stats["local_short_circuits"] > 150
    assert stats["db_reads"] <= 10, (
        "a flood was still opening a connection per request")
    assert wired["opens"]["count"] <= 10


def test_the_short_circuit_never_answers_below_the_limit(wired):
    """The dangerous version of the optimisation above: if the local count were
    trusted while *under* the limit, four workers would each allow the full
    limit and this would be process memory again."""
    now = 1_800_000_000.0
    for _ in range(5):
        decision = rate_limit.check("probe", "ip:x", limit=5, window_seconds=600,
                                    now=now)
        assert decision.distributed is True, (
            "an under-limit decision was made without consulting the shared store")


# =============================================================================
# Failure
# =============================================================================


def test_degrades_to_process_memory_and_admits_it(wired, monkeypatch):
    """A database failure must not become an outage, and must not become a
    silent open door either. It becomes today's behaviour, labelled."""

    def broken():
        raise sqlite3.OperationalError("no such host")

    monkeypatch.setattr(store.platform_db, "connect", broken)

    now = 1_800_000_000.0
    decisions = [rate_limit.check("probe", "ip:x", limit=3, window_seconds=600,
                                  now=now) for _ in range(5)]

    assert [d.allowed for d in decisions] == [True, True, True, False, False], (
        "degraded mode stopped limiting entirely")
    assert all(d.distributed is False for d in decisions)
    assert decisions[0].reason == "degraded_to_process_memory"
    assert "OperationalError" in decisions[0].degraded_error

    # The counts the degraded decisions *report* have to be the real local
    # counts, and this assertion is doing more work than it looks like it is.
    # Mutation testing showed that the `allowed` sequence above is satisfied
    # entirely by the local short circuit on calls 4 and 5 — a degraded branch
    # that reported `count = 0` would still produce exactly that sequence,
    # because a degraded call can never be the one that blocks (it is only
    # reached when the local count is under the limit). So without this, the
    # degradation path's arithmetic was asserted by nothing and the test name
    # claimed more than the test proved.
    assert [d.count for d in decisions[:3]] == [1.0, 2.0, 3.0]

    # Only the first three degrade. Once the local count has passed the limit,
    # the short circuit answers without attempting the database at all — so a
    # broken database gets *quieter* under abuse rather than retrying per
    # request. The two paths are counted separately because they mean different
    # things: `degraded` is "we could not see the other workers", while
    # `local_short_circuits` is "we did not need to".
    stats = rate_limit.stats()
    assert stats["degraded"] == 3
    assert stats["local_short_circuits"] == 2
    assert [d.reason for d in decisions[3:]] == ["local_lower_bound_exceeds_limit"] * 2
    assert stats["distributed_ok"] is False, (
        "health reported a distributed limiter while it was running on memory")


def test_a_broken_limiter_cannot_break_the_endpoint(wired, monkeypatch):
    """A limiter that can raise into the route it protects has made the system
    less available, not more secure."""

    def explode(*a, **kw):
        raise RuntimeError("limiter is on fire")

    monkeypatch.setattr(rate_limit, "_bump_local", explode)
    decision = rate_limit.check("probe", "ip:x", limit=3, window_seconds=600)
    assert decision.allowed is True
    assert decision.reason == "internal_error"


def test_a_failing_prune_does_not_fail_the_decision(wired, monkeypatch):
    now = 1_800_000_000.0
    rate_limit.check("probe", "ip:x", limit=5, window_seconds=600, now=now)

    real_connect = store.platform_db.connect
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] > 1:
            raise sqlite3.OperationalError("prune blew up")
        return real_connect()

    rate_limit.reset_for_tests()
    monkeypatch.setattr(store.platform_db, "connect", flaky)
    decision = rate_limit.check("probe", "ip:y", limit=5, window_seconds=600,
                                now=now + 10_000)
    assert decision.allowed is True
    assert decision.distributed is True


# =============================================================================
# Housekeeping — the flaw this must not repeat
# =============================================================================


def test_expired_windows_are_deleted(wired):
    """``security_guard.BUCKETS`` is never evicted and grows without bound — a
    slow memory-exhaustion vector. Repeating that in a table would make it a
    disk-exhaustion vector, which is worse because it survives a restart."""
    window = 600
    base = 1_800_000_000.0
    for step in range(8):
        rate_limit.reset_for_tests()
        rate_limit.check("probe", f"ip:{step}", limit=100,
                         window_seconds=window, now=base + step * window)

    remaining = rows(wired["db"])
    assert len(remaining) < 8, "nothing was ever pruned"
    cutoff = int(base + 7 * window) - window * rate_limit.PRUNE_RETENTION_WINDOWS
    assert all(r[2] >= cutoff for r in remaining)
    assert rate_limit.stats()["prunes"] > 0


def test_pruning_is_rate_limited_itself(wired):
    """Pruning on every check would double the connection cost of the control."""
    now = 1_800_000_000.0
    for i in range(30):
        rate_limit.check("probe", f"ip:{i}", limit=100, window_seconds=600, now=now)
    assert rate_limit.stats()["prunes"] == 1


def test_the_local_map_is_bounded(wired, monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_LOCAL_KEYS", 32)
    now = 1_800_000_000.0
    for i in range(200):
        rate_limit.check("probe", f"ip:{i}", limit=100, window_seconds=600, now=now)
    assert rate_limit.stats()["local_keys"] <= 32


# =============================================================================
# Shape of the decision
# =============================================================================


def test_retry_after_points_at_the_end_of_the_window(wired):
    window = 600
    start = 1_800_000_000.0
    decision = rate_limit.check("probe", "ip:x", limit=1, window_seconds=window,
                                now=start + 100)
    assert decision.retry_after == window - 100


def test_retry_after_is_never_zero_or_negative(wired):
    window = 600
    start = 1_800_000_000.0
    decision = rate_limit.check("probe", "ip:x", limit=1, window_seconds=window,
                                now=start + window - 0.0001)
    assert decision.retry_after >= 1


def test_the_decision_distinguishes_its_three_kinds_of_yes(wired, monkeypatch):
    """`allowed=True` alone is ambiguous, and the ambiguity is the whole risk:
    under the limit, only observing, and the database is down all look identical
    to a caller that reads one boolean."""
    now = 1_800_000_000.0

    under = rate_limit.check("probe", "ip:a", limit=5, window_seconds=600, now=now)
    assert (under.allowed, under.limited, under.enforced, under.distributed) == \
        (True, False, True, True)

    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "shadow")
    rate_limit.reset_for_tests()
    for _ in range(6):
        watching = rate_limit.check("probe", "ip:b", limit=5, window_seconds=600,
                                    now=now)
    assert (watching.allowed, watching.limited, watching.enforced) == (True, True, False)

    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "enforce")
    monkeypatch.setattr(store.platform_db, "connect",
                        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("down")))
    rate_limit.reset_for_tests()
    blind = rate_limit.check("probe", "ip:c", limit=5, window_seconds=600, now=now)
    assert (blind.allowed, blind.distributed) == (True, False)


def test_stats_reports_the_mode_it_is_actually_in(wired, monkeypatch):
    monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "shadow")
    assert rate_limit.stats()["mode"] == "shadow"
    assert rate_limit.stats()["enabled"] is True
    monkeypatch.delenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", raising=False)
    assert rate_limit.stats()["mode"] == "off"
    assert rate_limit.stats()["enabled"] is False


# =============================================================================
# Storage
# =============================================================================


def test_one_row_per_scope_subject_window(wired):
    now = 1_800_000_000.0
    for _ in range(10):
        rate_limit.check("probe", "ip:x", limit=100, window_seconds=600, now=now)
    stored = rows(wired["db"])
    assert len(stored) == 1
    assert stored[0][3] == 10


def test_the_unique_index_exists(wired):
    """``ON CONFLICT (scope, subject, window_start) DO UPDATE`` is not merely a
    nicety — without the matching unique index it is a syntax error on
    PostgreSQL, and the atomic increment silently becomes four workers racing."""
    cur = wired["db"].cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='sentinel_rate_counters'")
    names = {r[0] for r in cur.fetchall()}
    assert "ux_sentinel_rate_counters_window" in names


def test_no_raw_subject_is_invented_by_this_module(wired):
    """The module must not hash or transform subjects: Sentinel's identifiers
    have to join against the ones the platform already stores, or they
    correlate with nothing (Hard Rule #6)."""
    now = 1_800_000_000.0
    rate_limit.check("probe", "ip:deadbeef", limit=5, window_seconds=600, now=now)
    assert rows(wired["db"])[0][1] == "ip:deadbeef"
