"""Structural integrity of the fact store.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_integrity.py
    python tests/private_office/test_private_integrity.py

What these tests are actually defending
---------------------------------------
* **A healthy store reports nothing.** This is the first stage and the most
  important one. Every other guarantee here is worthless if the sweep fires on
  ordinary data: a diagnostic with a non-zero baseline gets read once, dismissed
  as noise, and then the real fault arrives and lands in the same bucket. So the
  clean-store stage builds corrections, corroboration, disjoint validity periods
  and verified facts through the real writers, and requires *zero* findings.
* **Corroboration is not duplication.** Two sources agreeing about the same
  value is the store working. A sweep that reported it as a duplicate would be
  advising the member to delete an independent second source, so the negative
  case is asserted at least as hard as the positive one.
* **Could-not-look is not clean, and it is not broken either.** A pointer past
  the scan window, a citation nobody can resolve, an unreadable evidence table:
  each has a third answer, and each is asserted to produce that answer rather
  than a finding or a silence.
* **An unreadable evidence table is not mass corruption.** The single worst
  failure this module could have is reporting every verified fact in a store as
  unsupported because one query failed. It gets its own stage.
* **A cycle is detected rather than survived.** ``facts.fact_chain`` returns a
  truncated chain on a loop, which is right for a request and useless as a
  detector — the stage proves the helper cannot see it and the sweep can.
* **The sweep decides nothing.** Every function is exercised and the table row
  counts must be identical afterwards.

Damage fixtures
---------------
Most of these states cannot be written through the package — that is what makes
them integrity faults rather than review reasons. ``_damage`` below is the one
raw statement in this file, registered in the write-boundary guard's
``DAMAGE_FIXTURES`` with an exact count, and every stage that needs a broken
store goes through it.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_integrity_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import integrity  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9901
OTHER = 9902

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

    Every stage sweeps the whole owner's store, so two stages sharing a subject
    would let one stage's damage surface as another's finding. Isolation is
    per-subject rather than per-owner because owner isolation is itself under
    test and must not be spent on hygiene.
    """
    global _SUBJECT
    _SUBJECT += 1
    return f"ig{_SUBJECT}"


def _fact(cur, subject: str, owner: int = OWNER, *, value: object = "100",
          fact_type: str = "holding_value", **kwargs) -> int:
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("observed_at", _iso(30))
    kwargs.setdefault("valid_from", _iso(30))
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject,
        fact_type=fact_type, value=value, value_type=model.VALUE_MONEY,
        actor_user_id=owner, **kwargs)["fact_id"]


def _document(cur, owner: int, title: str) -> int:
    """A real row in the real documents table, built by the real DDL.

    ``force=True`` because the module memoises "schema is present" in a
    process-global that an earlier test module has already primed against a
    different database. The plain call is a no-op here and the insert would hit
    a table that was never created — a failure that appears only under the
    full-package run, which is the run that matters and not the one anybody
    iterates on.
    """
    documents.ensure_documents_schema(cur, force=True)
    stamp = _iso()
    cur.execute(
        f"""INSERT INTO {documents.DOCUMENTS_TABLE}
            (owner_user_id, title, lifecycle_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)""",
        (owner, title, documents.LIFECYCLE_ACTIVE, stamp, stamp))
    cur.execute(
        f"SELECT id FROM {documents.DOCUMENTS_TABLE} WHERE owner_user_id=?"
        " ORDER BY id DESC LIMIT 1", (owner,))
    return int(dict(cur.fetchone())["id"])


def _damage(cur, fact_id: int, **columns) -> None:
    """Put the store into a state no writer in this package can produce.

    The single raw write in this file, and deliberately generic so it stays
    single. Every fault below — a cycle, a dangling pointer, an ACTIVE row that
    already has a successor, a verified badge with nothing under it — is
    precisely a state the writers refuse to create, which is why they are
    integrity findings and not review reasons. There is no way to fabricate them
    through the package, and a fixture that could would mean the writer had a
    hole in it.
    """
    assignments = ", ".join(f"{name} = ?" for name in columns)
    cur.execute(
        f"UPDATE {schema.FACTS_TABLE} SET {assignments} WHERE id = ?",
        [*columns.values(), int(fact_id)])


def _kinds(report: dict) -> list[str]:
    return [f["kind"] for f in report.get("findings") or []]


def _for_fact(report: dict, fact_id: int) -> list[str]:
    return sorted(f["kind"] for f in (report.get("findings") or [])
                  if f.get("fact_id") == fact_id)


def _sweep(cur, owner: int = OWNER, **kwargs) -> dict:
    return integrity.check_integrity(cur, owner_user_id=owner, **kwargs)


def _row_counts(cur) -> dict[str, int]:
    counts = {}
    for table in (schema.FACTS_TABLE, schema.FACT_HISTORY_TABLE,
                  schema.FACT_EVIDENCE_TABLE, schema.FACT_CONFLICTS_TABLE):
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = int(dict(cur.fetchone())["n"])
    return counts


