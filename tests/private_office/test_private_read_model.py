"""The bounded read model — the shapes the Private Office screens render.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_read_model.py
    python tests/private_office/test_private_read_model.py

What these tests are actually defending
---------------------------------------
* **Exact counts and observed counts stay distinguishable.** ``total`` describes
  the store; everything under ``observed`` describes the rows that were read.
  A caller drawing "38% of your facts are unverified" from a bounded scan and
  labelling it with the total is the failure this split exists to prevent, and
  the ``complete`` flag is the only thing standing between the two.
* **Expiry is computed, not read.** A fact whose stored column still says
  OWNER_CONFIRMED but whose horizon has passed must be counted as EXPIRED. The
  stored column is the tempting thing to ``GROUP BY``; doing so would give a
  dashboard that disagrees with every detail screen in the product.
* **Paging is honest and lossless.** ``has_more`` is established by over-reading
  by one, and walking the pages must visit every fact exactly once — no row
  skipped at a boundary, none served twice.
* **Absent and forbidden are indistinguishable.** ``fact_detail`` returns
  ``None`` for another owner's fact and for one that does not exist. A
  distinguishable refusal confirms an id exists, which is the whole of what a
  prober wants.
* **Unknown is not missing.** A source nobody resolved reports ``available:
  None``; only a source that was checked and found gone reports ``False``. An
  unreadable evidence table reports ``unavailable`` rather than an empty list,
  because "you have cited nothing" is a statement about the member's store.
* **The timeline follows the chain.** A corrected fact's history begins when the
  correction was written, so a timeline scoped to one row answers a different
  question than the one being asked.
* **The read model decides nothing.** Every function here is exercised and the
  table row counts must be identical afterwards.
"""

import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_read_model_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import read_model  # noqa: E402
from services.private_office import review  # noqa: E402
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

    Several stages read the whole owner's store, so two stages sharing a subject
    would let one stage's rows appear as another's conflicts. Isolation is
    per-subject rather than per-owner because owner isolation is itself under
    test and must not be spent on hygiene.
    """
    global _SUBJECT
    _SUBJECT += 1
    return f"rm{_SUBJECT}"


def _fact(cur, subject: str, owner: int = OWNER, *, value: object = "100",
          fact_type: str = "estimated_value", **kwargs) -> int:
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("observed_at", _iso(1))
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject,
        fact_type=fact_type, value=value, value_type=model.VALUE_MONEY,
        actor_user_id=owner, **kwargs)["fact_id"]


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


def _stored_state(cur, fact_id: int) -> str:
    """The verification column as the database holds it, read directly.

    Used only to prove that the computed view differs from the stored one. If
    this were derived through ``verification_status`` the comparison would be
    against itself.
    """
    cur.execute(f"SELECT verification_state FROM {schema.FACTS_TABLE} "
                f"WHERE id = ?", (fact_id,))
    return str(dict(cur.fetchone())["verification_state"])


def _row_counts(cur) -> dict[str, int]:
    counts = {}
    for table in (schema.FACTS_TABLE, schema.FACT_HISTORY_TABLE,
                  schema.FACT_EVIDENCE_TABLE, schema.FACT_CONFLICTS_TABLE):
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = int(dict(cur.fetchone())["n"])
    return counts


class _CountingCursor:
    """A cursor that records the SQL it is asked to run.

    Delegating rather than mocking. The queries under inspection are the ones
    the real code issues against a real database, so the only thing substituted
    is the bookkeeping — a fake cursor would let a batching assertion pass
    against a query shape the database has never seen.
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
def stage_exact_counts_describe_the_whole_store():
    """``total``, ``by_domain`` and ``by_provenance`` must agree with each other.

    They are three answers to the same question computed by three different
    queries, so they are exactly the kind of thing that drifts silently: a
    predicate added to one and not the others produces a dashboard whose
    sections quietly stop summing, and nobody notices until a member counts.
    """
    print("\n[exact counts]")
    conn, cur = cursor()
    subject = _subject()

    for i, domain in enumerate((model.DOMAIN_FINANCIAL, model.DOMAIN_FINANCIAL,
                                model.DOMAIN_LEGAL)):
        _fact(cur, subject, value=str(100 + i), fact_type=f"exact_{i}",
              domain=domain)
    _fact(cur, subject, value="999", fact_type="exact_inferred",
          domain=model.DOMAIN_LEGAL,
          provenance_type=model.PROVENANCE_INFERRED)

    # A superseded row, so the store contains something the histograms are
    # supposed to leave out. Without one, every count here is over an all-ACTIVE
    # store and the three queries agree whether or not any of them filters on
    # lifecycle at all — the sums would balance for the wrong reason, and the
    # first correction a real member makes would unbalance them in production.
    corrected = _fact(cur, subject, value="1", fact_type="exact_corrected",
                      domain=model.DOMAIN_FINANCIAL)
    facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=corrected, value="2",
        value_type=model.VALUE_NUMBER,
        provenance_type=model.PROVENANCE_USER_ASSERTED, actor_user_id=OWNER)
    conn.commit()

    cur.execute(f"SELECT COUNT(*) AS n FROM {schema.FACTS_TABLE} "
                f"WHERE owner_user_id = ? AND lifecycle_state != ?",
                (OWNER, model.LIFECYCLE_ACTIVE))
    check("the fixture really does contain a non-active row",
          int(dict(cur.fetchone())["n"]) >= 1)

    overview = read_model.facts_overview(cur, owner_user_id=OWNER, at=NOW)

    check("the total equals the sum of the domain histogram",
          overview["total"] == sum(overview["by_domain"].values()),
          f"{overview['total']} vs {sum(overview['by_domain'].values())}")
    check("the total equals the sum of the provenance histogram",
          overview["total"] == sum(overview["by_provenance"].values()),
          f"{overview['total']} vs {sum(overview['by_provenance'].values())}")

    # Both histograms are complete maps, so a screen never has to invent a key
    # for a domain or provenance that happens to have no rows today.
    check("every declared domain is present, zeros included",
          set(overview["by_domain"]) == set(model.DOMAINS),
          str(sorted(set(model.DOMAINS) - set(overview['by_domain']))))
    check("every declared provenance type is present, zeros included",
          set(overview["by_provenance"]) == set(model.PROVENANCE_TYPES),
          str(sorted(set(model.PROVENANCE_TYPES) - set(overview['by_provenance']))))

    check("the provenance histogram counts the inferred fact as inferred",
          overview["by_provenance"][model.PROVENANCE_INFERRED] >= 1,
          str(overview["by_provenance"][model.PROVENANCE_INFERRED]))
    check("an owner with no facts gets a complete map of zeros, not an empty one",
          set(read_model.facts_overview(
              cur, owner_user_id=OTHER, at=NOW)["by_domain"]) == set(model.DOMAINS))


