"""The review queue — what needs attention, in what order, and why.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_review_queue.py
    python tests/private_office/test_private_review_queue.py

What these tests are actually defending
---------------------------------------
* **The ranking is a maximum, not a sum.** Three mild observations about one
  dusty row must not outrank one live contradiction. This is the single
  assertion the module exists for: a member works down the queue until they
  lose interest, so whatever sorts to the top is the only part of the queue
  that exists.
* **The order is total and stable.** Two reads of unchanged data must produce
  the same sequence. A queue that reshuffles between refreshes loses the
  member's place, and losing their place in a list of things that need
  attention is how items stop getting attention.
* **Every item explains itself.** ``primary_reason`` is the reason it was
  ranked by — not a separate editorial choice that can drift from the score.
* **A settled conflict leaves the queue.** ``mark_conflicts`` never clears the
  ``conflict_id`` it stamps, so reading the marker as the answer would make
  CONTRADICTED permanent and the queue unclearable. And a decision reached
  about *other* rows must not clear this one.
* **Degrading never invents work.** An unreadable evidence table reports no
  missing sources rather than flagging every fact — a queue that cries wolf
  about vanished documents is one whose genuine SOURCE_UNAVAILABLE items get
  scrolled past.
* **The source check stays batched.** One query against the evidence table for
  the whole page, and one probe per *distinct* document rather than per fact.
  The N+1 belongs to the screen most likely to be opened against a large store.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_review_queue_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import review  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9701
OTHER = 9702

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

    Every stage below reads the whole owner's queue, so two stages sharing a
    subject would let one stage's rows appear as another's conflicts. The
    isolation is per-subject rather than per-owner because owner isolation is
    itself one of the things under test and must not be spent on hygiene.
    """
    global _SUBJECT
    _SUBJECT += 1
    return f"rq{_SUBJECT}"


def _fact(cur, subject: str, owner: int = OWNER, *, value: object = "100",
          fact_type: str = "estimated_value", **kwargs) -> int:
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("observed_at", _iso(1))
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject,
        fact_type=fact_type, value=value, value_type=model.VALUE_MONEY,
        actor_user_id=owner, **kwargs)["fact_id"]


def _healthy(cur, subject: str, owner: int = OWNER) -> int:
    """A fact with nothing wrong with it, which must never reach the queue.

    Worth stating explicitly rather than assuming: USER_ASSERTED carries a
    180-day freshness horizon, opens UNVERIFIED, has no validity window and no
    evidence, so every one of the nine reasons is false. If a change makes this
    row appear, the queue has started flagging ordinary facts and the signal is
    gone.
    """
    return _fact(cur, subject, owner)


def _document(cur, owner: int, title: str) -> int:
    """A real row in the real documents table, built by the real DDL."""
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


def _disagreeing_pair(cur, subject: str, owner: int = OWNER) -> dict:
    """Two facts that genuinely conflict, marked so the rows carry the id.

    Same construction as the contradiction suite: one subject, one fact type,
    overlapping windows six hours apart so the pair lands inside
    ``SIMULTANEITY_HOURS`` and is compared as two claims about one moment
    rather than as a value that moved.
    """
    ids = []
    for offset, value in zip(("00", "06"), (0.35, 0.40)):
        stamp = f"2026-05-20T{offset}:00:00Z"
        ids.append(facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id=subject,
            fact_type="ownership_share", value_type=model.VALUE_PERCENT,
            value=value, provenance_type=model.PROVENANCE_USER_ASSERTED,
            valid_from=stamp, observed_at=stamp, actor_user_id=owner,
        )["fact_id"])
    found = contradictions.detect_conflicts(
        cur, owner_user_id=owner, subject_id=subject)
    if len(found) != 1:
        raise AssertionError(
            f"fixture did not produce exactly one conflict: {len(found)}")
    contradictions.mark_conflicts(cur, owner_user_id=owner, conflicts=found)
    return {"conflict": found[0], "ids": ids, "subject_id": subject}


def _queue(cur, owner: int = OWNER, **kwargs) -> list[dict]:
    return review.review_queue(cur, owner_user_id=owner, at=NOW, **kwargs)


def _live_refs(cur, owner: int = OWNER) -> list:
    """Every still-attached evidence link this owner has, read directly.

    Used only to express the batching bound in terms of *distinct sources*,
    which is the quantity the resolver is allowed to scale with.
    """
    cur.execute(
        f"SELECT source_ref FROM {schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND detached_at = ''", (owner,))
    return cur.fetchall()


def _for(items: list[dict], fact_id: int) -> dict | None:
    for item in items:
        if item["fact_id"] == fact_id:
            return item
    return None


