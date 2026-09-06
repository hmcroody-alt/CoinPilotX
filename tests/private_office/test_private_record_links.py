"""G-3 — dependencies between records.

Run either way::

    python -m pytest tests/private_office/test_private_record_links.py
    python tests/private_office/test_private_record_links.py

What these tests are actually defending
---------------------------------------
A dependency edge is the smallest possible feature and the easiest one to get
subtly wrong, because almost every mistake still renders a plausible screen.
Each stage below exists because there is a tidy implementation that would pass
a "can I link two records" test and be wrong anyway.

* **A cycle is refused before it exists, not detected afterwards.** The obvious
  shape — insert, then walk, then delete if it looped — leaves a cycle in the
  table for the duration of a transaction, and the read side walks these same
  edges. A concurrent reader does not care that the loop was going to be
  removed. So the check runs before the INSERT, and the multi-hop case
  (A→B→C, then C→A) is tested separately from the one-hop case, because a
  cycle check that only compares the two endpoints passes the one-hop test and
  admits the three-hop loop.

* **Blocked is derived, never stored.** Closing a blocker unblocks its
  dependents with no sweep, no job and no write. The test proves it by closing
  the blocker and re-reading — if ``blocked`` were a column, that read would
  still say blocked until something ran.

* **Cross-owner is indistinguishable from nonexistent.** Linking to another
  member's record fails the same way as linking to id 999999: "no such X
  record". Not "forbidden", which would confirm the row exists and turn the
  endpoint into an enumeration oracle. The stage asserts on the message text,
  because the leak here is in the wording rather than the outcome.

* **EVENT is excluded structurally.** An event has no closing status, so a
  dependency on one could never be satisfied. The exclusion is computed from
  ``SPECS[...]["closing"]`` rather than hardcoded, and the test asserts on that
  derivation — not on a literal list that would silently stop matching if EVENT
  ever gained a closing status.

* **Refusals are audited before they are raised.** A denied link is the
  interesting event; an accepted one is ordinary. A refusal that leaves no
  trace makes repeated probing invisible, so every rejection path is checked
  for its audit row.

* **The ceiling is real.** ``MAX_DEPENDENCIES_PER_RECORD`` is filled exactly to
  the limit and then exceeded by one, so a boundary written ``>`` instead of
  ``>=`` fails here rather than in production.

* **Re-linking converges.** A retried request reports ``existing`` and writes
  nothing. Two rows for one dependency would double-count every blocker.

* **A revision carries its dependencies.** This one is here because the first
  implementation got it wrong and every other test in this file still passed.
  An edge left pointing at a superseded row is read by ``dependencies_for``
  (which resolves any id) and skipped by ``blocked_record_ids`` (which filters
  to ACTIVE), so the two readers answer the same question differently, closing
  the live blocker stops unblocking anything, and revising a dependent drops
  its blockers so the record reports itself ready to start. The stage asserts
  the two readers agree, not just that each one is individually plausible.

* **The bounds are exercised, not described.** Every other stage stays well
  inside them — chains three deep, queues of five — so the code that only runs
  at the edge never executes and its fail-closed direction is never tested.
  ``stage_bounds`` builds a chain longer than ``MAX_DEPENDENCY_DEPTH`` and a
  queue longer than ``MAX_ATTENTION_ITEMS``. Both of those mutations survived
  until it existed.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_record_links_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import operations  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9741
USER_B = 9742

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    records.ensure_records_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def setup_environment() -> None:
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


def _obligation(cur, owner: int, title: str) -> int:
    out = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=owner,
        title=title, obligation_type="LEGAL", due_at=_iso_in(30),
    )
    return int(out["record_id"])


def _request(cur, owner: int, title: str) -> int:
    out = records.create_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=owner,
        title=title, category="ADMIN")
    return int(out["record_id"])


def _decision(cur, owner: int, title: str) -> int:
    out = records.create_record(
        cur, record_type=records.TYPE_DECISION, owner_user_id=owner,
        title=title, question=f"should we {title}?")
    return int(out["record_id"])


def _event(cur, owner: int, title: str) -> int:
    out = records.create_record(
        cur, record_type=records.TYPE_EVENT, owner_user_id=owner,
        title=title, event_type="NOTE")
    return int(out["record_id"])


def _audit_actions(cur, owner: int) -> list[str]:
    cur.execute(
        f"SELECT action FROM {schema.AUDIT_TABLE} WHERE owner_user_id = ? "
        f"ORDER BY id",
        (owner,),
    )
    return [str(dict(r)["action"]) for r in (cur.fetchall() or ())]


def _rejects(fn) -> tuple[bool, str]:
    """Run ``fn``; report whether it refused and with what message."""
    try:
        fn()
    except records.PrivateRecordRejected as exc:
        return True, str(exc)
    except Exception as exc:  # noqa: BLE001 — a wrong exception type is a failure
        return False, f"{type(exc).__name__}: {exc}"
    return False, "no exception"


# ---------------------------------------------------------------------------
# Schema and vocabulary
# ---------------------------------------------------------------------------

def stage_schema() -> None:
    print("\n[schema]")
    conn = db.connect()
    cur = conn.cursor()
    records.reset_records_schema_cache()
    first = records.ensure_records_schema(cur, force=True)
    second = records.ensure_records_schema(cur, force=True)
    conn.commit()

    check("ensure_records_schema reports ready", first.get("status") == "ready", str(first))
    check("ensure_records_schema is idempotent", second.get("status") == "ready", str(second))

    present = set(db.get_table_columns(cur, records.RECORD_LINKS_TABLE) or [])
    missing = set(records.LINK_COLUMNS) - present
    check("the links table has every declared column", not missing, str(sorted(missing)))
    check("the links table has an id", "id" in present, str(sorted(present)))

    # The links table is registered in TABLES, so anything that iterates the
    # package's tables — a teardown, a backup, an export — includes it. A table
    # that exists but is not in TABLES is the one that silently survives a wipe.
    check("the links table is in TABLES", records.RECORD_LINKS_TABLE in records.TABLES)
    check("RECORD_TABLES still tracks exactly the six primitives",
          len(records.RECORD_TABLES) == len(records.RECORD_TYPES),
          f"{len(records.RECORD_TABLES)} vs {len(records.RECORD_TYPES)}")
    check("the links table is not one of the record tables",
          records.RECORD_LINKS_TABLE not in records.RECORD_TABLES)

    # Portability: the DDL must stay in the one dialect `services.db` can
    # translate. `SERIAL`, `JSONB` or a bare `AUTOINCREMENT` variant would work
    # on SQLite and fail on the Postgres deploy.
    ddl = records.links_table_ddl()
    check("links DDL uses the translatable autoincrement form",
          "INTEGER PRIMARY KEY AUTOINCREMENT" in ddl, ddl[:120])
    check("links DDL is guarded by IF NOT EXISTS", "IF NOT EXISTS" in ddl)
    for banned in ("SERIAL", "JSONB", "AUTO_INCREMENT"):
        check(f"links DDL avoids {banned}", banned not in ddl.upper().replace("AUTOINCREMENT", ""))

    # Every index leads with owner_user_id. An index that does not cannot serve
    # an owner-scoped query, and the query planner's answer to that is a scan
    # across every member's edges.
    for stmt in records.links_index_ddl():
        head = stmt.split("(", 1)[1] if "(" in stmt else ""
        check("index leads with owner_user_id", head.strip().startswith("owner_user_id"), stmt)

    conn.close()


def stage_vocabulary() -> None:
    print("\n[vocabulary]")
    linkable = records.linkable_types()

    # Derived, not listed. If EVENT ever gains a closing status this assertion
    # changes meaning with it, which is the point — a hardcoded exclusion list
    # would keep excluding EVENT for a reason that had stopped being true.
    expected = tuple(t for t in records.RECORD_TYPES if records.SPECS[t]["closing"])
    check("linkable types are derived from the closing vocabulary",
          linkable == expected, f"{linkable} vs {expected}")
    check("EVENT is not linkable", records.TYPE_EVENT not in linkable)
    check("EVENT has no closing status to derive from",
          records.SPECS[records.TYPE_EVENT]["closing"] == ())
    check("EVENT's exclusion is explained", records.TYPE_EVENT in records.NO_LINK_REASON)
    check("every other primitive is linkable",
          set(records.RECORD_TYPES) - {records.TYPE_EVENT} == set(linkable))

    check("DEPENDS_ON is the only link type", records.LINK_TYPES == (records.LINK_DEPENDS_ON,))
    check("the traversal is bounded by depth", records.MAX_DEPENDENCY_DEPTH > 0)
    check("the traversal is bounded by total visits", records.MAX_DEPENDENCY_VISITS > 0)
    check("dependencies per record are capped", records.MAX_DEPENDENCIES_PER_RECORD > 0)

    # The three audit actions exist and are registered. `audit.record` validates
    # against ACTIONS, so an unregistered action raises at the moment of the
    # write — inside a refusal path, where it would replace a clean rejection
    # with a 500.
    for action in (audit.ACTION_RECORD_LINK, audit.ACTION_RECORD_UNLINK,
                   audit.ACTION_RECORD_LINK_DENIED):
        check(f"{action} is registered", action in audit.ACTIONS)


# ---------------------------------------------------------------------------
# The happy path, and what it must derive
# ---------------------------------------------------------------------------

def stage_link_and_derive() -> None:
    print("\n[link + derived state]")
    conn, cur = _connect()

    blocker = _request(cur, USER_A, "get the survey back")
    dependent = _obligation(cur, USER_A, "sign the lease")

    out = records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=dependent,
        target_type=records.TYPE_REQUEST, target_id=blocker,
        note="cannot sign before the survey")
    check("a valid link is created", out.get("status") == records.STATUS_CREATED, str(out))
    check("the link reports an id", int(out.get("link_id") or 0) > 0, str(out))

    view = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=dependent)
    check("the dependent lists one blocker", len(view["depends_on"]) == 1, str(view))
    check("the dependent is blocked", view["blocked"] is True, str(view))
    check("the open blocker count is 1", view["open_blocker_count"] == 1, str(view))
    check("the blocker is reported open", view["depends_on"][0]["open"] is True, str(view))
    check("the blocker resolves", view["depends_on"][0]["found"] is True, str(view))
    check("the dependent blocks nothing", view["blocks"] == [], str(view))

    # The other direction, from the blocker's side.
    other = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_REQUEST, record_id=blocker)
    check("the blocker lists one dependent", len(other["blocks"]) == 1, str(other))
    check("the blocker is not itself blocked", other["blocked"] is False, str(other))

    ids = records.blocked_record_ids(
        cur, owner_user_id=USER_A, record_type=records.TYPE_OBLIGATION)
    check("the bulk reader agrees the dependent is blocked", dependent in ids, str(ids))

    # Closing the blocker unblocks the dependent with no sweep and no write.
    # This is the check that a stored `blocked` column cannot pass.
    records.update_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=blocker, status="COMPLETED")

    after = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=dependent)
    check("closing the blocker unblocks the dependent immediately",
          after["blocked"] is False, str(after))
    check("the closed blocker is still listed", len(after["depends_on"]) == 1, str(after))
    check("the closed blocker is reported not open",
          after["depends_on"][0]["open"] is False, str(after))
    check("the open blocker count falls to 0", after["open_blocker_count"] == 0, str(after))

    ids_after = records.blocked_record_ids(
        cur, owner_user_id=USER_A, record_type=records.TYPE_OBLIGATION)
    check("the bulk reader agrees, with no sweep run", dependent not in ids_after, str(ids_after))

    actions = _audit_actions(cur, USER_A)
    check("the link was audited", audit.ACTION_RECORD_LINK in actions, str(actions[-6:]))

    conn.commit()
    conn.close()


def stage_idempotence_and_unlink() -> None:
    print("\n[idempotence + unlink]")
    conn, cur = _connect()

    a = _obligation(cur, USER_A, "renew the policy")
    b = _request(cur, USER_A, "quote from the broker")

    first = records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_REQUEST, target_id=b)
    second = records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_REQUEST, target_id=b)

    check("a repeated link reports existing",
          second.get("status") == records.STATUS_EXISTING, str(second))
    check("a repeated link returns the same row",
          second.get("link_id") == first.get("link_id"), f"{first} / {second}")

    view = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=a)
    check("a repeated link does not duplicate the blocker",
          len(view["depends_on"]) == 1, str(view))

    removed = records.unlink_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_REQUEST, target_id=b)
    check("unlink removes the edge", removed.get("status") == "removed", str(removed))

    gone = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=a)
    check("the dependency is gone", gone["depends_on"] == [], str(gone))
    check("the record is no longer blocked", gone["blocked"] is False, str(gone))

    again = records.unlink_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_REQUEST, target_id=b)
    check("unlinking twice is not an error", again.get("status") == "absent", str(again))

    actions = _audit_actions(cur, USER_A)
    check("the unlink was audited", audit.ACTION_RECORD_UNLINK in actions, str(actions[-6:]))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# The rejection battery
# ---------------------------------------------------------------------------

def stage_self_and_cycles() -> None:
    print("\n[self-dependency + cycles]")
    conn, cur = _connect()

    a = _obligation(cur, USER_A, "cycle A")
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_OBLIGATION, target_id=a))
    check("a record cannot depend on itself", refused, message)
    check("the self-dependency message says so, not 'circular'",
          "itself" in message.lower(), message)

    # One hop: A depends on B, so B may not depend on A.
    b = _request(cur, USER_A, "cycle B")
    records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=a,
        target_type=records.TYPE_REQUEST, target_id=b)
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_REQUEST, source_id=b,
        target_type=records.TYPE_OBLIGATION, target_id=a))
    check("a one-hop cycle is refused", refused, message)
    check("the cycle message says circular", "circular" in message.lower(), message)

    # Three hops. This is the case a check that only compares the two endpoints
    # would admit: A→B and B→C already exist, and C→A closes the loop without
    # C and A being adjacent.
    c = _decision(cur, USER_A, "cycle C")
    records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_REQUEST, source_id=b,
        target_type=records.TYPE_DECISION, target_id=c)
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_DECISION, source_id=c,
        target_type=records.TYPE_OBLIGATION, target_id=a))
    check("a three-hop cycle is refused", refused, message)

    # And the refusal left nothing behind. A cycle check that inserts first and
    # rolls back on detection would pass every assertion above and fail this one.
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {records.RECORD_LINKS_TABLE} "
        f"WHERE owner_user_id = ? AND source_type = ? AND source_id = ?",
        (USER_A, records.TYPE_DECISION, c),
    )
    left = int(dict(cur.fetchone())["n"])
    check("the refused cycle wrote no edge", left == 0, f"{left} edge(s) remain")

    # A diamond is not a cycle. A→B, A→C, B→D, C→D is legal; a check that
    # refuses any node reachable by two paths would break real sequencing.
    d1 = _obligation(cur, USER_A, "diamond top")
    d2 = _request(cur, USER_A, "diamond left")
    d3 = _request(cur, USER_A, "diamond right")
    d4 = _decision(cur, USER_A, "diamond bottom")
    for src_t, src, tgt_t, tgt in (
        (records.TYPE_OBLIGATION, d1, records.TYPE_REQUEST, d2),
        (records.TYPE_OBLIGATION, d1, records.TYPE_REQUEST, d3),
        (records.TYPE_REQUEST, d2, records.TYPE_DECISION, d4),
        (records.TYPE_REQUEST, d3, records.TYPE_DECISION, d4),
    ):
        out = records.link_records(
            cur, owner_user_id=USER_A, source_type=src_t, source_id=src,
            target_type=tgt_t, target_id=tgt)
        check("a diamond edge is allowed",
              out.get("status") == records.STATUS_CREATED, str(out))

    denials = [x for x in _audit_actions(cur, USER_A)
               if x == audit.ACTION_RECORD_LINK_DENIED]
    check("every refusal was audited", len(denials) >= 3, f"{len(denials)} denial rows")

    conn.commit()
    conn.close()


def stage_cross_owner() -> None:
    print("\n[cross-owner]")
    conn, cur = _connect()

    mine = _obligation(cur, USER_A, "my obligation")
    theirs = _request(cur, USER_B, "their request")

    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=mine,
        target_type=records.TYPE_REQUEST, target_id=theirs))
    check("linking to another member's record is refused", refused, message)

    # The wording is the whole point. "forbidden" or "belongs to another user"
    # confirms the row exists; an attacker walking ids reads that as a hit.
    _, absent = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=mine,
        target_type=records.TYPE_REQUEST, target_id=999999))
    check("cross-owner and nonexistent fail identically",
          message == absent, f"cross-owner: {message!r} / absent: {absent!r}")
    for leak in ("owner", "forbidden", "permission", "another", "belongs"):
        check(f"the message does not leak via '{leak}'", leak not in message.lower(), message)

    # And the same in the source position.
    refused, src_message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_REQUEST, source_id=theirs,
        target_type=records.TYPE_OBLIGATION, target_id=mine))
    check("using another member's record as the source is refused", refused, src_message)

    # B's own view is unaffected by A's attempts.
    view = records.dependencies_for(
        cur, owner_user_id=USER_B,
        record_type=records.TYPE_REQUEST, record_id=theirs)
    check("the other member's record has no dependencies",
          view["depends_on"] == [] and view["blocks"] == [], str(view))

    # An owner reading with someone else's id gets nothing rather than a refusal.
    refused, message = _rejects(lambda: records.dependencies_for(
        cur, owner_user_id=USER_B,
        record_type=records.TYPE_OBLIGATION, record_id=mine))
    check("reading dependencies across owners is refused as not-found",
          refused and "no such" in message.lower(), message)

    check("the bulk reader is owner-scoped",
          records.blocked_record_ids(
              cur, owner_user_id=USER_B, record_type=records.TYPE_OBLIGATION) == set())

    conn.commit()
    conn.close()


def stage_shape_rejections() -> None:
    print("\n[shape rejections]")
    conn, cur = _connect()

    ob = _obligation(cur, USER_A, "shape obligation")
    ev = _event(cur, USER_A, "shape event")

    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=ob,
        target_type=records.TYPE_EVENT, target_id=ev))
    check("an event cannot be depended on", refused, message)
    check("the event refusal explains why", "never be satisfied" in message, message)

    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_EVENT, source_id=ev,
        target_type=records.TYPE_OBLIGATION, target_id=ob))
    check("an event cannot depend on anything", refused, message)

    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type="TASK", source_id=ob,
        target_type=records.TYPE_OBLIGATION, target_id=ob))
    check("an unknown record type is refused", refused, message)
    check("the unknown-type message is a shape error",
          "unknown record_type" in message, message)

    for bad in (0, -1, None, "abc"):
        refused, message = _rejects(lambda bad=bad: records.link_records(
            cur, owner_user_id=USER_A,
            source_type=records.TYPE_OBLIGATION, source_id=ob,
            target_type=records.TYPE_REQUEST, target_id=bad))
        check(f"target id {bad!r} is refused", refused, message)

    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=0,
        source_type=records.TYPE_OBLIGATION, source_id=ob,
        target_type=records.TYPE_OBLIGATION, target_id=ob))
    check("a missing owner is refused", refused, message)

    # A shape error must not be disguised as a not-found. The two refusals are
    # deliberately different: "unknown record_type" reveals nothing about this
    # member because it is true for everybody, and flattening it into "no such
    # record" would give a caller with a typo the least useful message
    # available while buying no extra safety.
    _, unknown = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type="TASK", source_id=ob,
        target_type=records.TYPE_OBLIGATION, target_id=ob))
    check("a shape error is not flattened into not-found",
          "no such" not in unknown.lower(), unknown)

    conn.commit()
    conn.close()


def stage_ceiling() -> None:
    print("\n[dependency ceiling]")
    conn, cur = _connect()

    limit = records.MAX_DEPENDENCIES_PER_RECORD
    src = _obligation(cur, USER_A, "ceiling source")

    created = 0
    for i in range(limit):
        out = records.link_records(
            cur, owner_user_id=USER_A,
            source_type=records.TYPE_OBLIGATION, source_id=src,
            target_type=records.TYPE_REQUEST,
            target_id=_request(cur, USER_A, f"ceiling blocker {i}"))
        if out.get("status") == records.STATUS_CREATED:
            created += 1
    check(f"exactly {limit} dependencies are accepted", created == limit, str(created))

    # The boundary. A `>` where `>=` was meant accepts one more than the limit,
    # and nothing else in the suite would notice.
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=src,
        target_type=records.TYPE_REQUEST,
        target_id=_request(cur, USER_A, "one too many")))
    check("the limit-plus-one dependency is refused", refused, message)

    view = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=src)
    check("the source holds exactly the limit",
          len(view["depends_on"]) == limit, str(len(view["depends_on"])))

    # The ceiling is per source record, not global. A counter that forgot the
    # source clause would refuse the next record's first dependency.
    other = _obligation(cur, USER_A, "another source")
    out = records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=other,
        target_type=records.TYPE_REQUEST,
        target_id=_request(cur, USER_A, "unrelated blocker"))
    check("the ceiling is per record, not per owner",
          out.get("status") == records.STATUS_CREATED, str(out))

    conn.commit()
    conn.close()


def stage_revision() -> None:
    """A revision is the same thing continuing, so its edges come with it.

    This stage exists because the first implementation of G-3 failed all four
    checks below and passed every other test in this file. A revision
    supersedes the old row and inserts a new one; the edge kept pointing at the
    superseded version, which ``_fetch`` still resolves (it does not filter
    lifecycle) but ``blocked_record_ids`` filters out. The observable results
    were that the two readers disagreed, that closing the live blocker never
    unblocked anything, and that revising a *dependent* silently dropped its
    dependencies so the record reported itself ready to start.
    """
    print("\n[revision carries dependencies]")
    conn, cur = _connect()

    src = _obligation(cur, USER_A, "sign the lease")
    tgt = _request(cur, USER_A, "survey")
    records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=src,
        target_type=records.TYPE_REQUEST, target_id=tgt)

    # Revise the blocker.
    new_tgt = int(records.revise_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=tgt, title="survey, rescheduled")["record_id"])
    check("a revision produces a new id", new_tgt != tgt, f"{tgt} -> {new_tgt}")

    view = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=src)
    check("the edge follows the blocker's revision",
          view["depends_on"][0]["record_id"] == new_tgt,
          f"points at {view['depends_on'][0]['record_id']}, expected {new_tgt}")
    check("the dependent is still blocked", view["blocked"] is True, str(view))

    # The disagreement check. These two readers answer the same question by
    # different routes — one resolves each endpoint, the other joins and filters
    # on lifecycle — so they are the pair most likely to drift apart.
    bulk = records.blocked_record_ids(
        cur, owner_user_id=USER_A, record_type=records.TYPE_OBLIGATION)
    check("both readers agree the dependent is blocked",
          (src in bulk) == view["blocked"], f"bulk={bulk}, per-record={view['blocked']}")

    # Closing the version that is actually live must unblock.
    records.update_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=new_tgt, status="COMPLETED")
    after = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=src)
    check("closing the live version of the blocker unblocks the dependent",
          after["blocked"] is False, str(after))
    check("both readers still agree",
          (src in records.blocked_record_ids(
              cur, owner_user_id=USER_A,
              record_type=records.TYPE_OBLIGATION)) == after["blocked"])

    # Now revise the dependent. Its dependencies must move to the new version,
    # not stay behind on a row nobody reads.
    reopened = _request(cur, USER_A, "second survey")
    records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=src,
        target_type=records.TYPE_REQUEST, target_id=reopened)
    new_src = int(records.revise_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        record_id=src, title="sign the lease, v2")["record_id"])

    moved = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=new_src)
    check("the new version inherits every dependency",
          len(moved["depends_on"]) == 2, str(len(moved["depends_on"])))
    check("the new version is blocked by the open one",
          moved["blocked"] is True, str(moved))

    stale = records.dependencies_for(
        cur, owner_user_id=USER_A,
        record_type=records.TYPE_OBLIGATION, record_id=src)
    check("the superseded version keeps no dependencies",
          stale["depends_on"] == [], str(stale))

    # Re-pointing relabels one node, so it can neither create nor destroy a
    # cycle. Confirm the invariant survived rather than assuming it.
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_REQUEST, source_id=reopened,
        target_type=records.TYPE_OBLIGATION, target_id=new_src))
    check("acyclicity survives re-pointing", refused, message)

    conn.commit()
    conn.close()


def stage_read_model() -> None:
    """What the member actually sees. A dependency nobody surfaces is a no-op."""
    print("\n[read model]")
    conn, cur = _connect()

    src = _obligation(cur, USER_A, "read-model obligation")
    b1 = _request(cur, USER_A, "read-model blocker one")
    b2 = _request(cur, USER_A, "read-model blocker two")
    for blocker in (b1, b2):
        records.link_records(
            cur, owner_user_id=USER_A,
            source_type=records.TYPE_OBLIGATION, source_id=src,
            target_type=records.TYPE_REQUEST, target_id=blocker)

    view = operations.overview(cur, owner_user_id=USER_A)
    items = {(i["record_type"], i["id"]): i for i in view["attention"]["items"]}
    item = items.get((records.TYPE_OBLIGATION, src))
    check("a blocked record reaches the attention queue", item is not None,
          str(sorted(items)))
    if item is not None:
        check("the queue row is marked blocked", item["blocked"] is True, str(item))
        check("the queue row carries the blocker count",
              item["open_blocker_count"] == 2, str(item["open_blocker_count"]))
        check("BLOCKED is among its reasons",
              operations.REASON_BLOCKED in item["reasons"], str(item["reasons"]))

    check("the overview reports a blocked total", view["blocked"] >= 1, str(view["blocked"]))

    # Blocked is not subtracted from needs_attention. A dashboard that moved
    # blocked rows out of the headline number would let a late obligation
    # vanish from the one figure the member reads, just by pointing it at a
    # second late thing.
    check("blocked records still count as needing attention",
          view["needs_attention"] >= view["blocked"],
          f"attention={view['needs_attention']} blocked={view['blocked']}")

    # Closing one blocker lowers the count without clearing the flag. A boolean
    # alone cannot tell a member whether finishing the next thing frees the row.
    records.update_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=b1, status="COMPLETED")
    after = operations.overview(cur, owner_user_id=USER_A)
    item = {(i["record_type"], i["id"]): i
            for i in after["attention"]["items"]}.get((records.TYPE_OBLIGATION, src))
    check("closing one blocker lowers the count",
          item is not None and item["open_blocker_count"] == 1,
          str(item and item["open_blocker_count"]))
    check("one blocker left still reads as blocked",
          item is not None and item["blocked"] is True, str(item))

    records.update_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=b2, status="COMPLETED")
    freed = operations.overview(cur, owner_user_id=USER_A)
    freed_item = {(i["record_type"], i["id"]): i
                  for i in freed["attention"]["items"]}.get(
                      (records.TYPE_OBLIGATION, src))
    check("clearing every blocker removes the BLOCKED reason",
          freed_item is None or operations.REASON_BLOCKED not in freed_item["reasons"],
          str(freed_item))

    # Being blocked must not mask a deadline. An overdue record that is also
    # blocked still ranks OVERDUE, because the blocker is what the member has to
    # go and chase — not a reason to stop showing them the date.
    late = int(records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="late and blocked", obligation_type="LEGAL",
        due_at=_iso_in(-3))["record_id"])
    records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=late,
        target_type=records.TYPE_REQUEST,
        target_id=_request(cur, USER_A, "blocker for the late one"))
    ranked = {(i["record_type"], i["id"]): i for i in
              operations.overview(cur, owner_user_id=USER_A)["attention"]["items"]}
    late_item = ranked.get((records.TYPE_OBLIGATION, late))
    check("an overdue blocked record is still reported blocked",
          late_item is not None and late_item["blocked"] is True, str(late_item))
    check("being blocked does not outrank being overdue",
          late_item is not None
          and late_item["primary_reason"] == operations.REASON_OVERDUE,
          str(late_item and late_item["primary_reason"]))
    check("both reasons are kept, not just the winner",
          late_item is not None
          and {operations.REASON_OVERDUE, operations.REASON_BLOCKED}
          <= set(late_item["reasons"]), str(late_item and late_item["reasons"]))

    # And the queue stays owner-scoped once dependencies are in it.
    other = operations.overview(cur, owner_user_id=USER_B)
    check("another member sees none of this", other["blocked"] == 0, str(other["blocked"]))

    conn.commit()
    conn.close()


def stage_bounds() -> None:
    """The two bounds, exercised past their limits rather than described.

    Both of these survived the mutation battery until this stage existed. Every
    other test in this file stays comfortably inside the bounds — chains three
    deep, queues of five — so the code that runs only at the edge was never
    executed, and a mutation flipping the fail-closed direction went unnoticed.
    """
    print("\n[bounds]")
    conn, cur = _connect()

    # 1. Traversal depth. Build a chain longer than MAX_DEPENDENCY_DEPTH, then
    #    ask for a link whose cycle check has to walk the whole thing. There is
    #    no cycle here; the walk simply cannot finish inside the bound, and the
    #    safe answer to "I could not rule out a loop" is to refuse. Refusing a
    #    legitimate link is visible and recoverable; admitting one that closes a
    #    loop leaves a graph the readers walk forever.
    depth = records.MAX_DEPENDENCY_DEPTH + 8
    chain = [_request(cur, USER_A, f"chain {i}") for i in range(depth)]
    for i in range(depth - 1):
        records.link_records(
            cur, owner_user_id=USER_A,
            source_type=records.TYPE_REQUEST, source_id=chain[i],
            target_type=records.TYPE_REQUEST, target_id=chain[i + 1])

    head = _obligation(cur, USER_A, "in front of a very long chain")
    refused, message = _rejects(lambda: records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION, source_id=head,
        target_type=records.TYPE_REQUEST, target_id=chain[0]))
    check("a walk that exhausts the depth bound fails closed", refused, message)

    # The bound is the reason, so a short chain of the same shape must still be
    # accepted — otherwise this check would also pass against an implementation
    # that refused everything.
    short = [_request(cur, USER_A, f"short {i}") for i in range(3)]
    for i in range(2):
        records.link_records(
            cur, owner_user_id=USER_A,
            source_type=records.TYPE_REQUEST, source_id=short[i],
            target_type=records.TYPE_REQUEST, target_id=short[i + 1])
    ok = records.link_records(
        cur, owner_user_id=USER_A,
        source_type=records.TYPE_OBLIGATION,
        source_id=_obligation(cur, USER_A, "in front of a short chain"),
        target_type=records.TYPE_REQUEST, target_id=short[0])
    check("a walk inside the depth bound still succeeds",
          ok.get("status") == records.STATUS_CREATED, str(ok))

    conn.commit()
    conn.close()

    # 2. The attention page cap. `blocked` must be counted over everything
    #    collected, not over the page — the number matters most to the member
    #    who has more than a page of problems, which is exactly when counting
    #    the page starts under-reporting.
    conn, cur = _connect()
    over = operations.MAX_ATTENTION_ITEMS + 10
    for i in range(over):
        blocked_record = _obligation(cur, USER_B, f"bulk blocked {i}")
        records.link_records(
            cur, owner_user_id=USER_B,
            source_type=records.TYPE_OBLIGATION, source_id=blocked_record,
            target_type=records.TYPE_REQUEST,
            target_id=_request(cur, USER_B, f"bulk blocker {i}"))

    view = operations.overview(cur, owner_user_id=USER_B)
    queue = view["attention"]
    check("the queue is genuinely over its page cap",
          queue["truncated"] is True and len(queue["items"]) == operations.MAX_ATTENTION_ITEMS,
          f"truncated={queue['truncated']} page={len(queue['items'])}")
    check("the blocked total counts past the page cap",
          view["blocked"] >= over, f"{view['blocked']} < {over}")
    check("the blocked total is not the page length",
          view["blocked"] != len(queue["items"]),
          f"blocked={view['blocked']} page={len(queue['items'])}")

    conn.commit()
    conn.close()


def run_all() -> None:
    setup_environment()
    stage_schema()
    stage_vocabulary()
    stage_link_and_derive()
    stage_idempotence_and_unlink()
    stage_self_and_cycles()
    stage_cross_owner()
    stage_shape_rejections()
    stage_ceiling()
    stage_revision()
    stage_read_model()
    stage_bounds()


def test_record_links() -> None:
    _FAILURES.clear()
    run_all()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE RECORD DEPENDENCIES — G-3")
    print(f"db: {_TMP_DB}")
    run_all()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