def stage_observed_counts_admit_their_bound():
    """A bounded scan must say it was bounded, and say what it covered.

    This is the module's central contract. ``by_verification`` is computed from
    rows in memory and therefore describes only the rows that were read; the
    only thing preventing a caller from rendering it as a proportion of the
    whole store is ``complete``. If ``complete`` ever reports True for a partial
    scan, every percentage in the product silently becomes a percentage of the
    first N rows.
    """
    print("\n[observed counts admit their bound]")
    conn, cur = cursor()
    subject = _subject()

    cur.execute(f"SELECT COUNT(*) AS n FROM {schema.FACTS_TABLE} "
                f"WHERE owner_user_id = ? AND lifecycle_state = ?",
                (OWNER, model.LIFECYCLE_ACTIVE))
    before = int(dict(cur.fetchone())["n"])
    for i in range(6):
        _fact(cur, subject, value=str(200 + i), fact_type=f"bound_{i}")
    conn.commit()
    total = before + 6

    full = read_model.facts_overview(cur, owner_user_id=OWNER, at=NOW)
    check("a scan that reached everything reports complete",
          full["observed"]["complete"] is True,
          f"scanned={full['observed']['scanned']} total={full['total']}")
    check("and it scanned the whole store",
          full["observed"]["scanned"] == total,
          f"{full['observed']['scanned']} vs {total}")
    check("the verification histogram sums to what was scanned, not to the total",
          sum(full["observed"]["by_verification"].values())
          == full["observed"]["scanned"],
          f"{sum(full['observed']['by_verification'].values())} vs "
          f"{full['observed']['scanned']}")

    # The bound biting is the case that matters, and it cannot be exercised
    # without a store larger than the scan — a completeness assertion made
    # against a store smaller than the ceiling passes whether or not the flag
    # is computed at all.
    partial = read_model.facts_overview(cur, owner_user_id=OWNER, scan=3, at=NOW)
    check("a scan that stopped short does not claim to be complete",
          partial["observed"]["complete"] is False,
          f"scanned={partial['observed']['scanned']} total={partial['total']}")
    check("the partial scan still reports the exact total",
          partial["total"] == total, f"{partial['total']} vs {total}")
    check("and its verification histogram sums to the partial scan",
          sum(partial["observed"]["by_verification"].values()) == 3,
          str(sum(partial["observed"]["by_verification"].values())))
    check("so the histogram is visibly smaller than the total it sits beside",
          sum(partial["observed"]["by_verification"].values()) < partial["total"])