class _FailingCursor:
    """A cursor that fails one query shape, records the rest, and delegates.

    Delegating rather than mocking. The queries under inspection are the ones
    the real code issues against a real database, so the only thing substituted
    is the single failure — a fake cursor would let a degradation assertion pass
    against a query shape the database has never seen.

    It also records every statement it is handed, because one of the guarantees
    below is about SQL that was *not* issued: an anonymous caller must cause no
    query at all. That is not observable from a return value — a sweep with no
    owner returns an empty result whether it refused to look or looked and found
    nothing.
    """

    def __init__(self, inner, fail_on: str = "\0"):
        self._inner = inner
        self._fail_on = fail_on
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        if self._fail_on in str(sql):
            raise RuntimeError("simulated read failure")
        return self._inner.execute(sql, params) if params is not None \
            else self._inner.execute(sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
def stage_a_healthy_store_reports_nothing():
    """The baseline, and the stage the whole module depends on.

    Everything else here asserts that a fault is found. None of it is worth
    anything if ordinary data also produces findings: a diagnostic with a
    non-zero baseline is read once, filed as noise, and then the genuine fault
    arrives and lands in the same bucket as the noise.

    So this store is deliberately built out of the shapes most likely to be
    mistaken for damage — a real correction (which leaves a SUPERSEDED row with
    a successor and an ACTIVE row with a predecessor), two sources agreeing
    about one value, the same source describing two non-overlapping periods,
    and a verified fact with a live document behind it. All of it is written
    through the real writers, and none of it may produce a finding.
    """
    print("\n[a healthy store reports nothing]")
    conn, cur = cursor()
    subject = _subject()

    # A correction: the shape that most resembles every supersession finding.
    original = _fact(cur, subject, value="500", fact_type="healthy_corrected")
    facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=original, value="600",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(5), actor_user_id=OWNER)

    # Corroboration: one value, two independent origins.
    for source in ("statement", "registry"):
        _fact(cur, subject, value="900", fact_type="healthy_corroborated",
              provenance=facts.ProvenanceRef(
                  source_type="account", source_id=source,
                  observed_at=_iso(30)))

    # Two periods, one value, one source. Ordinary history, not two copies.
    _fact(cur, subject, value="42", fact_type="healthy_periods",
          valid_from=_iso(300), valid_to=_iso(200))
    _fact(cur, subject, value="42", fact_type="healthy_periods",
          valid_from=_iso(100), valid_to=_iso(50))

    # A correction that leaves the value standing and only moves the window.
    # This is the shape a duplicate check keys on almost exactly — same
    # subject, same type, same value, same source, overlapping periods — and
    # the only thing separating it from a genuine duplicate is that one of the
    # two rows is history. A check that looked at superseded rows would report
    # every restated fact in the store as a copy of itself.
    restated = _fact(cur, subject, value="77", fact_type="healthy_restated",
                     valid_from=_iso(30), valid_to=_iso(1))
    facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=restated, value="77",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(20), valid_to=_iso(1),
        actor_user_id=OWNER)

    # A verified fact with a document that is actually there.
    document = _document(cur, OWNER, "healthy deed")
    verified = _fact(cur, subject, value="7", fact_type="healthy_verified")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=verified,
                        source_ref=evidence.format_ref("document", document),
                        actor_user_id=OWNER)
    facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=verified,
        verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED,
        actor_user_id=OWNER)
    conn.commit()

    report = _sweep(cur)
    check("a store built entirely by the writers has no findings",
          report["counts"] == {}, str(report["counts"]))
    check("and the findings list is empty to match",
          report["findings"] == [], str(_kinds(report))[:120])
    check("nothing was left unchecked either",
          report["uncheckable"] == {}, str(report["uncheckable"]))
    check("the sweep covered the whole store",
          report["complete"] is True,
          f"scanned {report['scanned']} of {report['total']}")
    check("and says how much that was",
          report["scanned"] == report["total"] and report["total"] > 0,
          f"{report['scanned']} vs {report['total']}")


def stage_corroboration_is_never_reported_as_duplication():
    """Two sources agreeing is the store working, not a fault.

    The obvious implementation of "find duplicates" groups on subject, type and
    value and reports every group of two. It would fire on exactly this fixture,
    and acting on it means deleting an independent second source — the single
    most damaging thing a diagnostic in this package could recommend.

    The genuine duplicate is narrower: the *same* source asserting the *same*
    value over an *overlapping* period, twice. Both halves are asserted here,
    because a check that only proves it catches the duplicate has not shown it
    can tell the two apart.
    """
    print("\n[corroboration is not duplication]")
    conn, cur = cursor()
    subject = _subject()

    for source in ("bank", "broker"):
        _fact(cur, subject, value="1000", fact_type="dup_corroborated",
              valid_from=_iso(100), valid_to=_iso(10),
              provenance=facts.ProvenanceRef(
                  source_type="account", source_id=source,
                  observed_at=_iso(100)))

    same = facts.ProvenanceRef(source_type="account", source_id="bank",
                               observed_at=_iso(100))
    first = _fact(cur, subject, value="1000", fact_type="dup_real",
                  valid_from=_iso(100), valid_to=_iso(10), provenance=same)
    second = _fact(cur, subject, value="1000", fact_type="dup_real",
                   valid_from=_iso(90), valid_to=_iso(10), provenance=same)

    _fact(cur, subject, value="55", fact_type="dup_disjoint",
          valid_from=_iso(400), valid_to=_iso(300), provenance=same)
    _fact(cur, subject, value="55", fact_type="dup_disjoint",
          valid_from=_iso(200), valid_to=_iso(100), provenance=same)
    conn.commit()

    report = _sweep(cur)
    flagged = {f["fact_id"] for f in report["findings"]
               if f["kind"] == model.INTEGRITY_DUPLICATE_VALUE}
    check("the same source saying the same thing twice is a duplicate",
          second in flagged, str(sorted(flagged)))
    check("and the finding names the row it duplicates",
          any(f.get("detail", {}).get("duplicate_of") == first
              for f in report["findings"]
              if f["fact_id"] == second), str(report["findings"])[:160])
    check("the earlier of the pair is not itself reported",
          first not in flagged, str(sorted(flagged)))
    check("two sources agreeing are left alone",
          len(flagged) == 1, str(sorted(flagged)))
    check("and so are two non-overlapping periods",
          report["counts"].get(model.INTEGRITY_DUPLICATE_VALUE) == 1,
          str(report["counts"]))