class _CountingCursor:
    """A cursor that records the SQL it is asked to run.

    Delegating rather than mocking. The queries under inspection are the ones
    the real code issues against a real database, so the only thing this
    substitutes is the bookkeeping — a fake cursor would let the batching
    assertion pass against a query shape the database has never seen.
    """

    def __init__(self, inner, fail_on: str = ""):
        self._inner = inner
        self._fail_on = fail_on
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        if self._fail_on and self._fail_on in str(sql):
            raise RuntimeError("simulated read failure")
        return self._inner.execute(sql, params) if params is not None \
            else self._inner.execute(sql)

    def count(self, needle: str) -> int:
        return sum(1 for sql in self.statements if needle in sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
def stage_every_reason_is_reachable():
    """The vocabulary and the producer agree, in both directions.

    A coverage guard rather than nine separate assertions. ``REVIEW_REASONS``
    is hand-maintained in ``model.py`` and ``review_reasons`` is the only thing
    that emits them; a reason added to one and not the other is a dead entry
    that a screen will render a filter chip for and never populate. Discovering
    the union from real calls is what makes that failure visible.
    """
    print("\n[vocabulary]")
    seen: set[str] = set()

    cases = {
        model.REVIEW_CONTRADICTED: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED},
            open_conflict=True),
        model.REVIEW_SOURCE_UNAVAILABLE: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED},
            missing_source=True),
        model.REVIEW_VERIFICATION_FAILED: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED,
                 "verification_state": model.VERIFICATION_FAILED}),
        model.REVIEW_DISPUTED: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED,
                 "verification_state": model.VERIFICATION_DISPUTED}),
        model.REVIEW_VERIFICATION_EXPIRED: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED,
                 "verification_state": model.VERIFICATION_OWNER_CONFIRMED,
                 "verified_at": _iso(400)}),
        model.REVIEW_VALIDITY_ENDING: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED,
                 "valid_to": _iso(-10)}),
        model.REVIEW_PROPOSED: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED,
                 "verification_state": model.VERIFICATION_PENDING_REVIEW}),
        model.REVIEW_UNKNOWN_ORIGIN: dict(
            row={"observed_at": _iso(1),
                 "provenance_type": model.PROVENANCE_LEGACY_UNKNOWN}),
        model.REVIEW_STALE: dict(
            row={"observed_at": _iso(400),
                 "provenance_type": model.PROVENANCE_USER_ASSERTED}),
    }

    for expected, kwargs in cases.items():
        row = dict(kwargs.pop("row"))
        found = review.review_reasons(row, at=NOW, **kwargs)
        seen.update(found)
        check(f"{expected} is produced by a fact that has it",
              expected in found, str(found))

    check("every declared review reason is reachable from review_reasons",
          seen >= set(model.REVIEW_REASONS),
          f"never produced: {sorted(set(model.REVIEW_REASONS) - seen)}")
    check("review_reasons invents no reason outside the vocabulary",
          seen <= set(model.REVIEW_REASONS),
          f"unknown: {sorted(seen - set(model.REVIEW_REASONS))}")

    check("a healthy fact produces no reasons at all",
          review.review_reasons(
              {"observed_at": _iso(1),
               "provenance_type": model.PROVENANCE_USER_ASSERTED},
              at=NOW) == [])

    # The validity horizon has two sides and the interesting one is behind us.
    # Every fixture above uses a window closing shortly, which a "warn me about
    # the next thirty days" reading satisfies just as well as the real rule —
    # so on its own it cannot tell the two apart. A fact still ACTIVE whose
    # window shut months ago is asserting something it has itself declared out
    # of date, which is a stronger reason to look than one expiring next week,
    # not a reason to drop it for being too late to warn about.
    long_closed = review.review_reasons(
        {"observed_at": _iso(1),
         "provenance_type": model.PROVENANCE_USER_ASSERTED,
         "valid_to": _iso(60)},
        at=NOW)
    check("a validity window that closed two months ago is still flagged",
          model.REVIEW_VALIDITY_ENDING in long_closed, str(long_closed))

    # And the far side, so the check above cannot be satisfied by a rule that
    # simply flags every fact carrying a valid_to at all.
    far_off = review.review_reasons(
        {"observed_at": _iso(1),
         "provenance_type": model.PROVENANCE_USER_ASSERTED,
         "valid_to": _iso(-365 * 5)},
        at=NOW)
    check("a window closing in five years is not the member's problem yet",
          model.REVIEW_VALIDITY_ENDING not in far_off, str(far_off))