def stage_expiry_is_computed_not_read():
    """The headline: a stored badge that has aged out must count as EXPIRED.

    ``GROUP BY verification_state`` is the obvious way to build this histogram
    and it is wrong, because nothing ever writes EXPIRED — the state is derived
    at read from ``verified_at`` plus the horizon. A dashboard built on the
    stored column would report a confidently verified store while every detail
    screen in the product showed the same facts as expired.
    """
    print("\n[expiry is computed]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="500", fact_type="expiry_probe")
    # A positive verification is not something a fixture can simply assert:
    # `set_verification` refuses OWNER_CONFIRMED without a live supporting
    # source, on the grounds that a confirmed badge with nothing behind it is
    # the exact claim this package will not let anything make. So the evidence
    # comes first, and the refusal is worth leaning on rather than working
    # around — it means the row this stage ages out is a legitimately
    # verified one.
    doc = _document(cur, OWNER, "what the confirmation rests on")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                        source_ref=evidence.format_ref("document", doc),
                        actor_user_id=OWNER)
    facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=fact_id,
        verification_state=model.VERIFICATION_OWNER_CONFIRMED,
        actor_user_id=OWNER)
    conn.commit()

    stored = _stored_state(cur, fact_id)
    check("the database still stores OWNER_CONFIRMED",
          stored == model.VERIFICATION_OWNER_CONFIRMED, stored)

    horizon = model.VERIFICATION_HORIZON_DAYS.get(
        model.VERIFICATION_OWNER_CONFIRMED, 0)
    check("OWNER_CONFIRMED has a horizon at all, or this stage proves nothing",
          horizon > 0, str(horizon))

    fresh_moment = facts._now() + timedelta(days=1)
    aged_moment = facts._now() + timedelta(days=horizon + 5)

    fresh = read_model.facts_overview(cur, owner_user_id=OWNER, at=fresh_moment)
    aged = read_model.facts_overview(cur, owner_user_id=OWNER, at=aged_moment)

    check("before the horizon it counts as OWNER_CONFIRMED",
          fresh["observed"]["by_verification"][
              model.VERIFICATION_OWNER_CONFIRMED] >= 1,
          str(fresh["observed"]["by_verification"]))
    check("after the horizon it counts as EXPIRED instead",
          aged["observed"]["by_verification"][model.VERIFICATION_EXPIRED]
          > fresh["observed"]["by_verification"][model.VERIFICATION_EXPIRED],
          f"{fresh['observed']['by_verification'][model.VERIFICATION_EXPIRED]}"
          f" -> {aged['observed']['by_verification'][model.VERIFICATION_EXPIRED]}")
    check("and it is no longer counted as confirmed",
          aged["observed"]["by_verification"][
              model.VERIFICATION_OWNER_CONFIRMED]
          < fresh["observed"]["by_verification"][
              model.VERIFICATION_OWNER_CONFIRMED],
          str(aged["observed"]["by_verification"]))
    check("the stored column was not rewritten by reading it",
          _stored_state(cur, fact_id) == model.VERIFICATION_OWNER_CONFIRMED)

    # An expired verification is one of the states the model calls out as
    # needing attention, so the two numbers must move together.
    check("an aged-out badge raises the needs-attention count",
          aged["observed"]["needs_attention"] > fresh["observed"]["needs_attention"],
          f"{fresh['observed']['needs_attention']} ->"
          f" {aged['observed']['needs_attention']}")


def stage_paging_is_honest_and_lossless():
    """Walking the pages must visit every fact exactly once.

    The two ways this breaks are opposite and both look fine from a single
    page: an off-by-one in ``next_offset`` skips the row on a boundary, and an
    over-read that forgets to trim serves it twice. Only walking the whole set
    and comparing against the truth catches either.
    """
    print("\n[paging]")
    conn, cur = cursor()
    subject = _subject()

    made = [_fact(cur, subject, value=str(300 + i), fact_type=f"page_{i}")
            for i in range(7)]
    conn.commit()

    seen: list[int] = []
    offset = 0
    guard = 0
    pages = 0
    while True:
        guard += 1
        if guard > 20:
            break
        page = read_model.facts_page(
            cur, owner_user_id=OWNER, subject_id=subject, subject_type="NODE",
            limit=2, offset=offset, at=NOW)
        pages += 1
        seen.extend(item["fact_id"] for item in page["items"])
        if not page["has_more"]:
            break
        offset = page["next_offset"]

    check("every fact was served exactly once across the pages",
          sorted(seen) == sorted(made),
          f"served {sorted(seen)} vs made {sorted(made)}")
    check("no fact was served twice", len(seen) == len(set(seen)), str(seen))
    check("the walk took the expected number of pages",
          pages == 4, str(pages))

    first = read_model.facts_page(
        cur, owner_user_id=OWNER, subject_id=subject, subject_type="NODE",
        limit=2, offset=0, at=NOW)
    check("a full page that has successors says so", first["has_more"] is True)
    check("and points at the next offset",
          first["next_offset"] == 2, str(first["next_offset"]))
    check("a page never returns more rows than asked for",
          len(first["items"]) == 2, str(len(first["items"])))

    last = read_model.facts_page(
        cur, owner_user_id=OWNER, subject_id=subject, subject_type="NODE",
        limit=2, offset=6, at=NOW)
    check("the final partial page reports no successor",
          last["has_more"] is False and last["next_offset"] is None,
          f"has_more={last['has_more']} next={last['next_offset']}")

    # A page sized exactly to the remainder is the case an over-read gets
    # wrong: there is nothing after it, but the extra row it fetched to find
    # out must not be shown.
    exact = read_model.facts_page(
        cur, owner_user_id=OWNER, subject_id=subject, subject_type="NODE",
        limit=7, offset=0, at=NOW)
    check("a page sized to the exact remainder claims no successor",
          exact["has_more"] is False and len(exact["items"]) == 7,
          f"has_more={exact['has_more']} n={len(exact['items'])}")

    check("the page size is clamped to the ceiling",
          read_model.facts_page(cur, owner_user_id=OWNER, limit=10 ** 6,
                                at=NOW)["limit"] == read_model.MAX_PAGE)