def stage_an_active_row_with_a_successor_outranks_everything():
    """The invisible fault, and the reason it sorts first.

    A cycle produces a visibly truncated chain. A dangling pointer breaks a
    history view. Neither changes what the member is *told* the current value
    is. An ACTIVE row that already has a successor does exactly that: it passes
    the lifecycle predicate on every default read, feeds every projection, and
    carries nothing saying a correction exists. The member corrected the number
    and the store kept quoting the old one.
    """
    print("\n[active with a successor]")
    conn, cur = cursor()
    subject = _subject()

    original = _fact(cur, subject, value="500", fact_type="active_successor")
    facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=original, value="600",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(5), actor_user_id=OWNER)
    # The correction happened correctly and the lifecycle was later reverted —
    # a restored backup, or a well-meant repair script.
    _damage(cur, original, lifecycle_state=model.LIFECYCLE_ACTIVE)
    conn.commit()

    report = _sweep(cur)
    check("a corrected row still filed ACTIVE is reported",
          model.INTEGRITY_ACTIVE_WITH_SUCCESSOR in _for_fact(report, original),
          str(_for_fact(report, original)))
    check("it is the highest severity this module assigns",
          model.integrity_severity(model.INTEGRITY_ACTIVE_WITH_SUCCESSOR)
          == max(model.INTEGRITY_SEVERITY.values()),
          str(model.INTEGRITY_SEVERITY))
    check("so it sorts first even against a cycle",
          _kinds(report)[0] == model.INTEGRITY_ACTIVE_WITH_SUCCESSOR,
          str(_kinds(report))[:120])
    check("and names the successor so the fix is obvious",
          any(f["detail"].get("superseded_by_id", 0) > 0
              for f in report["findings"]
              if f["kind"] == model.INTEGRITY_ACTIVE_WITH_SUCCESSOR),
          str(report["findings"][:1])[:160])


def stage_a_cycle_is_detected_rather_than_survived():
    """``fact_chain`` survives a loop; this module has to see one.

    The helper is bounded and returns a *truncated* chain when it revisits an
    id — correct for a request, which must not hang, and useless as a detector,
    because a truncated chain and a legitimately long one are the same value.
    The stage proves both halves: the helper comes back with something
    plausible-looking, and the sweep names the loop.
    """
    print("\n[a cycle is detected]")
    conn, cur = cursor()
    subject = _subject()

    first = _fact(cur, subject, value="10", fact_type="cycle")
    second = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=first, value="20",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(5),
        actor_user_id=OWNER)["fact_id"]
    _damage(cur, second, superseded_by_id=first)
    conn.commit()

    chain = facts.fact_chain(cur, owner_user_id=OWNER, fact_id=first)
    check("the chain helper returns without hanging",
          isinstance(chain, list), str(chain))
    check("but reports the loop as an ordinary chain, seeing nothing wrong",
          len(chain) <= facts.MAX_CHAIN and first in chain, str(chain))

    report = _sweep(cur)
    check("the sweep names the first row as on a cycle",
          model.INTEGRITY_SUPERSESSION_CYCLE in _for_fact(report, first),
          str(_for_fact(report, first)))
    check("and the second, because both are on it",
          model.INTEGRITY_SUPERSESSION_CYCLE in _for_fact(report, second),
          str(_for_fact(report, second)))
    check("a cycle outranks every structural link finding",
          model.integrity_severity(model.INTEGRITY_SUPERSESSION_CYCLE)
          > model.integrity_severity(model.INTEGRITY_SUPERSESSION_DANGLING),
          "")