def stage_the_score_is_a_maximum_not_a_sum():
    """Several mild problems must not outrank one contradiction.

    The headline. A fact can be stale *and* of unknown origin *and* have a
    validity window closing *and* carry a verification that has aged out —
    10 + 20 + 40 + 50 = 120, which under a summed score outranks a live
    contradiction at 100. That is the wrong answer: the summed row is merely
    under-documented, while the contradiction is the one case where the store
    holds two different answers and cannot say which it would give.

    Taking the maximum also keeps the label honest. The summed row's score of
    120 corresponds to no reason at all, so there is nothing true the queue
    could print beside it to explain why it is first.
    """
    print("\n[maximum, not sum]")
    conn, cur = cursor()

    contradicted = review.review_reasons(
        {"observed_at": _iso(1),
         "provenance_type": model.PROVENANCE_USER_ASSERTED},
        open_conflict=True, at=NOW)
    ceiling = model.REVIEW_WEIGHT[model.REVIEW_CONTRADICTED]

    overloaded = review.review_reasons(
        {"observed_at": _iso(400),
         "provenance_type": model.PROVENANCE_LEGACY_UNKNOWN,
         "valid_to": _iso(-10),
         "verification_state": model.VERIFICATION_OWNER_CONFIRMED,
         "verified_at": _iso(400)},
        at=NOW)
    summed = sum(model.REVIEW_WEIGHT[r] for r in overloaded)
    check("a fact can accumulate four independent mild reasons",
          len(overloaded) == 4, str(overloaded))
    check("whose weights sum past a contradiction's",
          summed > ceiling, f"sum={summed} vs {ceiling}")
    check("yet its primary reason is a single one of them, well below that",
          overloaded[0] == model.REVIEW_VERIFICATION_EXPIRED
          and model.REVIEW_WEIGHT[overloaded[0]] < ceiling,
          str(overloaded))
    check("so it ranks below a contradiction rather than above it",
          model.REVIEW_WEIGHT[overloaded[0]]
          < model.REVIEW_WEIGHT[contradicted[0]])

    crowded = {"observed_at": _iso(400),
               "provenance_type": model.PROVENANCE_LEGACY_UNKNOWN,
               "valid_to": _iso(-10)}
    crowded_reasons = review.review_reasons(crowded, at=NOW)
    check("the three-reason case behaves the same way",
          len(crowded_reasons) == 3
          and crowded_reasons[0] == model.REVIEW_VALIDITY_ENDING
          and sum(model.REVIEW_WEIGHT[r] for r in crowded_reasons)
          > model.REVIEW_WEIGHT[crowded_reasons[0]],
          str(crowded_reasons))

    # The same thing through the queue, which is where it actually matters.
    dusty_subject, conflict_subject = _subject(), _subject()
    dusty = _fact(cur, dusty_subject, observed_at=_iso(400),
                  provenance_type=model.PROVENANCE_LEGACY_UNKNOWN,
                  valid_to=_iso(-10), allow_backfill=True)
    pair = _disagreeing_pair(cur, conflict_subject)
    conn.commit()

    items = _queue(cur)
    dusty_item = _for(items, dusty)
    conflict_item = _for(items, pair["ids"][0])
    check("the dusty fact is queued", dusty_item is not None)
    check("the contradicted fact is queued", conflict_item is not None)
    if dusty_item and conflict_item:
        check("the dusty fact carries more reasons than the contradicted one",
              len(dusty_item["reasons"]) > len(conflict_item["reasons"]),
              f"{dusty_item['reasons']} vs {conflict_item['reasons']}")
        check("and still sorts below it",
              items.index(conflict_item) < items.index(dusty_item),
              f"contradiction at {items.index(conflict_item)}, "
              f"dusty at {items.index(dusty_item)}")
        check("priority is the weight of the primary reason, not a total",
              dusty_item["priority"]
              == model.REVIEW_WEIGHT[dusty_item["primary_reason"]]
              < summed, str(dusty_item["priority"]))

    _STATE["dusty"] = dusty
    _STATE["pair"] = pair


def stage_every_item_explains_itself():
    """``primary_reason`` is the reason it was ranked by, on every item.

    Checked over the whole queue rather than one crafted row. The failure this
    catches is a drift between the score and the label — an item ranked at 100
    displaying "STALE" is worse than an unexplained order, because it is a
    confident wrong answer to "why is this first".
    """
    print("\n[self-explaining]")
    conn, cur = cursor()
    items = _queue(cur)
    check("the queue is not empty, or this stage proves nothing", bool(items))

    for item in items:
        strongest = max(model.REVIEW_WEIGHT[r] for r in item["reasons"])
        if item["priority"] != strongest:
            check(f"fact {item['fact_id']} is ranked by its strongest reason",
                  False, f"priority={item['priority']} strongest={strongest}")
            return
        if item["primary_reason"] != item["reasons"][0]:
            check(f"fact {item['fact_id']} names the reason it was ranked by",
                  False, str(item))
            return
        if model.REVIEW_WEIGHT[item["primary_reason"]] != item["priority"]:
            check(f"fact {item['fact_id']}'s label matches its score", False,
                  str(item))
            return
    check("every item is ranked by, and names, its strongest reason", True,
          f"{len(items)} item(s)")

    descending = all(
        items[i]["priority"] >= items[i + 1]["priority"]
        for i in range(len(items) - 1))
    check("the queue is ordered by priority descending", descending,
          str([i["priority"] for i in items]))