def stage_the_list_row_is_a_shape_not_a_dump():
    """A list row carries what the screen needs and nothing it inherits.

    Forwarding the raw database row would mean the endpoint gains a field every
    time the table does — including, eventually, one nobody meant to put on a
    screen. The owner id is the specific thing checked for: it is on every row,
    it is never needed by a caller that already had to name the owner to get
    here, and it is the field an export would most want.
    """
    print("\n[list row shape]")
    conn, cur = cursor()
    subject = _subject()
    _fact(cur, subject, value="777", fact_type="shape_probe")
    conn.commit()

    page = read_model.facts_page(
        cur, owner_user_id=OWNER, subject_id=subject, subject_type="NODE",
        at=NOW)
    check("the page returned the fact", len(page["items"]) == 1,
          str(len(page["items"])))
    row = page["items"][0]

    check("the list row does not carry the owner id",
          "owner_user_id" not in row, str(sorted(row)))
    check("nor the raw encoded provenance ref",
          "provenance_ref" not in row, str(sorted(row)))
    check("it does carry the computed verification view",
          isinstance(row.get("verification"), dict)
          and "effective_state" in row["verification"])
    check("and the computed freshness view",
          isinstance(row.get("freshness"), dict) and "stale" in row["freshness"])
    check("the fact id is exposed under a name a route can use",
          isinstance(row.get("fact_id"), int) and row["fact_id"] > 0)


def stage_detail_cannot_distinguish_absent_from_forbidden():
    """Another owner's fact and a fact that never existed answer identically.

    A refusal that differs from a miss is an existence oracle: a prober walks
    the id space and learns which ids are real without ever reading one.
    """
    print("\n[detail refuses uniformly]")
    conn, cur = cursor()
    subject = _subject()
    mine = _fact(cur, subject, value="10", fact_type="detail_mine")
    theirs = _fact(cur, _subject(), owner=OTHER, value="20",
                   fact_type="detail_theirs")
    conn.commit()

    check("my own fact resolves",
          read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=mine)
          is not None)
    check("another owner's fact reads as absent",
          read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=theirs)
          is None)
    check("a fact id that was never issued reads as absent",
          read_model.fact_detail(cur, owner_user_id=OWNER,
                                 fact_id=99_000_111) is None)
    check("and the two refusals are the same value",
          read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=theirs)
          == read_model.fact_detail(cur, owner_user_id=OWNER,
                                    fact_id=99_000_111))
    check("a nonsense owner reads nothing",
          read_model.fact_detail(cur, owner_user_id=0, fact_id=mine) is None)


def stage_detail_reads_the_resolution_not_the_marker():
    """A settled conflict must stop showing as contradicted.

    ``mark_conflicts`` stamps ``conflict_id`` and never clears it, so a detail
    screen that treated the marker as the verdict would show CONTRADICTED
    forever — the member resolves the disagreement and the badge never moves.
    """
    print("\n[detail consults the resolution]")
    conn, cur = cursor()
    subject = _subject()

    ids = []
    for offset, value in zip(("00", "06"), (0.35, 0.40)):
        stamp = f"2026-05-20T{offset}:00:00Z"
        ids.append(facts.record_fact(
            cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
            fact_type="ownership_share", value_type=model.VALUE_PERCENT,
            value=value, provenance_type=model.PROVENANCE_USER_ASSERTED,
            valid_from=stamp, observed_at=stamp, actor_user_id=OWNER,
        )["fact_id"])
    found = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=subject)
    check("the fixture produced exactly one conflict", len(found) == 1,
          str(len(found)))
    contradictions.mark_conflicts(cur, owner_user_id=OWNER, conflicts=found)
    conn.commit()

    before = read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=ids[0],
                                    at=NOW)
    check("an unsettled disagreement shows as an open conflict",
          before["open_conflict"] is True)
    check("and the review reasons say why",
          model.REVIEW_CONTRADICTED in before["review_reasons"],
          str(before["review_reasons"]))

    contradictions.resolve_conflict(
        cur, owner_user_id=OWNER, conflict_id=found[0]["conflict_id"],
        outcome=model.RESOLUTION_SEPARATED, competing_fact_ids=ids,
        actor_user_id=OWNER, purpose="user_request")
    conn.commit()

    after = read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=ids[0],
                                   at=NOW)
    check("the stamped marker is still on the row",
          bool(after["conflict_id"]), str(after["conflict_id"]))
    check("but the settled disagreement no longer reads as open",
          after["open_conflict"] is False)
    check("and CONTRADICTED has left the reasons",
          model.REVIEW_CONTRADICTED not in after["review_reasons"],
          str(after["review_reasons"]))