def stage_a_pointer_past_the_window_is_not_a_broken_one():
    """The distinction that makes DANGLING mean anything.

    From inside a bounded scan, a pointer naming a row outside the window looks
    exactly like a pointer naming a row that does not exist. Reporting the first
    as the second would tell every member with a large store that their history
    is corrupt, and the finding would be worthless within a day.

    So both cases are built and the sweep must separate them: a healthy chain
    read through a scan window of one, and a pointer to an id that genuinely is
    not there.
    """
    print("\n[past the window is not broken]")
    conn, cur = cursor()
    subject = _subject()

    head = _fact(cur, subject, value="1", fact_type="window_chain")
    facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=head, value="2",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(5), actor_user_id=OWNER)
    conn.commit()

    narrow = _sweep(cur, scan=1)
    check("a scan of one row admits it did not cover the store",
          narrow["complete"] is False,
          f"scanned {narrow['scanned']} of {narrow['total']}")
    check("and does not call the chain it half-saw broken",
          model.INTEGRITY_SUPERSESSION_DANGLING not in narrow["counts"],
          str(narrow["counts"]))

    orphan = _fact(cur, subject, value="3", fact_type="window_orphan")
    _damage(cur, orphan, superseded_by_id=987654321)
    conn.commit()

    report = _sweep(cur)
    check("a pointer to an id that is genuinely absent is reported",
          model.INTEGRITY_SUPERSESSION_DANGLING in _for_fact(report, orphan),
          str(_for_fact(report, orphan)))
    check("and the finding names both the pointer and its target",
          any(f["detail"].get("target_id") == 987654321
              and f["detail"].get("pointer") == "superseded_by_id"
              for f in report["findings"]
              if f["fact_id"] == orphan), str(report["findings"])[:200])


def stage_a_link_both_rows_disagree_about_is_reported():
    """A pointer is a claim made by two rows, and the two can disagree.

    Checking one direction accepts a chain that reads A→B walking forward and
    B→C walking back, which resolves to a different answer depending on which
    end the reader started from. That is worse than a plainly broken link: both
    walks succeed and return confident, different answers.
    """
    print("\n[the two ends disagree]")
    conn, cur = cursor()
    subject = _subject()

    first = _fact(cur, subject, value="1", fact_type="asym")
    second = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=first, value="2",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(5), valid_from=_iso(5),
        actor_user_id=OWNER)["fact_id"]
    third = _fact(cur, subject, value="3", fact_type="asym_other")
    # `second` now points back at `third` while `first` still points forward at
    # `second`. Neither pointer dangles; they simply do not agree.
    _damage(cur, second, supersedes_id=third)
    conn.commit()

    report = _sweep(cur)
    check("the disagreement is reported",
          model.INTEGRITY_SUPERSESSION_ASYMMETRIC in _for_fact(report, first),
          str(_for_fact(report, first)))
    check("and it carries what the other end actually says",
          any(f["detail"].get("counterpart_supersedes_id") == third
              for f in report["findings"]
              if f["kind"] == model.INTEGRITY_SUPERSESSION_ASYMMETRIC),
          str(report["findings"])[:200])
    # `first` is caught walking *forward*: its `superseded_by_id` names a row
    # whose `supersedes_id` names somebody else. `second` is caught walking
    # *backward*, from a `supersedes_id` whose target does not point back. A
    # check that followed only one direction would find the first and miss the
    # second, and a chain is only sound if both ends agree.
    check("the row at the other end is reported from its own pointer",
          model.INTEGRITY_SUPERSESSION_ASYMMETRIC in _for_fact(report, second),
          str(_for_fact(report, second)))
    check("and that finding names the backward pointer specifically",
          any(f["detail"].get("pointer") == "supersedes_id"
              and f["detail"].get("target_id") == third
              for f in report["findings"] if f["fact_id"] == second),
          str([f for f in report["findings"] if f["fact_id"] == second])[:200])

    lonely = _fact(cur, subject, value="4", fact_type="asym_lonely")
    _damage(cur, lonely, lifecycle_state=model.LIFECYCLE_SUPERSEDED)
    conn.commit()
    report = _sweep(cur)
    check("a SUPERSEDED row with nothing named as its replacement is reported",
          model.INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR
          in _for_fact(report, lonely), str(_for_fact(report, lonely)))
    check("but it ranks below the row that is serving a stale value",
          model.integrity_severity(model.INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR)
          < model.integrity_severity(model.INTEGRITY_ACTIVE_WITH_SUCCESSOR), "")


def stage_a_verified_badge_needs_something_under_it():
    """Three outcomes, and the gaps between them are the point.

    Nothing attached at all means the claim was never substantiated. Links that
    no longer resolve means it *was* — the document was there when somebody
    checked and has since been deleted — and telling the member that
    verification was baseless would be wrong, so the two are separate findings
    with separate remedies. A citation nobody can resolve is neither: the badge
    is unconfirmed and unrefuted, and condemning it would be condemning a fact
    on the strength of not having looked.
    """
    print("\n[a verified badge needs something under it]")
    conn, cur = cursor()
    subject = _subject()

    bare = _fact(cur, subject, value="1", fact_type="verify_bare")
    _damage(cur, bare,
            verification_state=model.VERIFICATION_OWNER_CONFIRMED,
            verified_at=_iso(1))

    gone_doc = _document(cur, OWNER, "deleted deed")
    gone = _fact(cur, subject, value="2", fact_type="verify_gone")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=gone,
                        source_ref=evidence.format_ref("document", gone_doc),
                        actor_user_id=OWNER)
    facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=gone,
        verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED,
        actor_user_id=OWNER)
    documents.delete_document(cur, owner_user_id=OWNER, document_id=gone_doc,
                              actor_user_id=OWNER)

    live_doc = _document(cur, OWNER, "live deed")
    live = _fact(cur, subject, value="3", fact_type="verify_live")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=live,
                        source_ref=evidence.format_ref("document", live_doc),
                        actor_user_id=OWNER)
    facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=live,
        verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED,
        actor_user_id=OWNER)
    conn.commit()

    report = _sweep(cur)
    check("a verified row with nothing attached is reported",
          model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE in _for_fact(report, bare),
          str(_for_fact(report, bare)))
    check("a verified row whose document was deleted is reported differently",
          model.INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE
          in _for_fact(report, gone), str(_for_fact(report, gone)))
    check("the two are never confused",
          model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE
          not in _for_fact(report, gone), str(_for_fact(report, gone)))
    check("a verified row with a live document is left alone",
          _for_fact(report, live) == [], str(_for_fact(report, live)))
    check("an unsupportable badge outranks a broken pointer",
          model.integrity_severity(model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE)
          > model.integrity_severity(model.INTEGRITY_SUPERSESSION_DANGLING), "")