def stage_a_settled_conflict_leaves_the_queue():
    """Resolving a disagreement clears CONTRADICTED, though the marker stays.

    The tie-in to durable resolution. ``mark_conflicts`` never clears the
    ``conflict_id`` it writes, on purpose — the row keeps the record that it
    was once in dispute. If the queue read that marker as the answer,
    CONTRADICTED would be permanent and no amount of member effort could empty
    the list.

    SEPARATED is the outcome that makes this observable: its competitors all
    stay ACTIVE, so the row is still in the queue's scan and still carries the
    marker. Under KEPT the losers are archived and drop out on their own, which
    would let a broken check pass.
    """
    print("\n[settled conflicts]")
    conn, cur = cursor()
    subject = _subject()
    pair = _disagreeing_pair(cur, subject)
    conn.commit()

    before = _queue(cur)
    first = _for(before, pair["ids"][0])
    check("an unresolved conflict puts both facts in the queue",
          first is not None and _for(before, pair["ids"][1]) is not None)
    if first:
        check("ranked as CONTRADICTED",
              first["primary_reason"] == model.REVIEW_CONTRADICTED,
              str(first["reasons"]))

    contradictions.resolve_conflict(
        cur, owner_user_id=OWNER, conflict_id=pair["conflict"]["conflict_id"],
        outcome=model.RESOLUTION_SEPARATED,
        competing_fact_ids=pair["ids"], subject_type="NODE",
        subject_id=subject, fact_type="ownership_share",
        reason="two different holdings", actor_user_id=OWNER)
    conn.commit()

    after = _queue(cur)
    still_marked = facts.list_facts(
        cur, owner_user_id=OWNER, subject_id=subject, limit=10)
    check("both facts are still ACTIVE and still carry the conflict marker",
          len(still_marked) == 2
          and all(str(r.get("conflict_id") or "") for r in still_marked),
          str([(r["id"], r.get("conflict_id")) for r in still_marked]))
    check("but neither is queued as CONTRADICTED any more",
          all(model.REVIEW_CONTRADICTED not in (item["reasons"] or [])
              for item in after if item["fact_id"] in pair["ids"]),
          str([i for i in after if i["fact_id"] in pair["ids"]]))

    # DEFERRED must not do this. "I looked and could not decide" is the answer
    # a member gives when the conflict is real and hard, and if engaging with
    # the backlog were the way to make an item disappear, the queue would
    # reward exactly the response it exists to discourage.
    other = _subject()
    deferred_pair = _disagreeing_pair(cur, other)
    contradictions.resolve_conflict(
        cur, owner_user_id=OWNER,
        conflict_id=deferred_pair["conflict"]["conflict_id"],
        outcome=model.RESOLUTION_DEFERRED,
        competing_fact_ids=deferred_pair["ids"], subject_type="NODE",
        subject_id=other, fact_type="ownership_share",
        reason="need the statement first", actor_user_id=OWNER)
    conn.commit()

    deferred_items = [i for i in _queue(cur)
                      if i["fact_id"] in deferred_pair["ids"]]
    check("a deferred conflict stays in the queue as CONTRADICTED",
          len(deferred_items) == 2
          and all(i["primary_reason"] == model.REVIEW_CONTRADICTED
                  for i in deferred_items),
          str(deferred_items))


def stage_a_decision_about_other_rows_clears_nothing():
    """A closing resolution only settles the facts it actually names.

    The set-binding check, viewed from the queue. A resolution row is looked up
    by ``conflict_id``, and an id is a hash — so the question "is this fact
    settled" cannot be answered by finding *a* closed decision under that id.
    It has to be a decision about this row. Without the membership test a
    resolution reached about two other facts would silently clear a live
    disagreement, and it would do so invisibly: the fact simply stops appearing.
    """
    print("\n[decisions bind to their own rows]")
    conn, cur = cursor()
    subject = _subject()
    pair = _disagreeing_pair(cur, subject)
    conn.commit()

    conflict_id = pair["conflict"]["conflict_id"]
    outsider = _fact(cur, _subject())
    conn.commit()
    resolution = {
        "closed": True,
        "competing_fact_ids": [outsider, outsider + 1],
    }

    row = dict(facts.list_facts(
        cur, owner_user_id=OWNER, subject_id=subject, limit=1)[0])
    check("the fixture row carries the marker",
          str(row.get("conflict_id") or "") == conflict_id, str(row.get("conflict_id")))
    check("a closed decision naming other facts leaves this one in dispute",
          review._open_conflict(row, {conflict_id: resolution}) is True)
    check("a closed decision naming this fact settles it",
          review._open_conflict(
              row, {conflict_id: {"closed": True,
                                  "competing_fact_ids": [int(row["id"])]}})
          is False)
    check("a decision that closes nothing settles nothing",
          review._open_conflict(
              row, {conflict_id: {"closed": False,
                                  "competing_fact_ids": [int(row["id"])]}})
          is True)
    check("no decision at all leaves the conflict open",
          review._open_conflict(row, {}) is True)
    check("a fact with no marker is not contradicted",
          review._open_conflict({"id": 1, "conflict_id": ""}, {}) is False)