def stage_an_unresolved_source_is_unknown_not_missing():
    """``available`` distinguishes checked-and-gone from never-checked.

    False means somebody looked and the document was not there. ``None`` means
    nobody could look: ``evidence.normalize_refs`` silently drops a ref whose
    kind it does not recognise, so the resolver never returns a verdict for it.
    Collapsing the two would render a citation this build cannot parse as a
    document the member has lost — and a screen that cries wolf about evidence
    is one whose genuine missing-evidence warnings get dismissed.

    That third case is not hypothetical, but it is unreachable through the
    writers: ``link_evidence`` validates the kind, which is what makes it a
    writer. It arrives from outside — the coming legacy backfill reads tables
    this package did not write, and an unrecognised citation kind is exactly
    what turns up first. Hence the raw insert below. Producing a row the
    writers exist to reject is the point of the test, not a way around the
    write boundary, and it is registered as such in DAMAGE_FIXTURES.
    """
    print("\n[unknown is not missing]")
    conn, cur = cursor()
    subject = _subject()

    fact_id = _fact(cur, subject, value="42", fact_type="source_probe")
    live = _document(cur, OWNER, "still here")
    doomed = _document(cur, OWNER, "about to vanish")
    for doc in (live, doomed):
        facts.link_evidence(
            cur, owner_user_id=OWNER, fact_id=fact_id,
            source_ref=evidence.format_ref("document", doc),
            actor_user_id=OWNER)
    conn.commit()

    detail = read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=fact_id,
                                    at=NOW)
    check("both sources resolve while both documents exist",
          all(entry["available"] is True for entry in detail["evidence"]),
          str([e["available"] for e in detail["evidence"]]))
    check("and nothing is reported missing",
          detail["missing_sources"] == [], str(detail["missing_sources"]))

    documents.delete_document(cur, owner_user_id=OWNER, document_id=doomed,
                              actor_user_id=OWNER)
    conn.commit()

    after = read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=fact_id,
                                   at=NOW)
    gone_ref = evidence.format_ref("document", doomed)
    check("the deleted document now resolves as unavailable",
          after["missing_sources"] == [gone_ref], str(after["missing_sources"]))
    check("the surviving document is still available",
          any(e["available"] is True for e in after["evidence"]),
          str([(e["source_ref"], e["available"]) for e in after["evidence"]]))
    check("a vanished source shows up in the review reasons",
          model.REVIEW_SOURCE_UNAVAILABLE in after["review_reasons"],
          str(after["review_reasons"]))

    # Now the third value. A ref naming a kind the resolver has never heard of
    # cannot be produced by link_evidence, so it goes in underneath.
    stamp = _iso()
    cur.execute(
        f"""INSERT INTO {schema.FACT_EVIDENCE_TABLE}
        (owner_user_id, fact_id, source_ref, source_kind, relation,
         note_key, linked_by, linked_at, detached_at, detached_by, created_at)
        VALUES (?, ?, ?, ?, 'SUPPORTS', '', ?, ?, '', 0, ?)""",
        (OWNER, fact_id, "ledger:7", "ledger", OWNER, stamp, stamp))
    conn.commit()

    opaque = read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=fact_id,
                                    at=NOW)
    unknown = [e for e in opaque["evidence"] if e["source_ref"] == "ledger:7"]
    check("an unparseable citation is still listed", len(unknown) == 1,
          str([e["source_ref"] for e in opaque["evidence"]]))
    check("and its availability is unknown, not false",
          unknown and unknown[0]["available"] is None,
          str(unknown))

    # The whole reason the tri-state earns its keep: None must not leak into
    # the two places a False is consumed as a statement about the member.
    check("a ref nobody could check is not reported as a missing document",
          "ledger:7" not in opaque["missing_sources"],
          str(opaque["missing_sources"]))
    check("the still-missing document is unaffected by the unreadable one",
          opaque["missing_sources"] == [gone_ref],
          str(opaque["missing_sources"]))

    # All three values reachable in one payload, so no check above can be
    # satisfied by a collapse in either direction.
    values = {e["available"] for e in opaque["evidence"]}
    check("availability is a tri-state, not a boolean wearing a mask",
          values == {True, False, None}, str(values))


def stage_the_timeline_follows_the_chain():
    """A corrected fact's timeline must reach back past the correction.

    The current row's own history starts the moment it was written. A member
    asking "when did I first learn this" is asking about the chain, and the two
    answers differ by exactly the interval they care about.
    """
    print("\n[timeline follows the chain]")
    conn, cur = cursor()
    subject = _subject()

    original = _fact(cur, subject, value="1000", fact_type="chain_probe")
    conn.commit()
    replacement = facts.supersede_fact(
        cur, owner_user_id=OWNER, fact_id=original, value="2000",
        value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        actor_user_id=OWNER, purpose="user_request")["fact_id"]
    conn.commit()

    timeline = read_model.fact_timeline(
        cur, owner_user_id=OWNER, fact_id=replacement)

    check("the chain names both the old fact and the new one",
          original in timeline["chain"] and replacement in timeline["chain"],
          str(timeline["chain"]))
    touched = {event["fact_id"] for event in timeline["events"]}
    check("the timeline carries events from the superseded predecessor",
          original in touched, str(sorted(touched)))
    check("and events belonging to the current fact are marked as such",
          any(e["is_this_fact"] for e in timeline["events"]),
          str([(e["fact_id"], e["is_this_fact"]) for e in timeline["events"]]))
    check("events about the predecessor are not marked as this fact",
          all(not e["is_this_fact"] for e in timeline["events"]
              if e["fact_id"] == original))

    stamps = [e["at"] for e in timeline["events"] if e["at"]]
    check("the timeline is newest first",
          stamps == sorted(stamps, reverse=True), str(stamps))
    check("an untruncated timeline says so",
          timeline["truncated"] is False)

    bounded = read_model.fact_timeline(
        cur, owner_user_id=OWNER, fact_id=replacement, limit=1)
    check("a bound that bit is reported rather than hidden",
          bounded["truncated"] is True and len(bounded["events"]) == 1,
          f"truncated={bounded['truncated']} n={len(bounded['events'])}")

    check("another owner's fact has no timeline",
          read_model.fact_timeline(
              cur, owner_user_id=OTHER, fact_id=replacement)["events"] == [])