def stage_a_citation_nobody_can_resolve_is_not_a_fault():
    """The third answer, at the evidence layer.

    A ref in a kind ``evidence.parse_ref`` does not know is dropped by the
    resolver rather than answered, so the fact's only citation produces no
    verdict at all. That is not support and it is not absence of support — and
    reporting it as either is how a legacy-backfilled store would light up
    entirely on its first sweep.
    """
    print("\n[an unresolvable citation]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="1", fact_type="verify_unknown_kind")
    stamp = _iso()
    # `link_evidence` validates the kind — that is what makes it a writer — so
    # a citation in a vocabulary this package does not know cannot be produced
    # through it. The legacy backfill will read tables this package never
    # wrote, which is where such a citation actually comes from.
    cur.execute(
        f"""INSERT INTO {schema.FACT_EVIDENCE_TABLE}
            (owner_user_id, fact_id, source_ref, source_kind, relation,
             linked_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (OWNER, fact_id, "ledger:44", "ledger", model.EVIDENCE_SUPPORTS,
         stamp, stamp))
    _damage(cur, fact_id,
            verification_state=model.VERIFICATION_AUTHORITY_VERIFIED,
            verified_at=_iso(1))
    conn.commit()

    report = _sweep(cur)
    check("the badge is not reported as unsupported",
          model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE
          not in _for_fact(report, fact_id), str(_for_fact(report, fact_id)))
    check("nor as having lost its evidence",
          model.INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE
          not in _for_fact(report, fact_id), str(_for_fact(report, fact_id)))
    check("it is counted as something nobody could check",
          report["uncheckable"].get(integrity.UNCHECKABLE_UNRESOLVABLE_REF, 0) >= 1,
          str(report["uncheckable"]))


def stage_an_unreadable_evidence_table_is_not_mass_corruption():
    """The worst thing this module could do, asserted against.

    If a failed evidence query read as "no evidence anywhere", one broken table
    would report every verified fact in the store as unsubstantiated at once —
    an infrastructure fault presented to the member as mass data corruption,
    with nothing in the output to tell them which it was.
    """
    print("\n[an unreadable evidence table]")
    conn, cur = cursor()
    subject = _subject()

    document = _document(cur, OWNER, "readable deed")
    for index in range(3):
        fact_id = _fact(cur, subject, value=str(index), fact_type=f"unread_{index}")
        facts.link_evidence(
            cur, owner_user_id=OWNER, fact_id=fact_id,
            source_ref=evidence.format_ref("document", document),
            actor_user_id=OWNER)
        facts.set_verification(
            cur, owner_user_id=OWNER, fact_id=fact_id,
            verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED,
            actor_user_id=OWNER)
    conn.commit()

    blind = _FailingCursor(cur, schema.FACT_EVIDENCE_TABLE)
    report = integrity.check_integrity(blind, owner_user_id=OWNER)
    check("no verified fact is reported as unsupported",
          model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE not in report["counts"],
          str(report["counts"]))
    check("nor as having unresolvable evidence",
          model.INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE not in report["counts"],
          str(report["counts"]))
    check("the failure is reported as an unreadable table",
          report["uncheckable"].get(integrity.UNCHECKABLE_EVIDENCE_UNREADABLE, 0) > 0,
          str(report["uncheckable"]))
    check("and the checks that do not need evidence still ran",
          report["scanned"] > 0, str(report["scanned"]))


def stage_provenance_is_checked_only_where_it_can_be():
    """``provenance_ref`` is not a foreign key, and the check says so.

    ``source_type`` and ``source_id`` are free strings naming an origin that is
    usually outside this system — a bank statement, a phone call, a registry the
    member read. There is nothing to dangle, and a check written as a join would
    report every honestly-recorded external origin as an orphan.

    So there are exactly two things worth saying: the column holds something
    that cannot be parsed at all, or it happens to spell a reference in the
    evidence vocabulary and that row is gone. Everything else is outside what
    can be seen, and stays silent.
    """
    print("\n[provenance where it can be checked]")
    conn, cur = cursor()
    subject = _subject()

    unreadable = _fact(cur, subject, value="1", fact_type="prov_unreadable")
    _damage(cur, unreadable, provenance_ref="{not json at all")

    external = _fact(cur, subject, value="2", fact_type="prov_external",
                     provenance=facts.ProvenanceRef(
                         source_type="bank_statement", source_id="march-2026",
                         observed_at=_iso(30)))

    document = _document(cur, OWNER, "provenance deed")
    orphaned = _fact(cur, subject, value="3", fact_type="prov_orphan",
                     provenance=facts.ProvenanceRef(
                         source_type="document", source_id=str(document),
                         observed_at=_iso(30)))
    documents.delete_document(cur, owner_user_id=OWNER, document_id=document,
                              actor_user_id=OWNER)
    conn.commit()

    report = _sweep(cur)
    check("a provenance blob nobody can parse is reported",
          model.INTEGRITY_PROVENANCE_UNREADABLE
          in _for_fact(report, unreadable), str(_for_fact(report, unreadable)))
    check("an origin outside this system is not called an orphan",
          _for_fact(report, external) == [], str(_for_fact(report, external)))
    check("a provenance naming a deleted document is",
          model.INTEGRITY_PROVENANCE_ORPHANED in _for_fact(report, orphaned),
          str(_for_fact(report, orphaned)))
    check("and the finding quotes the reference it could not reach",
          any(f["detail"].get("source_ref") == f"document:{document}"
              for f in report["findings"]
              if f["kind"] == model.INTEGRITY_PROVENANCE_ORPHANED),
          str(report["findings"])[:200])


def stage_a_reference_past_the_budget_is_not_an_orphan():
    """The third answer again, this time at the resolution budget.

    ``_provenance_orphans`` reads its verdict map with ``is False`` rather than
    testing falsiness, and this is the only fixture where the two differ: a ref
    that was never resolved is *absent* from the map, and absence read as
    falsiness becomes "this source is gone". The store would then report
    orphaned provenance in direct proportion to how much provenance it has,
    which is the failure mode where the diagnostic gets worse the more the
    member uses the product.

    The budget is lowered rather than the fixture inflated to two hundred rows.
    Same code path, same boundary, one second instead of a minute — and a
    fixture nobody will delete for being slow.

    A private owner, because the assertion is about *which* refs fell inside a
    budget of one, and that is only deterministic in a store this stage owns.
    """
    print("\n[a reference past the budget]")
    conn, cur = cursor()
    subject = _subject()
    budget_owner = 9903

    for index in range(2):
        document = _document(cur, budget_owner, f"budget deed {index}")
        _fact(cur, subject, owner=budget_owner, value=str(index),
              fact_type=f"budget_{index}",
              provenance=facts.ProvenanceRef(
                  source_type="document", source_id=str(document),
                  observed_at=_iso(30)))
        documents.delete_document(cur, owner_user_id=budget_owner,
                                  document_id=document, actor_user_id=budget_owner)
    conn.commit()

    generous = _sweep(cur, owner=budget_owner)
    check("with the real budget both deleted sources are reported",
          generous["counts"].get(model.INTEGRITY_PROVENANCE_ORPHANED) == 2,
          str(generous["counts"]))

    original = integrity.MAX_EVIDENCE_REFS
    integrity.MAX_EVIDENCE_REFS = 1
    try:
        tight = _sweep(cur, owner=budget_owner)
    finally:
        integrity.MAX_EVIDENCE_REFS = original

    check("with a budget of one only the ref that was resolved is reported",
          tight["counts"].get(model.INTEGRITY_PROVENANCE_ORPHANED) == 1,
          str(tight["counts"]))
    check("and the one nobody looked at is counted, not accused",
          tight["uncheckable"].get(integrity.UNCHECKABLE_EVIDENCE_BUDGET) == 1,
          str(tight["uncheckable"]))
    check("the budget is restored so later stages are unaffected",
          integrity.MAX_EVIDENCE_REFS == original,
          str(integrity.MAX_EVIDENCE_REFS))


def stage_every_finding_is_owner_scoped():
    """Another member's damage is invisible, and so is its absence.

    The owner predicate is in every query this module issues, but the sweep
    joins facts to evidence to documents, and a join is exactly where an owner
    predicate goes missing without anything failing.
    """
    print("\n[owner scoping]")
    conn, cur = cursor()
    subject = _subject()

    theirs = _fact(cur, subject, owner=OTHER, value="1", fact_type="other_bad")
    _damage(cur, theirs, superseded_by_id=987654321,
            verification_state=model.VERIFICATION_OWNER_CONFIRMED)
    conn.commit()

    mine = _sweep(cur)
    check("their broken row is not in my sweep",
          all(f["fact_id"] != theirs for f in mine["findings"]),
          str([f["fact_id"] for f in mine["findings"]])[:120])

    theirs_report = _sweep(cur, owner=OTHER)
    check("but it is in theirs",
          model.INTEGRITY_SUPERSESSION_DANGLING in _for_fact(theirs_report, theirs),
          str(_for_fact(theirs_report, theirs)))
    check("and their sweep sees only their own rows",
          theirs_report["total"] < mine["total"],
          f"{theirs_report['total']} vs {mine['total']}")
    # An empty result is not evidence of a refusal — a sweep with no owner
    # would return an empty list either way, because owner zero has no facts.
    # The guarantee is that it never reaches the database, so it is asserted
    # against the statements issued rather than against what came back.
    silent = _FailingCursor(cur)
    anonymous = integrity.check_integrity(silent, owner_user_id=0)
    check("an anonymous sweep issues no query at all",
          silent.calls == [], str(silent.calls[:2])[:160])
    check("and claims no completeness it did not earn",
          anonymous["complete"] is False and anonymous["findings"] == [],
          str(anonymous))


def stage_the_sweep_decides_nothing():
    """Read-only, proven by counting rows rather than by reading the source.

    The module is absent from ``WRITER_MODULES`` and the write-boundary guard
    enforces that statically. This is the dynamic half: every entry point is
    exercised against a store that is full of exactly the faults a repairing
    implementation would be tempted to fix, and nothing may move.
    """
    print("\n[the sweep decides nothing]")
    conn, cur = cursor()
    before = _row_counts(cur)

    integrity.check_integrity(cur, owner_user_id=OWNER)
    integrity.check_integrity(cur, owner_user_id=OWNER, scan=5, limit=3)
    integrity.check_integrity(cur, owner_user_id=OWNER,
                              kinds=[model.INTEGRITY_SUPERSESSION_CYCLE])
    integrity.integrity_summary(cur, owner_user_id=OWNER)
    conn.commit()

    after = _row_counts(cur)
    check("no table changed size", before == after, f"{before} -> {after}")


def stage_counts_describe_the_store_and_the_list_describes_the_page():
    """Truncation must not be able to masquerade as a smaller problem.

    ``findings`` is a bounded page. ``counts`` is what the sweep found. A caller
    that draws "2 problems" from the length of a truncated list is drawing the
    ceiling, and the two numbers are kept separate so it cannot.
    """
    print("\n[counts vs the page]")
    conn, cur = cursor()

    report = _sweep(cur, limit=2)
    total_found = sum(report["counts"].values())
    check("the page respects its ceiling",
          len(report["findings"]) == 2, str(len(report["findings"])))
    check("the sweep says it was truncated",
          report["truncated"] is True, str(report["truncated"]))
    check("the counts still describe everything found",
          total_found > 2, f"{total_found} found, 2 shown")
    check("the page is the most severe findings, not an arbitrary two",
          report["findings"][0]["severity"] >= report["findings"][1]["severity"],
          str([f["severity"] for f in report["findings"]]))

    full = _sweep(cur)
    check("an untruncated sweep says so",
          full["truncated"] is False or len(full["findings"]) == integrity.MAX_INTEGRITY_FINDINGS,
          str(full["truncated"]))
    check("and its list and counts agree",
          len(full["findings"]) == sum(full["counts"].values()),
          f"{len(full['findings'])} vs {sum(full['counts'].values())}")

    # Ordering must be total, or the same store pages differently each call.
    again = _sweep(cur)
    check("two sweeps of an unchanged store return the same order",
          [(f["fact_id"], f["kind"]) for f in full["findings"]]
          == [(f["fact_id"], f["kind"]) for f in again["findings"]], "")


def stage_a_filter_narrows_the_list_not_the_picture():
    """"Show me only the cycles" is about attention, not about the rest."""
    print("\n[filtering]")
    conn, cur = cursor()

    full = _sweep(cur)
    only = _sweep(cur, kinds=[model.INTEGRITY_SUPERSESSION_CYCLE])
    check("the list is narrowed to the requested kind",
          set(_kinds(only)) <= {model.INTEGRITY_SUPERSESSION_CYCLE},
          str(set(_kinds(only))))
    check("and it actually returned some",
          len(only["findings"]) > 0, str(len(only["findings"])))
    check("the counts are not narrowed with it",
          only["counts"] == full["counts"], str(only["counts"])[:160])
    check("an unrecognised filter name is ignored rather than obeyed",
          len(_sweep(cur, kinds=["NOT_A_FINDING"])["findings"])
          == len(full["findings"]), "")


def stage_the_bounds_are_published_and_enforced():
    """A ceiling a caller can argue above is not a ceiling."""
    print("\n[bounds]")
    conn, cur = cursor()

    huge = _sweep(cur, scan=99999, limit=99999)
    check("the scan cannot be argued above the module ceiling",
          huge["scanned"] <= integrity.MAX_INTEGRITY_SCAN, str(huge["scanned"]))
    # `scanned` alone cannot prove the clamp, and neither can the emitted SQL.
    # This store is far smaller than the ceiling, so an unclamped window returns
    # the same row count; and `facts.list_facts` re-clamps to 500 of its own
    # accord, so the LIMIT binding reads as 500 whether this module clamped or
    # not. Both observations are downstream of the thing under test. What is
    # actually being asserted is that *this module* does not ask for more than
    # its own ceiling — a guarantee that must survive `list_facts` later raising
    # its cap, which is exactly the day the redundancy stops being redundant.
    asked: list[object] = []
    original = integrity._facts.list_facts

    def _recording(cur_, **kwargs):
        asked.append(kwargs.get("limit"))
        return original(cur_, **kwargs)

    integrity._facts.list_facts = _recording
    try:
        integrity.check_integrity(cur, owner_user_id=OWNER, scan=99999)
    finally:
        integrity._facts.list_facts = original
    check("and the ceiling is what this module asks for, not what it is given",
          asked and all(int(w) <= integrity.MAX_INTEGRITY_SCAN for w in asked),
          str(asked))
    check("the spy was removed again",
          integrity._facts.list_facts is original, "")
    check("nor the findings page",
          len(huge["findings"]) <= integrity.MAX_INTEGRITY_FINDINGS,
          str(len(huge["findings"])))
    tiny = _sweep(cur, scan=0, limit=0)
    check("a zero scan still reads at least one row rather than none",
          tiny["scanned"] >= 1, str(tiny["scanned"]))
    check("every finding kind has a severity",
          all(k in model.INTEGRITY_SEVERITY for k in model.INTEGRITY_FINDINGS),
          str(sorted(set(model.INTEGRITY_FINDINGS) - set(model.INTEGRITY_SEVERITY))))
    check("and no severity is orphaned from the vocabulary",
          set(model.INTEGRITY_SEVERITY) == set(model.INTEGRITY_FINDINGS),
          str(sorted(set(model.INTEGRITY_SEVERITY) ^ set(model.INTEGRITY_FINDINGS))))
    # Zero, not the maximum. A finding kind this module cannot name came from
    # something that is itself broken, and ranking it at the top would put the
    # least trustworthy diagnostic in front of the most serious real one — on
    # the screen a member opens precisely when something has gone wrong.
    check("an unrecognised finding kind ranks at zero, not at the top",
          model.integrity_severity("NOT_A_FINDING") == 0
          and model.integrity_severity(None) == 0,
          f'{model.integrity_severity("NOT_A_FINDING")}, {model.integrity_severity(None)}')
    check("every uncheckable reason this module can emit is named",
          set(integrity.UNCHECKABLE_REASONS) >= set(huge["uncheckable"]),
          str(sorted(set(huge["uncheckable"]) - set(integrity.UNCHECKABLE_REASONS))))


def stage_the_summary_names_no_fact():
    """The aggregate view has a different privacy shape, so it is a different call.

    ``integrity_summary`` is for a dashboard panel sitting next to figures that
    are already aggregate. It must not carry a fact id, a subject or a fact
    type, and ``total_findings`` must come from the counts rather than from a
    truncated list — otherwise the panel silently reports the ceiling.
    """
    print("\n[the summary]")
    conn, cur = cursor()

    summary = integrity.integrity_summary(cur, owner_user_id=OWNER)
    full = _sweep(cur)
    check("the summary reports the same counts as the sweep",
          summary["counts"] == full["counts"], str(summary["counts"])[:160])
    check("total_findings is summed from the counts, not from a page",
          summary["total_findings"] == sum(full["counts"].values()),
          f"{summary['total_findings']} vs {sum(full['counts'].values())}")
    check("and it exceeds what a single-item page would have shown",
          summary["total_findings"] > 1, str(summary["total_findings"]))
    serialized = str(summary)
    check("the summary carries no per-fact identity",
          "fact_id" not in serialized and "subject_id" not in serialized,
          serialized[:160])
    check("it still admits how much of the store it saw",
          "complete" in summary and "scanned" in summary, str(sorted(summary)))


def stage_completeness_is_never_quietly_true():
    """A store whose size could not be measured has not been swept in full.

    The tempting failure is a ``COUNT`` that returns 0 on error, which makes
    ``scanned >= total`` true and stamps ``complete`` on a sweep that measured
    nothing — the precise inversion of what happened.
    """
    print("\n[completeness]")
    conn, cur = cursor()

    blind = _FailingCursor(cur, "COUNT(*)")
    report = integrity.check_integrity(blind, owner_user_id=OWNER)
    check("a sweep that could not count the store is not complete",
          report["complete"] is False, str(report["complete"]))
    check("and does not invent a total",
          report["total"] == 0, str(report["total"]))
    check("but still reports what it did read",
          report["scanned"] > 0, str(report["scanned"]))

    empty = integrity.check_integrity(cur, owner_user_id=123456)
    check("an empty store is complete rather than unknown",
          empty["complete"] is True and empty["total"] == 0, str(empty))


def main() -> int:
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    conn.commit()

    stage_a_healthy_store_reports_nothing()
    stage_corroboration_is_never_reported_as_duplication()
    stage_an_active_row_with_a_successor_outranks_everything()
    stage_a_cycle_is_detected_rather_than_survived()
    stage_a_pointer_past_the_window_is_not_a_broken_one()
    stage_a_link_both_rows_disagree_about_is_reported()
    stage_a_verified_badge_needs_something_under_it()
    stage_a_citation_nobody_can_resolve_is_not_a_fault()
    stage_an_unreadable_evidence_table_is_not_mass_corruption()
    stage_provenance_is_checked_only_where_it_can_be()
    stage_a_reference_past_the_budget_is_not_an_orphan()
    stage_every_finding_is_owner_scoped()
    stage_the_sweep_decides_nothing()
    stage_counts_describe_the_store_and_the_list_describes_the_page()
    stage_a_filter_narrows_the_list_not_the_picture()
    stage_the_bounds_are_published_and_enforced()
    stage_the_summary_names_no_fact()
    stage_completeness_is_never_quietly_true()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_integrity():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