def stage_a_vanished_source_is_flagged():
    """A fact whose document was destroyed says so, and ranks accordingly.

    ``delete_document`` keeps a tombstone so the trail stays readable, which is
    right for the trail and wrong for availability: without this the fact keeps
    citing a document whose bytes are gone, and the citation still reads as
    support.
    """
    print("\n[vanished sources]")
    conn, cur = cursor()
    subject = _subject()
    doc = _document(cur, OWNER, "statement.pdf")
    kept_doc = _document(cur, OWNER, "deed.pdf")

    gone = _fact(cur, subject)
    intact = _fact(cur, subject, value="200", fact_type="purchase_price")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=gone,
                        source_ref=evidence.format_ref("document", doc),
                        actor_user_id=OWNER)
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=intact,
                        source_ref=evidence.format_ref("document", kept_doc),
                        actor_user_id=OWNER)
    conn.commit()

    check("neither fact is queued while both documents exist",
          _for(_queue(cur), gone) is None and _for(_queue(cur), intact) is None)

    documents.delete_document(cur, owner_user_id=OWNER, document_id=doc,
                              actor_user_id=OWNER)
    conn.commit()

    items = _queue(cur)
    flagged = _for(items, gone)
    check("the fact citing the destroyed document is queued",
          flagged is not None)
    if flagged:
        check("as SOURCE_UNAVAILABLE",
              flagged["primary_reason"] == model.REVIEW_SOURCE_UNAVAILABLE,
              str(flagged["reasons"]))
    check("the fact citing the surviving document is left alone",
          _for(items, intact) is None)

    _STATE["gone_fact"] = gone
    _STATE["gone_doc"] = doc


def stage_only_active_facts_are_queued():
    """Corrected and retired facts are history, not work.

    A superseded row has already been fixed and an archived one is no longer
    asserted. Queueing either fills the list with items whose only available
    action is "yes, I know" — and a list of things the member cannot act on is
    how they learn to stop reading it.
    """
    print("\n[active only]")
    conn, cur = cursor()
    subject = _subject()

    old = _fact(cur, subject, observed_at=_iso(400))
    check("the fact is queued while active", _for(_queue(cur), old) is not None)

    replacement = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=old, value="150",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        observed_at=_iso(400), actor_user_id=OWNER)["fact_id"]
    conn.commit()

    items = _queue(cur)
    check("a superseded fact drops out of the queue",
          _for(items, old) is None)
    check("its replacement takes its place",
          _for(items, replacement) is not None)

    facts.archive_fact(cur, owner_user_id=OWNER, fact_id=replacement,
                       actor_user_id=OWNER)
    conn.commit()
    check("an archived fact drops out too",
          _for(_queue(cur), replacement) is None)


def stage_the_queue_is_owner_scoped():
    """Another member's problems are never this member's work.

    Both facts below are flagged for the same reason, so a queue that leaked
    would return two indistinguishable items and the leak would read as a
    duplicate rather than as a breach.
    """
    print("\n[owner isolation]")
    conn, cur = cursor()
    mine = _fact(cur, _subject(), OWNER, observed_at=_iso(400))
    theirs = _fact(cur, _subject(), OTHER, observed_at=_iso(400))
    conn.commit()

    mine_queue = _queue(cur, OWNER)
    check("my flagged fact is in my queue", _for(mine_queue, mine) is not None)
    check("their flagged fact is not", _for(mine_queue, theirs) is None)

    owned = {int(r["id"]) for r in facts.list_facts(
        cur, owner_user_id=OWNER, limit=500, include_superseded=True)}
    strays = [i["fact_id"] for i in mine_queue if i["fact_id"] not in owned]
    check("every item in my queue is a fact I own", not strays, str(strays))

    theirs_queue = _queue(cur, OTHER)
    check("their queue is theirs alone",
          _for(theirs_queue, theirs) is not None
          and _for(theirs_queue, mine) is None)
    check("an owner of zero gets nothing rather than everything",
          review.review_queue(cur, owner_user_id=0, at=NOW) == [])