def stage_sources_group_by_document_not_by_citation():
    """The source list's unit is the document, and probing scales with it.

    A document supporting eleven facts is the one worth checking; the same
    document listed eleven times is a list nobody reads. The resolution cost
    must scale with distinct documents rather than citations, or the screen
    most likely to be opened against a large store is the one with the N+1.
    """
    print("\n[sources group by document]")
    conn, cur = cursor()
    subject = _subject()

    shared = _document(cur, OWNER, "the one everything cites")
    shared_ref = evidence.format_ref("document", shared)

    # Sized to straddle the resolver's own chunk boundary. Below
    # ``evidence.MAX_REFS`` every arrangement fits in one chunk and
    # ``resolve_refs`` dedups within it, so grouping per citation rather than
    # per document would look identical from outside — accidental batching
    # that comes apart at exactly the volume where it starts to matter.
    batch = evidence.MAX_REFS + 4
    for i in range(batch):
        fact_id = _fact(cur, subject, value=str(400 + i),
                        fact_type=f"cite_{i}")
        facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                            source_ref=shared_ref, actor_user_id=OWNER)
    conn.commit()

    counting = _CountingCursor(cur)
    index = read_model.source_index(counting, owner_user_id=OWNER)

    entry = next((s for s in index["sources"] if s["source_ref"] == shared_ref),
                 None)
    check("the shared document appears exactly once", entry is not None
          and sum(1 for s in index["sources"]
                  if s["source_ref"] == shared_ref) == 1)
    check(f"and it reports all {batch} facts resting on it",
          entry["fact_count"] == batch, str(entry["fact_count"]))
    check("the most-relied-upon source sorts first",
          index["sources"][0]["source_ref"] == shared_ref,
          str(index["sources"][0]))

    probes = counting.count(documents.DOCUMENTS_TABLE)
    check("resolving cost far fewer probes than there were citations",
          probes < batch, f"{probes} probe(s) for {batch} citation(s)")

    # The causal form of the same claim: adding citations of a document already
    # in the list must not add probes, whatever the absolute number is.
    for i in range(batch):
        fact_id = _fact(cur, subject, value=str(900 + i),
                        fact_type=f"recite_{i}")
        facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                            source_ref=shared_ref, actor_user_id=OWNER)
    conn.commit()

    again = _CountingCursor(cur)
    read_model.source_index(again, owner_user_id=OWNER)
    check(f"doubling the citations of one document adds no document probes",
          again.count(documents.DOCUMENTS_TABLE) == probes,
          f"{probes} -> {again.count(documents.DOCUMENTS_TABLE)}")

    # The tri-state again, at the second call site that claims it. The earlier
    # stage left this store holding all three kinds of source: one alive, one
    # deleted, and one whose kind the resolver cannot parse. A source list that
    # collapsed unknown into False would tell the member a citation this build
    # merely fails to understand is a document they have lost — and the source
    # list is precisely the screen they would open to go looking for it.
    states = {s["source_ref"]: s["available"] for s in index["sources"]}
    check("the shared document reads available", states.get(shared_ref) is True,
          str(states.get(shared_ref)))
    check("a deleted document reads unavailable",
          any(v is False for v in states.values()), str(states))
    check("an unparseable citation reads unknown",
          states.get("ledger:7", "absent") is None,
          str(states.get("ledger:7", "absent")))

    check("the source list is owner-scoped",
          read_model.source_index(cur, owner_user_id=OTHER)["sources"] == [])


def stage_an_unreadable_evidence_table_is_not_an_empty_one():
    """A failed read must not render as "you have cited nothing".

    Degrading in the safe direction here means refusing to make a claim about
    the member's store on the strength of a schema problem. An empty list is a
    statement; ``unavailable`` is an admission.
    """
    print("\n[unreadable is not empty]")
    conn, cur = cursor()
    subject = _subject()
    fact_id = _fact(cur, subject, value="55", fact_type="degrade_probe")
    doc = _document(cur, OWNER, "cited once")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                        source_ref=evidence.format_ref("document", doc),
                        actor_user_id=OWNER)
    conn.commit()

    healthy = read_model.source_index(cur, owner_user_id=OWNER)
    check("the source is listed while the table is readable",
          len(healthy["sources"]) >= 1, str(len(healthy["sources"])))
    check("and a healthy read is not flagged unavailable",
          "unavailable" not in healthy, str(sorted(healthy)))

    broken = _CountingCursor(cur, fail_on=schema.FACT_EVIDENCE_TABLE)
    degraded = read_model.source_index(broken, owner_user_id=OWNER)
    check("a failed read returns no sources",
          degraded["sources"] == [], str(degraded["sources"]))
    check("but says the answer is unavailable rather than empty",
          degraded.get("unavailable") is True, str(sorted(degraded)))
    check("the two results are distinguishable by a caller",
          degraded != healthy)


