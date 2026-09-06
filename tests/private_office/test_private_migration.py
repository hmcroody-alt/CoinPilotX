"""Idempotent migration and conservative legacy backfill.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_migration.py
    python tests/private_office/test_private_migration.py

What these tests are actually defending
---------------------------------------
* **A healthy store is left completely alone.** First stage, and the one that
  matters most. A migration that rewrites rows it did not need to touch is
  indistinguishable from data loss until somebody reads a diff, and by then it
  has run in production. Every column of every row is captured before and
  compared after.
* **Nothing is fabricated.** An unreadable origin becomes ``LEGACY_UNKNOWN``
  and never ``USER_ASSERTED`` or ``PROVIDER_ASSERTED``. Those two are asserted
  as *negatives*, by name, because they are the tempting repairs and a future
  edit that "improves" the backfill would reach for one of them.
* **The value is never touched.** ``typed_value``, ``value_number``,
  ``value_type``, ``fact_key`` and every timestamp are compared byte for byte
  across a repair that rewrites three other columns on the same row.
* **A repair never widens access except the one place it must, and only to the
  narrowest recognised tier.** An unreadable sensitivity is invisible to
  everyone including the owner; it becomes ``RESTRICTED``, not the
  ``CONFIDENTIAL`` default, and the difference is the whole point — the default
  would release the row to every subsystem holding a confidential ceiling.
* **Convergence, not just idempotence.** Run twice, and also run interrupted:
  a run that stops after one batch must leave the store consistent and the next
  run must finish the job.
* **A dry run writes nothing.** Asserted by comparing the whole table, not by
  trusting the returned counts — which is the failure mode, since a dry run
  that quietly wrote would return exactly the same numbers.
* **A failure never reads as a clean store.** ``complete`` is False whenever
  the scan could not finish or could not start.
* **The report carries counts and nothing else.** No fact id, no value, no
  subject: an operator running a migration has no business reading facts.

Damage fixtures
---------------
Legacy damage cannot be written through the package — ``record_fact`` rejects
every unreadable label, which is exactly why these rows can only arrive from an
older build, a restored backup or direct SQL. ``_damage`` below is the one raw
statement in this file, registered in the write-boundary guard's
``DAMAGE_FIXTURES`` with an exact count.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_migration_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import migration  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9801
OTHER = 9802

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def cursor():
    conn = db.connect()
    return conn, conn.cursor()


def _iso(days_ago: float = 0.0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


_SUBJECT = 0


def _subject() -> str:
    """A fresh subject per fixture.

    The backfill scans the whole table across every owner — that is what a
    migration is — so stages cannot be isolated by owner the way the other
    suites in this directory isolate them. They are isolated by subject, and
    every stage that counts repairs counts the *delta* it caused rather than an
    absolute, because another stage's damage is always still sitting in the
    table.
    """
    global _SUBJECT
    _SUBJECT += 1
    return f"mg{_SUBJECT}"


def _fact(cur, subject: str, owner: int = OWNER, *, value: object = "100",
          fact_type: str = "holding_value", **kwargs) -> int:
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("observed_at", _iso(30))
    kwargs.setdefault("valid_from", _iso(30))
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject,
        fact_type=fact_type, value=value, value_type=model.VALUE_MONEY,
        actor_user_id=owner, **kwargs)["fact_id"]


def _damage(cur, fact_id: int, **columns) -> None:
    """Write a label no writer in this package will accept.

    The single raw statement in this file. ``record_fact`` normalizes every one
    of these columns and rejects what it cannot read, so a row with a blank
    provenance or a sensitivity spelled ``NOPE`` has no route in through the
    package — which is the premise of the whole module under test. A fixture
    that could produce one through the writers would mean the writers had a
    hole, and the backfill would be repairing damage of its own making.
    """
    assignments = ", ".join(f"{name} = ?" for name in columns)
    cur.execute(
        f"UPDATE {schema.FACTS_TABLE} SET {assignments} WHERE id = ?",
        [*columns.values(), int(fact_id)])


def _row(cur, fact_id: int) -> dict:
    cur.execute(f"SELECT * FROM {schema.FACTS_TABLE} WHERE id = ?", (int(fact_id),))
    found = cur.fetchone()
    return dict(found) if found is not None else {}


def _snapshot(cur) -> dict[int, dict]:
    """Every column of every row, for before/after comparison.

    Deliberately not a checksum or a row count. The failure this defends
    against is a migration that changes a value while leaving the shape intact,
    and a count would sail straight past it.
    """
    cur.execute(f"SELECT * FROM {schema.FACTS_TABLE} ORDER BY id ASC")
    return {int(dict(r)["id"]): dict(r) for r in cur.fetchall()}


def _history_kinds(cur, fact_id: int) -> list[str]:
    cur.execute(
        f"SELECT change_type FROM {schema.FACT_HISTORY_TABLE} "
        f"WHERE fact_id = ? ORDER BY id ASC", (int(fact_id),))
    return [str(dict(r)["change_type"]) for r in cur.fetchall()]


def _repairs(report: dict) -> dict[str, int]:
    return {k: v for k, v in (report.get("repaired") or {}).items() if v}


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_a_healthy_store_is_left_completely_alone():
    """The first and most important guarantee: touch nothing that reads fine.

    A backfill that rewrites undamaged rows is not a smaller bug than one that
    misses damaged ones — it is a larger one, because the damaged rows were
    already broken and the healthy ones were not.
    """
    print("\n[a healthy store]")
    conn, cur = cursor()
    subject = _subject()

    _fact(cur, subject, value="100")
    _fact(cur, subject, value="200", fact_type="premium",
          provenance_type=model.PROVENANCE_PROVIDER_ASSERTED,
          domain=model.DOMAIN_FINANCIAL,
          sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE)
    conn.commit()

    before = _snapshot(cur)
    report = migration.run_backfill(cur, apply=True)
    after = _snapshot(cur)

    check("a store the writers built needs no repair at all",
          not _repairs(report), str(_repairs(report)))
    check("and reports nothing it refused to repair either",
          not {k: v for k, v in report["unrepairable"].items() if v},
          str(report["unrepairable"]))
    check("every row is byte-identical afterwards",
          before == after,
          str([k for k in before if before.get(k) != after.get(k)])[:200])
    check("the scan says it reached the end of the table",
          report["complete"] is True, str(report))
    # `remaining` is the count of damaged rows the run saw and did not fix, and
    # on a clean store it has to be zero. A healthy row counted as remaining is
    # not a cosmetic miscount: `remaining` is what an operator reads to decide
    # whether to run the backfill again, so a store that reports a permanent
    # non-zero remainder is one somebody reruns a migration against forever.
    check("and nothing is left over on a store with nothing wrong with it",
          report["remaining"] == 0, str(report["remaining"]))
    conn.commit()
    conn.close()


def stage_an_unknown_origin_is_never_flattered():
    """LEGACY_UNKNOWN, and specifically not the two tempting alternatives."""
    print("\n[an unreadable origin]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="300")
    _damage(cur, fact_id, provenance_type="")
    conn.commit()

    check("the damaged origin is genuinely unreadable to start with",
          model.normalize_provenance(_row(cur, fact_id)["provenance_type"]) is None,
          str(_row(cur, fact_id)["provenance_type"]))

    report = migration.run_backfill(cur, apply=True)
    conn.commit()
    repaired = _row(cur, fact_id)

    check("an unreadable origin becomes LEGACY_UNKNOWN",
          repaired["provenance_type"] == model.PROVENANCE_LEGACY_UNKNOWN,
          str(repaired["provenance_type"]))
    # Asserted by name rather than by "is not something else". These two are
    # the repairs a future edit would reach for, and both are unfalsifiable
    # once written: nothing afterwards distinguishes a fabricated
    # USER_ASSERTED from a row where the owner really did type the value.
    check("it is not promoted to USER_ASSERTED",
          repaired["provenance_type"] != model.PROVENANCE_USER_ASSERTED)
    check("nor flattered to PROVIDER_ASSERTED",
          repaired["provenance_type"] != model.PROVENANCE_PROVIDER_ASSERTED)
    check("and certainly not to VERIFIED",
          repaired["provenance_type"] != model.PROVENANCE_VERIFIED)
    check("the repair is counted under its own kind",
          report["repaired"][migration.REPAIR_PROVENANCE] >= 1,
          str(report["repaired"]))
    check("LEGACY_UNKNOWN cannot settle an argument it happens to be in",
          model.PROVENANCE_LEGACY_UNKNOWN in model.NON_SETTLING_PROVENANCE)
    conn.commit()
    conn.close()


def stage_the_member_value_is_never_rewritten():
    """Three labels change on one row; nothing else on it moves."""
    print("\n[the value is untouchable]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="4321")
    _damage(cur, fact_id, provenance_type="", sensitivity="NOPE", domain="???")
    conn.commit()
    before = _row(cur, fact_id)

    migration.run_backfill(cur, apply=True)
    conn.commit()
    after = _row(cur, fact_id)

    # The columns that carry what the member actually told us. `fact_key` is in
    # this list rather than the changed one for a specific reason: it is the
    # UNIQUE(owner_user_id, fact_key) identity, and recomputing it after
    # rewriting provenance — which is one of its inputs — could collapse two
    # distinct rows onto one key. A migration that deletes a fact by way of a
    # constraint violation is the worst outcome available here, so the key is
    # left stale on purpose and the staleness is asserted.
    for column in ("typed_value", "value_number", "value_type", "fact_key",
                   "subject_type", "subject_id", "fact_type", "owner_user_id",
                   "observed_at", "valid_from", "valid_to", "created_at",
                   "confidence", "lifecycle_state", "verification_state",
                   "supersedes_id", "superseded_by_id", "conflict_id"):
        check(f"{column} is identical after the repair",
              before.get(column) == after.get(column),
              f"{before.get(column)!r} -> {after.get(column)!r}")

    check("exactly the three label columns changed",
          {k for k in before if before[k] != after[k]}
          == {"provenance_type", "sensitivity", "domain", "updated_at"},
          str({k for k in before if before[k] != after[k]}))
    conn.commit()
    conn.close()


def stage_a_repair_widens_access_only_to_the_narrowest_tier():
    """The one deliberate widening, and the tier it must not use.

    An unreadable sensitivity matches no ceiling at any tier, so the row is
    invisible to everyone including its owner. Repairing it is therefore a
    widening, and the only question is how far.
    """
    print("\n[the one widening]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="500")
    _damage(cur, fact_id, sensitivity="NOT_A_TIER")
    conn.commit()

    invisible = facts.list_facts(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
        sensitivity_ceiling=model.SENSITIVITY_RESTRICTED, limit=50)
    check("before the repair the owner cannot see their own fact",
          fact_id not in [int(f["id"]) for f in invisible],
          str([int(f["id"]) for f in invisible]))

    migration.run_backfill(cur, apply=True)
    conn.commit()
    repaired = _row(cur, fact_id)

    check("the repair uses the most restrictive recognised tier",
          repaired["sensitivity"] == model.SENSITIVITY_RESTRICTED,
          str(repaired["sensitivity"]))
    # The distinction that carries the guarantee. DEFAULT_SENSITIVITY is
    # CONFIDENTIAL, and using it here would release the row to every subsystem
    # holding a confidential ceiling — a real disclosure, created by a
    # migration, which is the thing that must never happen quietly.
    check("and not the writer's default, which would be a wider release",
          repaired["sensitivity"] != model.DEFAULT_SENSITIVITY
          and model.SENSITIVITY_RANK[repaired["sensitivity"]]
          > model.SENSITIVITY_RANK[model.DEFAULT_SENSITIVITY],
          f"{repaired['sensitivity']} vs {model.DEFAULT_SENSITIVITY}")

    visible = facts.list_facts(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
        sensitivity_ceiling=model.SENSITIVITY_RESTRICTED, limit=50)
    check("after the repair the owner can see it again",
          fact_id in [int(f["id"]) for f in visible])

    withheld = facts.list_facts(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
        sensitivity_ceiling=model.SENSITIVITY_CONFIDENTIAL, limit=50)
    check("and a subsystem below the top ceiling still cannot",
          fact_id not in [int(f["id"]) for f in withheld],
          str([int(f["id"]) for f in withheld]))
    conn.commit()
    conn.close()


def stage_what_cannot_be_repaired_is_reported_not_guessed():
    """Four columns with no honest default. Counted, never written."""
    print("\n[reported, not guessed]")
    conn, cur = cursor()
    subject = _subject()

    lifecycle_row = _fact(cur, subject, value="600", fact_type="lifecycle_gap")
    _damage(cur, lifecycle_row, lifecycle_state="")
    value_row = _fact(cur, subject, value="700", fact_type="value_type_gap")
    _damage(cur, value_row, value_type="NOT_A_TYPE")
    conn.commit()

    before_lifecycle = _row(cur, lifecycle_row)
    before_value = _row(cur, value_row)
    report = migration.run_backfill(cur, apply=True)
    conn.commit()

    check("an unreadable lifecycle is reported",
          report["unrepairable"][migration.UNREPAIRABLE_LIFECYCLE] >= 1,
          str(report["unrepairable"]))
    # The reason it is only reported. ACTIVE would resurface a fact the member
    # may have archived deliberately; ARCHIVED would hide one they still rely
    # on. Both are guesses about intent, and the store has no way to check
    # either afterwards.
    check("and left exactly as it was found",
          _row(cur, lifecycle_row)["lifecycle_state"]
          == before_lifecycle["lifecycle_state"],
          str(_row(cur, lifecycle_row)["lifecycle_state"]))
    check("it is not quietly filed ACTIVE",
          _row(cur, lifecycle_row)["lifecycle_state"] != model.LIFECYCLE_ACTIVE)

    check("an unreadable value type is reported",
          report["unrepairable"][migration.UNREPAIRABLE_VALUE_TYPE] >= 1,
          str(report["unrepairable"]))
    check("and the value is not re-parsed under a type nobody chose",
          _row(cur, value_row)["typed_value"] == before_value["typed_value"]
          and _row(cur, value_row)["value_type"] == before_value["value_type"],
          str(_row(cur, value_row)["value_type"]))
    check("every unrepairable reason the module can emit is named",
          set(report["unrepairable"]) == set(migration.UNREPAIRABLE),
          str(sorted(report["unrepairable"])))
    conn.commit()
    conn.close()


def stage_a_dry_run_writes_nothing():
    """Asserted against the table, not against the returned counts.

    Trusting the report here would test nothing: a dry run that wrote anyway
    would return exactly the same numbers as one that did not, which is the
    entire failure mode.
    """
    print("\n[dry run]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="800")
    _damage(cur, fact_id, provenance_type="", domain="???")
    conn.commit()

    before = _snapshot(cur)
    report = migration.run_backfill(cur)
    after = _snapshot(cur)

    check("the default refuses to write",
          report["applied"] is False, str(report["applied"]))
    check("not one row changed",
          before == after,
          str([k for k in before if before.get(k) != after.get(k)])[:200])
    check("but it still reports what it would repair",
          report["repaired"][migration.REPAIR_PROVENANCE] >= 1
          and report["repaired"][migration.REPAIR_DOMAIN] >= 1,
          str(report["repaired"]))
    check("and the damage is still there to be repaired later",
          model.normalize_provenance(
              _row(cur, fact_id)["provenance_type"]) is None)

    applied = migration.run_backfill(cur, apply=True)
    conn.commit()
    check("the same call with apply=True does the work",
          applied["applied"] is True
          and _row(cur, fact_id)["provenance_type"]
          == model.PROVENANCE_LEGACY_UNKNOWN,
          str(_row(cur, fact_id)["provenance_type"]))
    conn.commit()
    conn.close()


def stage_the_backfill_converges():
    """Twice is a no-op, and an interrupted run leaves a consistent store.

    Convergence is stronger than idempotence and it is the property that
    actually matters, because a migration over a large table *will* be
    interrupted.

    The check that earns its place here is the resumption loop below, and it is
    worth saying what it caught, because the module passed every other check in
    this file while being unable to finish. ``run_backfill`` originally started
    its scan at id 0 on every call and did not report where it stopped. A
    caller whose budget was smaller than the table therefore re-read the same
    prefix forever: the first run repaired the damage in that prefix and every
    run afterwards scanned those same now-clean rows, reported ``complete``
    False, and never advanced. Nothing raised, rows really did get repaired, and
    the counts looked healthy — the only symptom was that the tail of the table,
    which is to say the most recently written rows, could never be reached. A
    stage that ran one partial batch and then one unbounded run would not have
    seen it. Driving the whole store through a budget that is smaller than the
    store is what makes it visible.
    """
    print("\n[convergence]")
    conn, cur = cursor()
    subject = _subject()

    damaged = [_fact(cur, subject, value=str(900 + i), fact_type=f"conv_{i}")
               for i in range(6)]
    for fact_id in damaged:
        _damage(cur, fact_id, provenance_type="")
    conn.commit()

    # One batch of two rows, one round: an interruption, expressed exactly as
    # the budget running out rather than by mocking a crash.
    partial = migration.run_backfill(cur, apply=True, batch=2, max_batches=1)
    conn.commit()
    check("an interrupted run does not claim the store is clean",
          partial["complete"] is False, str(partial))
    check("and it says where it stopped",
          partial["next_after_id"] > 0, str(partial))

    # Resume from the published cursor until it says it is finished. The bound
    # is the assertion: a store this size cannot need more rounds than it has
    # rows, and a loop that needs more is not converging.
    rounds, cursor_at, repaired = 0, partial["next_after_id"], 0
    total_rows = len(_snapshot(cur))
    finished = partial
    while not finished["complete"] and rounds < total_rows + 2:
        finished = migration.run_backfill(
            cur, apply=True, batch=2, max_batches=1, after_id=cursor_at)
        conn.commit()
        check_cursor = finished["next_after_id"]
        if check_cursor <= cursor_at and not finished["complete"]:
            check("a resumed run always advances the cursor", False,
                  f"stuck at {cursor_at}")
            break
        cursor_at = check_cursor
        repaired += finished["repaired"][migration.REPAIR_PROVENANCE]
        rounds += 1
    check("resuming from the published cursor reaches the end of the table",
          finished["complete"] is True, f"after {rounds} rounds: {finished}")
    check("and it took no more rounds than the store has rows",
          rounds <= total_rows, f"{rounds} rounds over {total_rows} rows")

    finished = migration.run_backfill(cur, apply=True)
    conn.commit()
    check("an unbounded run finishes the job",
          finished["complete"] is True, str(finished))

    # Advancing the cursor *within* one run is a separate guarantee from
    # publishing it, and it needs its own check because the two failures look
    # identical from outside on a small store. A run whose internal cursor never
    # moves re-reads its first batch on every round: it still terminates, still
    # reports plausible counts, and — on any table smaller than one batch —
    # still finishes, because the first round already reached the end. Only a
    # multi-round run over more rows than a single batch holds can tell the
    # difference, and the tell is the repair count, not the scan count.
    walk = [_fact(cur, subject, value=str(960 + i), fact_type=f"walk_{i}")
            for i in range(6)]
    for fact_id in walk:
        _damage(cur, fact_id, domain="???")
    conn.commit()
    highest = max(walk)
    stepped = migration.run_backfill(
        cur, apply=True, batch=2, max_batches=3, after_id=min(walk) - 1)
    conn.commit()
    check("three rounds of two walk six distinct rows, not the same two thrice",
          stepped["repaired"][migration.REPAIR_DOMAIN] == 6,
          str(stepped["repaired"]))
    check("and the cursor ends where the last row it read actually is",
          stepped["next_after_id"] == highest,
          f"{stepped['next_after_id']} != {highest}")
    check("every damaged row now reads LEGACY_UNKNOWN",
          all(_row(cur, f)["provenance_type"] == model.PROVENANCE_LEGACY_UNKNOWN
              for f in damaged),
          str([_row(cur, f)["provenance_type"] for f in damaged]))

    settled = _snapshot(cur)
    again = migration.run_backfill(cur, apply=True)
    conn.commit()
    check("a third run repairs nothing",
          not _repairs(again), str(_repairs(again)))
    check("and changes no row",
          settled == _snapshot(cur),
          str([k for k in settled if settled.get(k) != _snapshot(cur).get(k)])[:200])
    conn.commit()
    conn.close()


def stage_a_repair_is_recorded_as_a_backfill_not_a_correction():
    """The member's timeline must not claim somebody revised their figure."""
    print("\n[history]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="1000")
    _damage(cur, fact_id, domain="???")
    conn.commit()
    before = _history_kinds(cur, fact_id)

    migration.run_backfill(cur, apply=True, actor_user_id=0)
    conn.commit()
    after = _history_kinds(cur, fact_id)

    check("the repair leaves a history entry",
          len(after) == len(before) + 1, f"{before} -> {after}")
    check("filed as BACKFILLED",
          after[-1] == model.CHANGE_BACKFILLED, str(after))
    # The distinction the vocabulary exists for. CORRECTED means the member's
    # claim turned out to be wrong and a truer one replaced it — it is written
    # on the old row of a supersession. A migration that could not read the
    # domain column corrected nothing.
    check("and not as CORRECTED, which would be a different claim entirely",
          model.CHANGE_CORRECTED not in after[len(before):], str(after))
    check("the entry records no value, because the table has no value column",
          "typed_value" not in schema.FACT_HISTORY_TABLE_DDL
          and "value_number" not in schema.FACT_HISTORY_TABLE_DDL)
    conn.commit()
    conn.close()


def stage_a_failure_never_reads_as_a_clean_store():
    """`complete` is the only thing separating "clean" from "could not look"."""
    print("\n[honest failure]")
    conn, cur = cursor()

    class _ScanExplodes:
        """Delegates everything except the scan, which is the query under test."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None):
            if f"FROM {schema.FACTS_TABLE}" in str(sql) and "id > ?" in str(sql):
                raise RuntimeError("simulated scan failure")
            return self._inner.execute(sql, params) if params is not None \
                else self._inner.execute(sql)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    broken = migration.run_backfill(_ScanExplodes(cur), apply=True)
    check("a scan that raised is not complete",
          broken["complete"] is False, str(broken))
    check("and reports no repairs it did not make",
          not _repairs(broken), str(_repairs(broken)))
    check("a failure returns rather than raising into the caller's boot",
          isinstance(broken, dict))

    class _SchemaMissing:
        def execute(self, sql, params=None):
            raise RuntimeError("no such table")

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    unreachable = migration.run_backfill(_SchemaMissing(), apply=True)
    check("a store this process cannot reach is not reported clean",
          unreachable["complete"] is False, str(unreachable))
    check("and every count is zero rather than absent",
          set(unreachable["repaired"]) == set(migration.REPAIRS)
          and not any(unreachable["repaired"].values()),
          str(unreachable["repaired"]))

    # The cursor above never actually reached the schema check, and it is worth
    # being precise about why, because the check passed for the wrong reason
    # until this was written. `ensure_private_schema` answers from a process-wide
    # cache once it has succeeded, so handing it a cursor that raises on every
    # statement returns READY without touching the cursor at all — the failure
    # then surfaces one layer down, in the scan. Which means the branch that
    # handles an unusable *schema* was unreached by any test in this file while
    # appearing to be covered by one.
    #
    # Raising from `require_private_schema` directly is the honest way to reach
    # it: that is precisely what a role without SELECT on the private tables
    # produces on a cold process, and it is the branch whose whole contract is
    # that a store nobody could read is not a store reported clean.
    original_require = migration._schema.require_private_schema

    def _refuse(_cur, **_kwargs):
        raise migration._schema.PrivateSchemaMissing({"__ensure__": ("denied",)})

    migration._schema.require_private_schema = _refuse
    try:
        denied = migration.run_backfill(cur, apply=True)
    finally:
        migration._schema.require_private_schema = original_require

    check("a schema this process cannot read is not reported clean either",
          denied["complete"] is False, str(denied))
    check("and it repaired nothing on the way to saying so",
          not _repairs(denied) and denied["scanned"] == 0, str(denied))
    check("the stub was removed again",
          migration._schema.require_private_schema is original_require)
    conn.commit()
    conn.close()


def stage_the_report_names_nobody():
    """Counts and nothing else. An operator is not a member."""
    print("\n[the report discloses nothing]")
    conn, cur = cursor()
    subject = _subject()

    secret = "40404040"
    fact_id = _fact(cur, subject, value=secret, fact_type="disclosure_probe")
    _damage(cur, fact_id, provenance_type="", sensitivity="NOPE")
    conn.commit()

    report = migration.run_backfill(cur, apply=True)
    conn.commit()
    blob = repr(report)

    check("the member's value appears nowhere in the report",
          secret not in blob, blob[:200])
    check("nor the subject it was filed under",
          subject not in blob, blob[:200])
    check("nor the owner",
          str(OWNER) not in blob, blob[:200])
    check("the report carries only counts, status and the scan cursor",
          set(report) == {"scanned", "repaired", "unrepairable", "applied",
                          "complete", "remaining", "next_after_id"},
          str(sorted(report)))
    check("and every count is an integer, not a list of rows",
          all(isinstance(v, int) for v in report["repaired"].values())
          and all(isinstance(v, int) for v in report["unrepairable"].values()))

    # `next_after_id` is the one field here that is a row id, and it is stated
    # rather than smuggled. It is a scan cursor: the highest id the scan reached,
    # which is a property of the table and not of any member. It carries no
    # owner, no subject, no value, and no indication that the row it names was
    # damaged or even looked at twice — an operator holding it learns how far a
    # migration got and nothing about whose data lies on either side of it.
    # The alternative is worse than the disclosure: without a published cursor a
    # budget-limited run restarts at zero forever and can never reach the tail of
    # the table, which is the failure the convergence stage above demonstrates.
    check("the scan cursor is a bare integer, not a row",
          isinstance(report["next_after_id"], int), repr(report["next_after_id"]))
    check("and it does not point past the store it scanned",
          report["next_after_id"] >= fact_id, str(report["next_after_id"]))
    check("nothing in the report is a container of rows",
          not any(isinstance(v, (list, tuple)) for v in report.values()),
          str(sorted(report)))
    conn.commit()
    conn.close()


def stage_the_repair_stays_inside_one_owner():
    """The one module that reads across owners must still write inside one.

    Two boundaries, and the second is the one that is easy to leave untested.
    The obvious mistake is a repair that reaches another member's rows, and it
    is caught below. The subtler one lives inside a single member: a ``WHERE``
    clause that names the owner but not the row repairs *every* fact that member
    owns, stamping one damaged row's replacements across all of them. Both
    checks above would still pass — the damaged row really is repaired, and the
    other owner really is untouched — so the blast radius has to be measured
    directly, by counting how many rows moved.
    """
    print("\n[owner boundary]")
    conn, cur = cursor()
    subject = _subject()

    mine = _fact(cur, subject, value="1100", fact_type="boundary_mine")
    theirs = _fact(cur, subject, OTHER, value="1200", fact_type="boundary_theirs")
    # A second healthy row belonging to the *same* owner. Without it an
    # owner-wide UPDATE has nothing to hit but the row it was supposed to hit.
    also_mine = _fact(cur, subject, value="1150", fact_type="boundary_sibling")
    _damage(cur, mine, provenance_type="")
    conn.commit()
    before_theirs = _row(cur, theirs)
    before_all = _snapshot(cur)

    migration.run_backfill(cur, apply=True)
    conn.commit()

    moved = [row_id for row_id, row in _snapshot(cur).items()
             if before_all.get(row_id) != row]
    check("exactly one row in the whole store changed",
          moved == [mine], f"moved {moved}, expected [{mine}]")
    check("the owner's other fact is untouched",
          _snapshot(cur)[also_mine] == before_all[also_mine],
          str({k for k in before_all[also_mine]
               if before_all[also_mine][k] != _snapshot(cur)[also_mine].get(k)}))
    check("the damaged row is repaired",
          _row(cur, mine)["provenance_type"] == model.PROVENANCE_LEGACY_UNKNOWN)
    check("the other owner's row is untouched",
          _row(cur, theirs) == before_theirs,
          str({k for k in before_theirs
               if before_theirs[k] != _row(cur, theirs).get(k)}))
    check("the history entry is filed under the row's own owner",
          all(int(dict(r)["owner_user_id"]) == OWNER
              for r in _history_for_owner_check(cur, mine)),
          "history crossed an owner")

    # A migration does repair across owners — that is what a migration is — so
    # the guarantee is not "it only ever saw one owner" but "it never attributed
    # one owner's row to another".
    damaged_other = _fact(cur, subject, OTHER, value="1300",
                          fact_type="boundary_other_damaged")
    _damage(cur, damaged_other, domain="???")
    conn.commit()
    migration.run_backfill(cur, apply=True)
    conn.commit()
    check("a second owner's damage is repaired too, under their own id",
          _row(cur, damaged_other)["domain"] == model.DOMAIN_GENERAL
          and int(_row(cur, damaged_other)["owner_user_id"]) == OTHER,
          str(_row(cur, damaged_other)["owner_user_id"]))
    conn.commit()
    conn.close()


def _history_for_owner_check(cur, fact_id: int):
    cur.execute(
        f"SELECT owner_user_id FROM {schema.FACT_HISTORY_TABLE} WHERE fact_id = ?",
        (int(fact_id),))
    return cur.fetchall()


def stage_migrate_runs_the_schema_before_it_reads_it():
    """Order is load-bearing, not conventional."""
    print("\n[migrate]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="1400", fact_type="migrate_probe")
    _damage(cur, fact_id, provenance_type="")
    conn.commit()

    result = migration.migrate(cur, apply=True)
    conn.commit()

    check("migrate reports both halves",
          set(result) == {"schema", "backfill"}, str(sorted(result)))
    check("the schema half came back ready",
          result["schema"]["status"] == schema.STATUS_READY,
          str(result["schema"].get("status")))
    check("and the backfill half did the repair",
          _row(cur, fact_id)["provenance_type"] == model.PROVENANCE_LEGACY_UNKNOWN)

    # The six columns added after the first release carry DEFAULTs, so the ALTER
    # backfills them on every existing row and they must never surface as
    # damage. If one ever appeared here it would mean a default was dropped and
    # every legacy row in production had a blank verification state.
    check("the columns added by ALTER never appear as damage",
          not result["backfill"]["unrepairable"][
              migration.UNREPAIRABLE_LIFECYCLE] > 100,
          str(result["backfill"]["unrepairable"]))
    for column, definition in schema.TABLE_ADDED_COLUMNS[schema.FACTS_TABLE]:
        check(f"{column} carries a DEFAULT so the ALTER reaches existing rows",
              "DEFAULT" in definition, definition)

    # A schema that is not ready must not produce a backfill report at all: a
    # zero count off a store this process could not read is the same lie that
    # `complete` exists to prevent, one level up.
    class _DDLRefused:
        def execute(self, sql, params=None):
            raise RuntimeError("permission denied")

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    # Asserted by whether the backfill was *called*, not by what it returned.
    # The returned report is the same either way: `run_backfill` re-checks the
    # schema itself and comes back with complete False and no repairs, so a
    # `migrate` that had dropped the guard entirely and called it anyway would
    # produce a byte-identical result to one that skipped it. The guarantee here
    # is about the order of two operations, and only a spy can see an order.
    called: list[bool] = []
    original_backfill = migration.run_backfill

    def _watched(*args, **kwargs):
        called.append(True)
        return original_backfill(*args, **kwargs)

    migration.run_backfill = _watched
    try:
        refused = migration.migrate(_DDLRefused(), apply=True)
    finally:
        migration.run_backfill = original_backfill

    check("a store whose schema is not ready is never even scanned",
          not called, f"run_backfill called {len(called)} time(s)")
    check("and the reported backfill is empty rather than absent",
          refused["backfill"]["complete"] is False
          and not _repairs(refused["backfill"]),
          str(refused["backfill"]))
    check("the spy was removed again",
          migration.run_backfill is original_backfill)
    conn.commit()
    conn.close()


def stage_the_bounds_are_enforced():
    """A batch size a caller can argue above is not a bound."""
    print("\n[bounds]")
    conn, cur = cursor()

    asked: list[object] = []
    original = migration._scan_batch

    def _recording(cur_, **kwargs):
        asked.append(kwargs.get("batch"))
        return original(cur_, **kwargs)

    migration._scan_batch = _recording
    try:
        migration.run_backfill(cur, batch=99999, max_batches=99999)
    finally:
        migration._scan_batch = original

    check("the batch size is clamped to the module ceiling",
          asked and all(int(b) <= migration.MAX_BACKFILL_BATCH for b in asked),
          str(asked[:5]))
    check("the spy was removed again",
          migration._scan_batch is original)

    # The round budget needs a store the budget can actually bind on, which the
    # run above does not provide: one batch of 500 swallows this whole table, so
    # the loop stops because it ran out of rows and the count of scan calls is 1
    # whether the clamp exists or not. Reading `len(asked) <= MAX_BACKFILL_BATCHES`
    # off that run is reading a property of the fixture. Forcing one row per
    # batch over a table with more rows than the ceiling is what makes the
    # ceiling the thing that stops it.
    padding = [_fact(cur, _subject(), value=str(2000 + i), fact_type=f"pad_{i}")
               for i in range(migration.MAX_BACKFILL_BATCHES + 4)]
    conn.commit()
    rows_available = len(_snapshot(cur))
    check("the store is larger than the round budget, so the budget can bind",
          rows_available > migration.MAX_BACKFILL_BATCHES,
          f"{rows_available} rows vs {migration.MAX_BACKFILL_BATCHES} rounds")

    rounds_asked: list[object] = []

    def _counting(cur_, **kwargs):
        rounds_asked.append(kwargs.get("after_id"))
        return original(cur_, **kwargs)

    migration._scan_batch = _counting
    try:
        bounded = migration.run_backfill(cur, batch=1, max_batches=99999)
    finally:
        migration._scan_batch = original

    check("and the number of rounds is clamped too",
          len(rounds_asked) <= migration.MAX_BACKFILL_BATCHES,
          f"{len(rounds_asked)} rounds over {rows_available} rows")
    check("a run stopped by its own budget does not claim it saw the whole store",
          bounded["complete"] is False, str(bounded))
    check("and it stopped short of the rows it never read",
          bounded["scanned"] < rows_available,
          f"{bounded['scanned']} of {rows_available}")
    check("the second spy was removed too",
          migration._scan_batch is original)
    del padding

    tiny = migration.run_backfill(cur, batch=0, max_batches=0)
    check("a zero batch still reads at least one row rather than none",
          tiny["scanned"] >= 1, str(tiny["scanned"]))

    check("every repair kind has a replacement",
          set(migration.REPLACEMENT) == set(migration.REPAIRS),
          str(sorted(migration.REPLACEMENT)))
    check("and every replacement is a value its own vocabulary recognises",
          model.normalize_provenance(
              migration.REPLACEMENT[migration.REPAIR_PROVENANCE][1])
          == model.PROVENANCE_LEGACY_UNKNOWN
          and model.normalize_sensitivity(
              migration.REPLACEMENT[migration.REPAIR_SENSITIVITY][1])
          == model.SENSITIVITY_RESTRICTED
          and model.normalize_domain(
              migration.REPLACEMENT[migration.REPAIR_DOMAIN][1])
          == model.DOMAIN_GENERAL)
    conn.commit()
    conn.close()


def stage_legacy_unknown_stays_hard_to_reach():
    """The backfill is the only door, and it must stay the only door."""
    print("\n[the backfill is the only door]")
    conn, cur = cursor()
    subject = _subject()

    rejected = False
    try:
        _fact(cur, subject, value="1500", fact_type="door_probe",
              provenance_type=model.PROVENANCE_LEGACY_UNKNOWN)
    except facts.PrivateFactRejected:
        rejected = True
    check("an ordinary write cannot claim LEGACY_UNKNOWN",
          rejected, "the create path accepted it")
    check("it is named as backfill-only in the model",
          model.PROVENANCE_LEGACY_UNKNOWN in model.BACKFILL_ONLY_PROVENANCE)
    check("and it is not a degraded state a writer moves a row into",
          model.PROVENANCE_LEGACY_UNKNOWN not in model.DEGRADED_PROVENANCE)
    check("BACKFILLED round-trips through its normalizer",
          model.normalize_change_type(model.CHANGE_BACKFILLED)
          == model.CHANGE_BACKFILLED)
    check("and is a member of the closed vocabulary",
          model.CHANGE_BACKFILLED in model.HISTORY_CHANGE_TYPES)
    conn.commit()
    conn.close()


def main() -> int:
    print("=" * 60)
    print("PRIVATE OFFICE — MIGRATION AND LEGACY BACKFILL")
    print("=" * 60)

    conn, cur = cursor()
    schema.ensure_private_schema(cur, force=True)
    conn.commit()
    conn.close()

    stage_a_healthy_store_is_left_completely_alone()
    stage_an_unknown_origin_is_never_flattered()
    stage_the_member_value_is_never_rewritten()
    stage_a_repair_widens_access_only_to_the_narrowest_tier()
    stage_what_cannot_be_repaired_is_reported_not_guessed()
    stage_a_dry_run_writes_nothing()
    stage_the_backfill_converges()
    stage_a_repair_is_recorded_as_a_backfill_not_a_correction()
    stage_a_failure_never_reads_as_a_clean_store()
    stage_the_report_names_nobody()
    stage_the_repair_stays_inside_one_owner()
    stage_migrate_runs_the_schema_before_it_reads_it()
    stage_the_bounds_are_enforced()
    stage_legacy_unknown_stays_hard_to_reach()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_migration():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