def stage_the_order_is_total_and_stable():
    """Two reads of unchanged data produce the same sequence.

    Ties break on reason count and then on fact id. The id is not decoration:
    ``ORDER BY observed_at DESC, id DESC`` in ``list_facts`` gives a stable
    input, but the queue re-sorts on computed values, and Python's sort is only
    stable with respect to an order the caller can predict. Two items alike in
    priority and reason count must not swap places between refreshes, or the
    member loses their place in the one list that is supposed to be worked
    through.
    """
    print("\n[total order]")
    conn, cur = cursor()
    subject = _subject()
    twins = [_fact(cur, subject, value=str(300 + i), observed_at=_iso(400),
                   fact_type=f"twin_{i}") for i in range(4)]
    conn.commit()

    first = [i["fact_id"] for i in _queue(cur)]
    second = [i["fact_id"] for i in _queue(cur)]
    check("the same data reads back in the same order", first == second,
          f"{first} vs {second}")

    positions = [first.index(t) for t in twins if t in first]
    check("all four indistinguishable facts are queued", len(positions) == 4,
          str(positions))
    check("and they are ordered by id, ascending",
          positions == sorted(positions)
          and [first[p] for p in sorted(positions)] == sorted(twins),
          str([first[p] for p in sorted(positions)]))

    ranked = _queue(cur)
    ordered = all(
        (-ranked[i]["priority"], -len(ranked[i]["reasons"]), ranked[i]["fact_id"])
        <= (-ranked[i + 1]["priority"], -len(ranked[i + 1]["reasons"]),
            ranked[i + 1]["fact_id"])
        for i in range(len(ranked) - 1))
    check("the whole queue obeys (priority, reason count, id)", ordered)


def stage_filtering_narrows_without_reordering():
    """A filtered queue and the full one agree about relative priority.

    Filtering is a view, not a different ranking. If "show me only
    contradictions" re-sorted its results, the member would have two screens
    disagreeing about which of the same two facts matters more, and no way to
    tell which one to believe.
    """
    print("\n[filtering]")
    conn, cur = cursor()
    full = _queue(cur)
    check("the full queue has something to filter", len(full) > 1)

    wanted = model.REVIEW_STALE
    filtered = _queue(cur, reasons=[wanted])
    expected = [i["fact_id"] for i in full if wanted in i["reasons"]]
    check("filtering returns exactly the items carrying that reason",
          [i["fact_id"] for i in filtered] == expected,
          f"{[i['fact_id'] for i in filtered]} vs {expected}")
    check("filtering does not change what those items say",
          all(_for(full, i["fact_id"])["reasons"] == i["reasons"]
              for i in filtered))

    check("an unrecognised reason filters nothing rather than everything",
          [i["fact_id"] for i in _queue(cur, reasons=["NOT_A_REASON"])]
          == [i["fact_id"] for i in full])
    check("a lower-case reason is accepted",
          [i["fact_id"] for i in _queue(cur, reasons=[wanted.lower()])]
          == expected)

    check("a limit truncates the top of the queue, not a random slice",
          [i["fact_id"] for i in _queue(cur, limit=2)]
          == [i["fact_id"] for i in full[:2]])


def stage_the_summary_agrees_with_the_queue():
    """The badge and the list behind it cannot disagree.

    A "12 need review" header opening onto nine rows is a small inconsistency
    that costs the member's trust in everything else on the screen, and it is
    the default outcome of counting with one query and listing with another.
    The summary is computed from the queue for that reason.
    """
    print("\n[summary]")
    conn, cur = cursor()
    items = _queue(cur)
    summary = review.review_summary(cur, owner_user_id=OWNER, at=NOW)

    check("the total matches the number of queued items",
          summary["total"] == len(items),
          f"{summary['total']} vs {len(items)}")
    check("the top reason matches the first item",
          summary["top_reason"] == items[0]["primary_reason"],
          f"{summary['top_reason']} vs {items[0]['primary_reason']}")

    expected = {reason: 0 for reason in model.REVIEW_REASONS}
    for item in items:
        for reason in item["reasons"]:
            expected[reason] += 1
    check("every per-reason count matches a recount of the queue",
          summary["by_reason"] == expected,
          f"{summary['by_reason']} vs {expected}")
    check("by_reason names every reason, including the zeroes",
          set(summary["by_reason"]) == set(model.REVIEW_REASONS))
    check("a fact with several reasons is counted once in the total",
          summary["total"] <= sum(summary["by_reason"].values()))
    check("an empty store summarises to nothing rather than failing",
          review.review_summary(cur, owner_user_id=9799, at=NOW)
          == {"total": 0,
              "by_reason": {r: 0 for r in model.REVIEW_REASONS},
              "top_reason": "", "truncated": False})