def stage_expiring_includes_what_already_expired():
    """A window that closed last month is the most overdue item, not the least.

    Dropping it for being past the horizon would remove exactly the rows most
    in need of attention — and it would put the list out of step with the review
    queue, which flags them. A member told to act with nowhere to act is the
    result.
    """
    print("\n[expiring]")
    conn, cur = cursor()
    subject = _subject()

    # Each window opens well before it closes. The store rejects a `valid_to`
    # that precedes its `valid_from`, which is worth stating: the fixture is
    # not free to describe an impossible window just because the assertion it
    # wants is about the closing end.
    opened = _iso(400)
    closed = _fact(cur, subject, value="1", fact_type="expiring_closed",
                   valid_from=opened, valid_to=_iso(45), allow_backfill=True)
    soon = _fact(cur, subject, value="2", fact_type="expiring_soon",
                 valid_from=opened, valid_to=_iso(-10), allow_backfill=True)
    distant = _fact(cur, subject, value="3", fact_type="expiring_distant",
                    valid_from=opened, valid_to=_iso(-365 * 4),
                    allow_backfill=True)
    never = _fact(cur, subject, value="4", fact_type="expiring_never")
    conn.commit()

    items = read_model.expiring_facts(cur, owner_user_id=OWNER, at=NOW)
    ids = [item["fact_id"] for item in items]

    check("a window that already closed is included", closed in ids, str(ids))
    check("a window closing soon is included", soon in ids, str(ids))
    check("a window years away is not", distant not in ids, str(ids))
    check("a fact with no window at all is not", never not in ids, str(ids))

    # Everything below describes the closed and soon items, so it is read
    # defensively rather than with `.index` and `next`. If a regression drops
    # one of them the membership checks above have already recorded it, and the
    # stage's job is then to finish and report — a ValueError here would replace
    # a legible list of failed guarantees with a stack trace naming none of them.
    by_id = {item["fact_id"]: item for item in items}
    closed_at = ids.index(closed) if closed in ids else None
    soon_at = ids.index(soon) if soon in ids else None
    check("the already-closed item sorts ahead of the merely soon one",
          closed_at is not None and soon_at is not None and closed_at < soon_at,
          str(ids))
    check("and it is labelled as already expired",
          by_id.get(closed, {}).get("already_expired") is True,
          str(by_id.get(closed)))
    check("while the upcoming one is not",
          by_id.get(soon, {}).get("already_expired") is False,
          str(by_id.get(soon)))
    remaining = by_id.get(closed, {}).get("days_remaining")
    check("days remaining is negative for a window that has closed",
          remaining is not None and remaining < 0, str(remaining))

    # Cross-module agreement. The review queue flags VALIDITY_ENDING using its
    # own horizon; anything it flags must be findable here, or the member is
    # told to act on a list that does not contain the item.
    flagged = {item["fact_id"] for item in review.review_queue(
        cur, owner_user_id=OWNER, at=NOW,
        reasons=[model.REVIEW_VALIDITY_ENDING])}
    reachable = {item["fact_id"] for item in read_model.expiring_facts(
        cur, owner_user_id=OWNER, within_days=read_model.MAX_EXPIRING_DAYS,
        at=NOW)}
    check("every fact the review queue flags as expiring is in the list",
          flagged <= reachable, f"unreachable: {sorted(flagged - reachable)}")

    check("the horizon is clamped rather than honoured unbounded",
          all(i["fact_id"] != distant for i in read_model.expiring_facts(
              cur, owner_user_id=OWNER, within_days=10 ** 6, at=NOW)),
          "a four-year window was returned by an absurd horizon")


def stage_every_read_is_owner_scoped():
    """No surface here answers about a store the caller does not own."""
    print("\n[owner scoping]")
    conn, cur = cursor()
    subject = _subject()
    mine = _fact(cur, subject, value="123", fact_type="scope_probe")
    conn.commit()

    other_overview = read_model.facts_overview(cur, owner_user_id=OTHER, at=NOW)
    mine_overview = read_model.facts_overview(cur, owner_user_id=OWNER, at=NOW)
    check("the other owner's total does not include my facts",
          other_overview["total"] < mine_overview["total"],
          f"{other_overview['total']} vs {mine_overview['total']}")

    other_page = read_model.facts_page(cur, owner_user_id=OTHER, at=NOW)
    check("the other owner's page contains none of my facts",
          all(item["fact_id"] != mine for item in other_page["items"]))
    check("the other owner's expiring list contains none of my facts",
          all(item["fact_id"] != mine for item in read_model.expiring_facts(
              cur, owner_user_id=OTHER, at=NOW)))
    check("the other owner's timeline for my fact is empty",
          read_model.fact_timeline(
              cur, owner_user_id=OTHER, fact_id=mine)["events"] == [])

    for name, value in (
            ("overview", read_model.facts_overview(cur, owner_user_id=0, at=NOW)),
            ("page", read_model.facts_page(cur, owner_user_id=0, at=NOW)),
            ("conflicts", read_model.open_conflicts(cur, owner_user_id=0))):
        check(f"a zero owner gets an empty {name}",
              value.get("total", 0) == 0 and not value.get("items")
              and not value.get("conflicts"))
    check("a zero owner gets an empty expiring list",
          read_model.expiring_facts(cur, owner_user_id=0, at=NOW) == [])
    check("a zero owner gets an empty source index",
          read_model.source_index(cur, owner_user_id=0)["sources"] == [])


