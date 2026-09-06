"""Supersession and immutable history — no silent overwrite, no cycles.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_fact_supersession.py
    python tests/private_office/test_private_fact_supersession.py

What these tests are actually defending
---------------------------------------
* **A fact's value is never edited in place.** Correcting a fact writes a second
  row and links the two. The old value, its provenance and its observation time
  all survive, so "what did I believe before this, and when did I stop" has an
  answer. There is no code path in this package that updates ``typed_value``,
  and the last stage here asserts that by reading the writer's source.
* **The chain is a chain.** One predecessor, one successor, per link. A second
  correction of an already-corrected row is refused rather than forking, because
  two rows claiming to have replaced the same fact is a contradiction wearing a
  supersession's clothes.
* **No cycles, and the cycle is reachable.** ``record_fact`` returns an
  *existing* row id when the same claim arrives from the same source in the same
  window. So correcting a fact back to a value already in its own chain lands on
  a row that is already upstream, and without a check the chain closes into a
  ring that every forward walk spins in. Two stages below drive exactly that.
* **History records transitions, never values.** The table has no value column
  and the writer has no value parameter; a list of every value a fact has ever
  held is a more sensitive object than the fact itself, and it would live in a
  table with no sensitivity column to govern who may read it.
* **A refused correction changes nothing.** Every rejection stage re-reads the
  chain afterwards. A guard that raises *after* half-writing the link would pass
  a test that only checks the exception.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_fact_supersession_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9301
OTHER = 9302

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


def _row(cur, fact_id: int) -> dict:
    cur.execute(
        "SELECT id, typed_value, lifecycle_state, supersedes_id, superseded_by_id,"
        " superseded_at, sensitivity, provenance_type, observed_at"
        " FROM private_facts WHERE id = ?",
        (fact_id,),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else {}


def _refuses(label: str, fn, expected=facts.PrivateFactRejected) -> None:
    """Assert ``fn`` raises ``expected`` — and nothing weaker."""
    try:
        fn()
    except expected as exc:
        check(label, True, str(exc))
    except Exception as exc:  # noqa: BLE001 - the wrong type is the failure
        check(label, False, f"raised {type(exc).__name__} instead: {exc}")
    else:
        check(label, False, "no exception raised")


def _write(cur, value, *, subject="node_alpha", fact_type="estimated_value",
           provenance=model.PROVENANCE_USER_ASSERTED, **kwargs) -> dict:
    return facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
        fact_type=fact_type, value=value, value_type=model.VALUE_MONEY,
        provenance_type=provenance, **kwargs)


# ---------------------------------------------------------------------------
_CHAIN: list[int] = []


def stage_correction_preserves_the_original():
    """A correction writes a new row; the old one keeps everything it had."""
    print("\n[correction]")
    conn, cur = cursor()
    schema.ensure_private_schema(cur)

    first = _write(cur, "100000")
    check("the first write is a new fact", first["status"] == facts.STATUS_WRITTEN,
          str(first))
    a = first["fact_id"]

    second = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=a, value="120000",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED)
    b = second["fact_id"]
    check("the correction is a different row", b != a, f"{a} -> {b}")
    check("the correction names what it replaced",
          second["superseded_fact_id"] == a, str(second))

    old = _row(cur, a)
    new_value = _row(cur, b)["typed_value"]
    # Compared as normalized against normalized rather than against the literal
    # the caller typed: MONEY stores "100000.0", and a test that asserts the raw
    # input would fail on a normalizer change that broke nothing.
    check("the original still holds its own value, not the correction's",
          old["typed_value"] == facts.normalize_value(
              "100000", model.VALUE_MONEY)[0] != new_value,
          f"{old.get('typed_value')} vs {new_value}")
    check("the original keeps its own provenance",
          old["provenance_type"] == model.PROVENANCE_USER_ASSERTED,
          old.get("provenance_type"))
    check("the original is SUPERSEDED, not deleted",
          old["lifecycle_state"] == model.LIFECYCLE_SUPERSEDED,
          old.get("lifecycle_state"))
    check("the original records when it stopped being current",
          bool(old["superseded_at"]))
    check("the original points forward at its replacement",
          old["superseded_by_id"] == b)

    new = _row(cur, b)
    check("the replacement points back", new["supersedes_id"] == a)
    check("the replacement is ACTIVE",
          new["lifecycle_state"] == model.LIFECYCLE_ACTIVE)
    check("the replacement has no successor yet", new["superseded_by_id"] == 0)

    third = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=b, value="135000",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_PROVIDER_ASSERTED)
    c = third["fact_id"]
    _CHAIN[:] = [a, b, c]
    conn.commit()


def stage_the_chain_reads_the_same_from_any_link():
    """``fact_chain`` returns the whole chain, oldest first, from any member."""
    print("\n[chain]")
    conn, cur = cursor()
    a, b, c = _CHAIN
    for label, start in (("the head", a), ("the middle", b), ("the tip", c)):
        found = facts.fact_chain(cur, owner_user_id=OWNER, fact_id=start)
        check(f"the chain reads whole from {label}", found == [a, b, c], str(found))

    lone = _write(cur, "42", subject="node_lonely")["fact_id"]
    check("an uncorrected fact is a chain of one",
          facts.fact_chain(cur, owner_user_id=OWNER, fact_id=lone) == [lone])
    check("a fact that does not exist has no chain",
          facts.fact_chain(cur, owner_user_id=OWNER, fact_id=987654) == [])
    check("another owner cannot walk this chain",
          facts.fact_chain(cur, owner_user_id=OTHER, fact_id=a) == [])
    conn.commit()


def stage_the_chain_refuses_to_fork_or_close():
    """Every way of breaking the chain is refused, and nothing is half-written."""
    print("\n[invariants]")
    conn, cur = cursor()
    a, b, c = _CHAIN

    _refuses("a second correction of an already-corrected row is refused",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=a, value="999999",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_USER_ASSERTED))

    # Reachable, not theoretical: the identical claim from the identical source
    # refreshes the tip and returns the tip's own id, so without the guard the
    # row would be recorded as having superseded itself.
    _refuses("correcting a fact to the value it already holds is refused",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=c, value="135000",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_PROVIDER_ASSERTED))

    # One link further out — this lands on `b`, which is upstream of `c`.
    _refuses("correcting a fact back to an earlier link is refused as a cycle",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=c, value="120000",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED))

    _refuses("a fact that does not exist reports missing, not rejected",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=987654, value="1",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_USER_ASSERTED),
             expected=facts.PrivateFactMissing)

    # The same error, with the same message, as a fact that is simply absent.
    # Anything else is an oracle for enumerating another account's id space.
    _refuses("another owner's fact reports missing, not forbidden",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OTHER, fact_id=a, value="1",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_USER_ASSERTED),
             expected=facts.PrivateFactMissing)

    _refuses("a correction cannot invent an unknown provenance",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=c, value="200000",
                 value_type=model.VALUE_MONEY, provenance_type="TRUST_ME"))

    _refuses("a correction cannot claim LEGACY_UNKNOWN",
             lambda: facts.supersede_fact(
                 cur, owner_user_id=OWNER, fact_id=c, value="200000",
                 value_type=model.VALUE_MONEY,
                 provenance_type=model.PROVENANCE_LEGACY_UNKNOWN))

    check("the chain is untouched by every refusal above",
          facts.fact_chain(cur, owner_user_id=OWNER, fact_id=a) == [a, b, c],
          str(facts.fact_chain(cur, owner_user_id=OWNER, fact_id=a)))
    check("the tip is still active after every refusal",
          _row(cur, c)["lifecycle_state"] == model.LIFECYCLE_ACTIVE)
    conn.commit()


def stage_a_correction_inherits_what_the_caller_omits():
    """Sensitivity and subject carry over; a correction cannot change either."""
    print("\n[inheritance]")
    conn, cur = cursor()
    guarded = facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id="node_private",
        fact_type="account_balance", value="5000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        sensitivity=model.SENSITIVITY_RESTRICTED,
        domain=model.DOMAIN_FINANCIAL)
    corrected = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=guarded["fact_id"], value="6000",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    # A correction that quietly dropped to the default would publish, to every
    # reader allowed at that level, a value the member had classified higher.
    check("a correction inherits the sensitivity it does not restate",
          corrected["sensitivity"] == model.SENSITIVITY_RESTRICTED,
          corrected.get("sensitivity"))
    check("a correction inherits the domain it does not restate",
          corrected["domain"] == model.DOMAIN_FINANCIAL, corrected.get("domain"))

    cur.execute("SELECT subject_id, fact_type FROM private_facts WHERE id = ?",
                (corrected["fact_id"],))
    replacement = dict(cur.fetchone())
    check("a correction is about the same subject",
          replacement["subject_id"] == "node_private", str(replacement))
    check("a correction is about the same fact type",
          replacement["fact_type"] == "account_balance", str(replacement))

    # Not a stylistic point. If a caller could redirect the subject, the chain
    # would assert continuity between two claims about different things.
    import inspect
    signature = inspect.signature(facts.supersede_fact).parameters
    for forbidden in ("subject_type", "subject_id", "fact_type"):
        check(f"a caller cannot pass {forbidden} to a correction",
              forbidden not in signature)
    conn.commit()


def stage_history_records_transitions_and_never_values():
    """The trail explains what happened without copying what it said."""
    print("\n[history]")
    conn, cur = cursor()
    a, b, c = _CHAIN

    head = facts.fact_history(cur, owner_user_id=OWNER, fact_id=a)
    kinds = [row["change_type"] for row in head]
    check("the head reads CREATED then CORRECTED",
          kinds == [model.CHANGE_CREATED, model.CHANGE_CORRECTED], str(kinds))
    check("CORRECTED names the successor", head[-1]["related_fact_id"] == b)
    check("CORRECTED records the lifecycle transition",
          (head[-1]["from_state"], head[-1]["to_state"])
          == (model.LIFECYCLE_ACTIVE, model.LIFECYCLE_SUPERSEDED), str(head[-1]))

    middle = facts.fact_history(cur, owner_user_id=OWNER, fact_id=b)
    kinds = [row["change_type"] for row in middle]
    check("a middle link reads CREATED, CORRECTS, CORRECTED",
          kinds == [model.CHANGE_CREATED, model.CHANGE_CORRECTS,
                    model.CHANGE_CORRECTED], str(kinds))
    check("both ends of one supersession are findable without a join",
          any(r["change_type"] == model.CHANGE_CORRECTS
              and r["related_fact_id"] == a for r in middle))

    # By absence over the whole row, not by spot-checking remembered names.
    leaked = {"typed_value", "value", "value_number", "fact_key", "provenance_ref",
              "sensitivity"} & set(middle[0].keys())
    check("no history row carries a value or a locator", not leaked, str(leaked))
    check("the writer has no value parameter",
          "value" not in __import__("inspect").signature(facts._history).parameters)

    check("history is owner-scoped",
          facts.fact_history(cur, owner_user_id=OTHER, fact_id=a) == [])
    check("history is ordered oldest first",
          [r["id"] for r in middle] == sorted(r["id"] for r in middle))
    check("history is bounded regardless of what the caller asks for",
          len(facts.fact_history(cur, owner_user_id=OWNER, fact_id=b,
                                 limit=10_000)) <= 500)

    check("an unknown change type is refused rather than stored",
          facts._history(cur, owner_user_id=OWNER, fact_id=a,
                         change_type="UPDATED") is False)
    check("there is no UPDATED change type to record",
          "UPDATED" not in model.HISTORY_CHANGE_TYPES)
    conn.commit()


def stage_a_chatty_source_cannot_flood_the_trail():
    """Re-confirmation is recorded, but not once per sync."""
    print("\n[refresh]")
    conn, cur = cursor()
    seed = _write(cur, "8800", subject="node_synced",
                  provenance=model.PROVENANCE_PROVIDER_ASSERTED)
    target = seed["fact_id"]
    before = len(facts.fact_history(cur, owner_user_id=OWNER, fact_id=target))

    for _ in range(6):
        again = _write(cur, "8800", subject="node_synced",
                       provenance=model.PROVENANCE_PROVIDER_ASSERTED)
        check("a repeat from the same source refreshes rather than duplicates",
              again["status"] == facts.STATUS_REFRESHED
              and again["fact_id"] == target, str(again))
    after = len(facts.fact_history(cur, owner_user_id=OWNER, fact_id=target))
    check("same-day re-syncs write no history at all", after == before,
          f"{before} -> {after}")

    _write(cur, "8800", subject="node_synced",
           provenance=model.PROVENANCE_PROVIDER_ASSERTED,
           observed_at="2027-06-01T00:00:00Z")
    trail = facts.fact_history(cur, owner_user_id=OWNER, fact_id=target)
    check("a re-confirmation much later is recorded",
          trail[-1]["change_type"] == model.CHANGE_REFRESHED,
          str([r["change_type"] for r in trail]))
    check("a refresh does not change the lifecycle",
          _row(cur, target)["lifecycle_state"] == model.LIFECYCLE_ACTIVE)
    conn.commit()


def stage_the_writer_never_edits_a_value():
    """No UPDATE in this package touches ``typed_value``. Read the source."""
    print("\n[no in-place edit]")
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "services", "private_office", "facts.py")
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    updates = [
        segment for segment in source.split("UPDATE ")[1:]
        if "private_facts" in segment.split(")")[0] or "FACTS_TABLE" in segment[:64]
    ]
    check("the fact writer issues at least one UPDATE to check", bool(updates))
    for index, segment in enumerate(updates):
        clause = segment.split("WHERE")[0]
        for column in ("typed_value", "value_number", "value_type", "subject_id",
                       "fact_type", "provenance_type"):
            check(f"UPDATE #{index + 1} does not rewrite {column}",
                  column not in clause, clause.strip()[:120])


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    schema.reset_schema_cache()
    stage_correction_preserves_the_original()
    stage_the_chain_reads_the_same_from_any_link()
    stage_the_chain_refuses_to_fork_or_close()
    stage_a_correction_inherits_what_the_caller_omits()
    stage_history_records_transitions_and_never_values()
    stage_a_chatty_source_cannot_flood_the_trail()
    stage_the_writer_never_edits_a_value()

    print()
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — supersession and history hold.")
    return 0


def test_private_fact_supersession():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