def stage_the_source_check_stays_batched():
    """One evidence query per page, and one probe per distinct document.

    The N+1 this forbids is not hypothetical: the per-fact path
    (``fact_evidence``) exists and is the obvious thing to call in a loop. It
    would issue a query per fact plus a resolution per ref, on the screen most
    likely to be opened against a large store.
    """
    print("\n[batching]")
    conn, cur = cursor()
    subject = _subject()
    shared = _document(cur, OWNER, "shared.pdf")
    ref = evidence.format_ref("document", shared)

    # Sized to straddle the resolver's own chunk boundary. With fewer links
    # than `evidence.MAX_REFS` every arrangement fits in one chunk and
    # `resolve_refs` dedups within it, so a queue that forwarded one entry per
    # *link* instead of per distinct source would look identical from the
    # outside — the batching would be accidental rather than built in, and it
    # would come apart at exactly the volume where it starts to matter.
    batch = evidence.MAX_REFS - 8
    linked = [_fact(cur, subject, value=str(400 + i), fact_type=f"batch_{i}")
              for i in range(batch)]
    for fact_id in linked:
        facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                            source_ref=ref, actor_user_id=OWNER)
    conn.commit()

    counter = _CountingCursor(cur)
    review.review_queue(counter, owner_user_id=OWNER, at=NOW)
    evidence_reads = counter.count(schema.FACT_EVIDENCE_TABLE)
    doc_reads = counter.count(documents.DOCUMENTS_TABLE)
    check("the evidence table is read once for the whole page",
          evidence_reads == 1, f"{evidence_reads} read(s)")

    # Measured as a delta rather than against an absolute number. The queue
    # covers every fact this owner has, including the documents earlier stages
    # attached, so any fixed expectation here would be a count of the whole
    # file's history and would drift the moment a stage was added. What the
    # batching actually promises is causal: citing a document that is already
    # being resolved costs nothing extra.
    more = [_fact(cur, subject, value=str(500 + i), fact_type=f"batch2_{i}")
            for i in range(batch)]
    for fact_id in more:
        facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                            source_ref=ref, actor_user_id=OWNER)
    conn.commit()

    after = _CountingCursor(cur)
    review.review_queue(after, owner_user_id=OWNER, at=NOW)
    check("doubling the facts citing one document adds no document probes",
          after.count(documents.DOCUMENTS_TABLE) == doc_reads,
          f"{doc_reads} -> {after.count(documents.DOCUMENTS_TABLE)} "
          f"after {len(more)} more facts")
    check("and still costs exactly one evidence read",
          after.count(schema.FACT_EVIDENCE_TABLE) == 1,
          f"{after.count(schema.FACT_EVIDENCE_TABLE)} read(s)")
    check("the probes are proportional to distinct sources, not to facts",
          doc_reads <= 2 * len(set(
              str(dict(r)["source_ref"]) for r in _live_refs(cur))),
          f"{doc_reads} probe(s)")

    counter = _CountingCursor(cur)
    review.review_summary(counter, owner_user_id=OWNER, at=NOW)
    check("the summary costs one evidence read too, not one per reason",
          counter.count(schema.FACT_EVIDENCE_TABLE) == 1,
          f"{counter.count(schema.FACT_EVIDENCE_TABLE)} read(s)")


def stage_degrading_never_invents_work():
    """An unreadable evidence table reports nothing missing, not everything.

    The tempting failure mode is to treat "I could not check the sources" as
    "the sources are gone". That puts SOURCE_UNAVAILABLE next to documents
    sitting right there, at 60 points, near the top of every member's queue at
    once — an infrastructure problem dressed as a data problem, and a queue
    that cries wolf about missing evidence is one whose genuine missing-evidence
    items get scrolled past.
    """
    print("\n[degradation]")
    conn, cur = cursor()

    broken = _CountingCursor(cur, fail_on=schema.FACT_EVIDENCE_TABLE)
    items = review.review_queue(broken, owner_user_id=OWNER, at=NOW)
    check("the queue still returns results when evidence is unreadable",
          bool(items))
    check("and flags nothing as SOURCE_UNAVAILABLE",
          all(model.REVIEW_SOURCE_UNAVAILABLE not in i["reasons"]
              for i in items),
          str([i["fact_id"] for i in items
               if model.REVIEW_SOURCE_UNAVAILABLE in i["reasons"]]))

    healthy = _queue(cur)
    check("the same read is not degraded when the table is readable",
          any(model.REVIEW_SOURCE_UNAVAILABLE in i["reasons"]
              for i in healthy),
          "the fixture from the vanished-source stage should still be flagged")

    check("a fact with no evidence at all is never SOURCE_UNAVAILABLE",
          model.REVIEW_SOURCE_UNAVAILABLE not in review.review_reasons(
              {"observed_at": _iso(400),
               "provenance_type": model.PROVENANCE_USER_ASSERTED}, at=NOW))