def stage_the_read_model_decides_nothing():
    """Exercising every surface must leave the store byte-for-byte unchanged.

    The package's central rule is that it never decides what is true on its
    own. A read model that corrected what it noticed — stamping EXPIRED,
    clearing a resolved marker, archiving a vanished source — would be exactly
    that decision, taken on a screen refresh, by the one component nobody thinks
    of as a writer.
    """
    print("\n[the read model decides nothing]")
    conn, cur = cursor()
    subject = _subject()
    fact_id = _fact(cur, subject, value="88", fact_type="readonly_probe")
    doc = _document(cur, OWNER, "read only")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=fact_id,
                        source_ref=evidence.format_ref("document", doc),
                        actor_user_id=OWNER)
    conn.commit()

    before = _row_counts(cur)

    read_model.facts_overview(cur, owner_user_id=OWNER, at=NOW)
    read_model.facts_page(cur, owner_user_id=OWNER, at=NOW)
    read_model.fact_detail(cur, owner_user_id=OWNER, fact_id=fact_id, at=NOW)
    read_model.fact_timeline(cur, owner_user_id=OWNER, fact_id=fact_id)
    read_model.source_index(cur, owner_user_id=OWNER)
    read_model.expiring_facts(cur, owner_user_id=OWNER, at=NOW)
    read_model.open_conflicts(cur, owner_user_id=OWNER)
    # Deliberately at a moment past every horizon, so every derived flag the
    # module can compute is in its alarming state. If anything here were going
    # to persist a verdict, this is the call that would do it.
    read_model.facts_overview(cur, owner_user_id=OWNER,
                              at=facts._now() + timedelta(days=4000))
    conn.commit()

    after = _row_counts(cur)
    check("no table gained or lost a row", before == after,
          f"{before} -> {after}")


def stage_the_bounds_are_published_and_enforced():
    """Every ceiling is a real clamp, not a default a caller can argue past."""
    print("\n[bounds]")
    conn, cur = cursor()

    check("the overview scan is clamped",
          read_model.facts_overview(
              cur, owner_user_id=OWNER, scan=10 ** 6,
              at=NOW)["observed"]["scanned"] <= read_model.MAX_OVERVIEW_SCAN)
    check("the source index is clamped",
          len(read_model.source_index(
              cur, owner_user_id=OWNER,
              limit=10 ** 6)["sources"]) <= read_model.MAX_SOURCES)
    check("the expiring list is clamped",
          len(read_model.expiring_facts(
              cur, owner_user_id=OWNER, limit=10 ** 6,
              at=NOW)) <= read_model.MAX_EXPIRING)
    check("the timeline is clamped",
          len(read_model.fact_timeline(
              cur, owner_user_id=OWNER, fact_id=1,
              limit=10 ** 6)["events"]) <= read_model.MAX_TIMELINE)
    check("the conflict list is clamped",
          len(read_model.open_conflicts(
              cur, owner_user_id=OWNER,
              limit=10 ** 6)["conflicts"]) <= read_model.MAX_CONFLICT_GROUPS)

    # A negative or unreadable page size must land on the default rather than
    # on zero — a page of nothing is indistinguishable from an empty store.
    check("a negative page size falls back to the default",
          read_model.facts_page(cur, owner_user_id=OWNER, limit=-5,
                                at=NOW)["limit"] == read_model.DEFAULT_PAGE)
    check("an unreadable page size falls back to the default",
          read_model.facts_page(cur, owner_user_id=OWNER, limit="soon",
                                at=NOW)["limit"] == read_model.DEFAULT_PAGE)


def main() -> int:
    print("PRIVATE OFFICE READ MODEL")
    print(f"db: {_TMP_DB}")
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    conn.commit()

    stage_exact_counts_describe_the_whole_store()
    stage_observed_counts_admit_their_bound()
    stage_expiry_is_computed_not_read()
    stage_paging_is_honest_and_lossless()
    stage_the_list_row_is_a_shape_not_a_dump()
    stage_detail_cannot_distinguish_absent_from_forbidden()
    stage_detail_reads_the_resolution_not_the_marker()
    stage_an_unresolved_source_is_unknown_not_missing()
    stage_the_timeline_follows_the_chain()
    stage_sources_group_by_document_not_by_citation()
    stage_an_unreadable_evidence_table_is_not_an_empty_one()
    stage_expiring_includes_what_already_expired()
    stage_every_read_is_owner_scoped()
    stage_the_read_model_decides_nothing()
    stage_the_bounds_are_published_and_enforced()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_read_model():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