def stage_the_queue_is_bounded():
    """Reads stay bounded however much the member has stored.

    Same rule as every other read in this package: an unbounded read of a
    private store is a full export waiting for one caller to forget a limit.

    The volume is built here rather than assumed. A ceiling assertion made
    against a store holding fewer rows than the ceiling passes whether or not
    the clamp exists, which is a test that reports on the size of its own
    fixture and nothing else.
    """
    print("\n[bounds]")
    conn, cur = cursor()
    subject = _subject()
    surplus = review.MAX_REVIEW_ITEMS + 10
    for i in range(surplus):
        _fact(cur, subject, value=str(1000 + i), fact_type=f"bulk_{i}",
              observed_at=_iso(400))
    conn.commit()

    flagged = [r for r in facts.list_facts(cur, owner_user_id=OWNER, limit=500)
               if review.review_reasons(r, at=NOW)]
    check("there are now more facts needing review than the ceiling allows",
          len(flagged) > review.MAX_REVIEW_ITEMS,
          f"{len(flagged)} flagged vs ceiling {review.MAX_REVIEW_ITEMS}")

    check("the default page stops at the ceiling",
          len(_queue(cur)) == review.MAX_REVIEW_ITEMS,
          str(len(_queue(cur))))
    check("and a caller asking for ten thousand still gets the ceiling",
          len(_queue(cur, limit=10_000)) == review.MAX_REVIEW_ITEMS,
          str(len(_queue(cur, limit=10_000))))
    check("a negative limit returns nothing rather than everything",
          _queue(cur, limit=-5) == [])
    check("the scan ceiling cannot be raised by the caller either",
          review.MAX_REVIEW_SCAN <= 500)
    check("a small scan returns at most what it looked at",
          len(_queue(cur, scan=5)) <= 5, str(len(_queue(cur, scan=5))))

    summary = review.review_summary(cur, owner_user_id=OWNER, at=NOW)
    check("the summary admits when it has been truncated",
          summary["truncated"] is True and summary["total"]
          == review.MAX_REVIEW_ITEMS, str(summary["total"]))

    # The truncated page keeps the *strongest* items, not the first ones the
    # database happened to return. A ceiling that cut the queue before ranking
    # would quietly drop the contradiction that this whole ordering exists to
    # surface, and it would do so only on the stores large enough to need it.
    truncated = _queue(cur)
    survived = {item["primary_reason"] for item in truncated}
    check("and what survives truncation is the top of the ranking",
          truncated[0]["primary_reason"] == model.REVIEW_CONTRADICTED
          and model.REVIEW_SOURCE_UNAVAILABLE in survived,
          f"top={truncated[0]['primary_reason']} kinds={sorted(survived)}")
    check("the sixty bulk stale rows did not crowd out the urgent ones",
          survived != {model.REVIEW_STALE}, str(sorted(survived)))


def stage_the_queue_decides_nothing():
    """Reading the queue changes no fact.

    The package's central rule, applied to the one module most tempted to break
    it: a review queue that auto-retired what it flagged would be the engine
    deciding what is true, wearing a different name. Nothing here writes — and
    ``review.py`` is deliberately absent from the write boundary's list of
    writer modules, which is the machine-checked half of this claim.
    """
    print("\n[read-only]")
    conn, cur = cursor()
    before = {
        int(row["id"]): (row.get("lifecycle_state"),
                         row.get("verification_state"),
                         row.get("provenance_type"),
                         row.get("conflict_id"))
        for row in facts.list_facts(cur, owner_user_id=OWNER, limit=500,
                                    include_superseded=True)
    }
    check("there are facts to disturb", bool(before))

    review.review_queue(cur, owner_user_id=OWNER, at=NOW)
    review.review_summary(cur, owner_user_id=OWNER, at=NOW)
    conn.commit()

    after = {
        int(row["id"]): (row.get("lifecycle_state"),
                         row.get("verification_state"),
                         row.get("provenance_type"),
                         row.get("conflict_id"))
        for row in facts.list_facts(cur, owner_user_id=OWNER, limit=500,
                                    include_superseded=True)
    }
    check("reading the queue changed no fact", before == after,
          str({k: (before.get(k), after.get(k))
               for k in set(before) | set(after)
               if before.get(k) != after.get(k)}))


_STATE: dict[str, int] = {}


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    schema.reset_schema_cache()
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    conn.commit()

    stage_every_reason_is_reachable()
    stage_the_score_is_a_maximum_not_a_sum()
    stage_every_item_explains_itself()
    stage_a_settled_conflict_leaves_the_queue()
    stage_a_decision_about_other_rows_clears_nothing()
    stage_a_vanished_source_is_flagged()
    stage_only_active_facts_are_queued()
    stage_the_queue_is_owner_scoped()
    stage_the_order_is_total_and_stable()
    stage_filtering_narrows_without_reordering()
    stage_the_summary_agrees_with_the_queue()
    stage_the_source_check_stays_batched()
    stage_degrading_never_invents_work()
    stage_the_queue_is_bounded()
    stage_the_queue_decides_nothing()

    print()
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — the queue ranks by its strongest reason and says which.")
    return 0


def test_private_review_queue():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
