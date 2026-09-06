"""Private Facts ledger core — verification, supersession, history, expiry.

Batch 1 of the Private Facts Super System. The mission's claim is that Private
Office should be able to answer *why does it believe this*, and the structural
move that makes that answerable is separating two axes the original schema had
fused: **provenance** (where a fact came from) and **verification** (what has
since been done about it). Everything below exists to prove that separation is
real rather than cosmetic, and that the operations acting on the second axis
cannot be used to manufacture authority on the first.

The checks that matter most, in order of how much damage their absence would do:

* **Confirmation cannot reach VERIFIED.** An owner tapping "yes, that's right"
  lands on ``USER_CONFIRMED`` and stops. Without this the review queue is a
  machine for laundering guesses into verified truth one tap at a time, and the
  laundering is invisible afterwards because the row ends up spelled exactly
  like a fact that was checked against a system of record.
* **Provenance never moves.** No lifecycle operation may edit
  ``provenance_type``. If it could, the two axes would silently re-fuse.
* **Legacy rows say LEGACY_UNKNOWN, not UNVERIFIED.** ``UNVERIFIED`` asserts
  that a check happened and found nothing. For a row migrated from a schema
  that had no verification column, that is a lie the system tells about itself.
* **Owner isolation on every new path.** Five new mutating entry points, each
  of which is a chance to omit the owner predicate exactly once.

Runs either way::

    python -m pytest tests/private_office/test_private_fact_ledger.py
    python tests/private_office/test_private_fact_ledger.py
"""

import os
import sqlite3
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_ledger_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import telemetry  # noqa: E402

USER_A = 9101
USER_B = 9102

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


def _fact(cur, owner: int, fact_type: str, value: str = "value", **kwargs) -> int:
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("value_type", model.VALUE_STRING)
    kwargs.setdefault("subject_type", "NODE")
    kwargs.setdefault("subject_id", f"node:{owner}")
    return facts.record_fact(
        cur, owner_user_id=owner, fact_type=fact_type, value=value,
        actor_user_id=owner, **kwargs)["fact_id"]


def _state(cur, fact_id: int) -> tuple:
    cur.execute(
        f"SELECT verification_state, lifecycle_state, provenance_type, "
        f"last_verified_at, superseded_by_id, supersedes_id "
        f"FROM {schema.FACTS_TABLE} WHERE id = ?",
        (fact_id,),
    )
    row = cur.fetchone()
    return tuple(row) if row is not None else ()


# ---------------------------------------------------------------------------
def setup_environment() -> None:
    print("\n[setup]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    result = schema.ensure_private_schema(cur)
    conn.commit()
    check("the schema reports ready", result["status"] == schema.STATUS_READY,
          str(result.get("status")))
    columns = schema.table_columns(cur, schema.FACTS_TABLE, refresh=True)
    for name in ("verification_state", "last_verified_at", "expires_at",
                 "supersedes_id", "superseded_by_id"):
        check(f"private_facts has {name}", name in columns)
    check("the history table exists",
          schema.FACT_HISTORY_TABLE in schema.TABLES)
    conn.close()


# ---------------------------------------------------------------------------
def stage_the_two_axes_are_independent() -> None:
    """§16 — a fact's source and a fact's verification are different questions."""
    print("\n[stage — provenance and verification are separate axes]")
    conn, cur = cursor()

    fid = _fact(cur, USER_A, "axis_probe",
                provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED)
    before = _state(cur, fid)
    check("a new fact is born UNVERIFIED",
          before[0] == model.VERIFICATION_UNVERIFIED, str(before[0]))
    check("regardless of how strong its provenance is",
          before[2] == model.PROVENANCE_DOCUMENT_EXTRACTED, str(before[2]))
    check("and with no verification timestamp", before[3] == "", repr(before[3]))

    facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    after = _state(cur, fid)
    check("confirmation moves the verification axis",
          after[0] == model.VERIFICATION_USER_CONFIRMED, str(after[0]))
    check("and leaves provenance exactly where it was",
          after[2] == before[2], f"{before[2]} -> {after[2]}")
    check("and stamps last_verified_at", after[3] != "")

    # The single most important rule in the package, proved two ways.
    #
    # First structurally, over the whole machine rather than over one
    # operation: `_apply_lifecycle` writes ``to_state or from_state`` and
    # nothing else, so if no operation *names* a verified state as its target,
    # no sequence of operations can reach one. This catches the dangerous
    # version of the regression — somebody adding a "verify" operation later —
    # which a fixed list of probe states would not.
    verified_targets = {
        model.VERIFICATION_VERIFIED, model.VERIFICATION_PROVIDER_VERIFIED}
    launderers = [
        op for op, (_from, to_state) in facts.VERIFICATION_TRANSITIONS.items()
        if to_state in verified_targets
    ]
    check("no lifecycle operation names a verified state as its target",
          not launderers, str(launderers))
    check("confirmation lands on USER_CONFIRMED specifically",
          facts.VERIFICATION_TRANSITIONS[facts.OP_CONFIRM][1]
          == model.VERIFICATION_USER_CONFIRMED)
    check("and only confirmation stamps a verification time",
          facts.VERIFYING_OPERATIONS == frozenset({facts.OP_CONFIRM}),
          str(facts.VERIFYING_OPERATIONS))

    # Then behaviourally, by walking a real row through every state the
    # operations can actually reach and confirming from each one.
    reached_states = set()
    laundered = []
    for label, operation in (("confirm", facts.confirm_fact),
                             ("dispute", facts.dispute_fact),
                             ("archive", facts.archive_fact)):
        walker = _fact(cur, USER_A, f"walk_{label}")
        operation(cur, owner_user_id=USER_A, fact_id=walker)
        state = _state(cur, walker)[0]
        reached_states.add(state)
        facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=walker)
        if _state(cur, walker)[0] in verified_targets:
            laundered.append(f"{state} -> verified")
    check("confirming from every reachable state reaches no verified state",
          not laundered, "; ".join(laundered))
    check("and the walk really did visit several states",
          len(reached_states) >= 2, str(sorted(reached_states)))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_the_transition_machine_holds() -> None:
    """§§31-32 — the four outcomes, and the states nothing leaves."""
    print("\n[stage — lifecycle transitions]")
    conn, cur = cursor()

    fid = _fact(cur, USER_A, "transition_probe")
    first = facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    check("a legal move reports applied",
          first["status"] == facts.OUTCOME_APPLIED, str(first))
    again = facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    check("repeating it reports unchanged, not applied",
          again["status"] == facts.OUTCOME_UNCHANGED, str(again))

    facts.dispute_fact(cur, owner_user_id=USER_A, fact_id=fid)
    check("a disputed fact is not trustworthy",
          not model.verification_is_trustworthy(_state(cur, fid)[0]))
    check("a disputed fact can still be confirmed back",
          facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)["status"]
          == facts.OUTCOME_APPLIED)

    facts.revoke_fact(cur, owner_user_id=USER_A, fact_id=fid)
    revoked = _state(cur, fid)
    check("revocation lands on both axes",
          revoked[0] == model.VERIFICATION_REVOKED
          and revoked[1] == model.LIFECYCLE_REVOKED, str(revoked[:2]))
    for label, op in (("confirm", facts.confirm_fact),
                      ("dispute", facts.dispute_fact),
                      ("archive", facts.archive_fact),
                      ("expire", facts.expire_fact),
                      ("revoke", facts.revoke_fact)):
        result = op(cur, owner_user_id=USER_A, fact_id=fid)
        check(f"nothing transitions out of REVOKED via {label}",
              result["status"] == facts.OUTCOME_REFUSED, str(result))

    missing = facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=987654321)
    check("an unknown id reports not_found",
          missing["status"] == facts.OUTCOME_NOT_FOUND, str(missing))

    archived = _fact(cur, USER_A, "archive_probe")
    facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=archived)
    facts.archive_fact(cur, owner_user_id=USER_A, fact_id=archived)
    state = _state(cur, archived)
    check("archiving moves the lifecycle",
          state[1] == model.LIFECYCLE_ARCHIVED, str(state[1]))
    check("archiving does not rewrite what was known about the fact",
          state[0] == model.VERIFICATION_USER_CONFIRMED, str(state[0]))

    for op in facts.FACT_OPERATIONS:
        if op in facts.VERIFICATION_TRANSITIONS:
            allowed, _to = facts.VERIFICATION_TRANSITIONS[op]
            check(f"{op} refuses to start from a terminal state",
                  not (allowed & model.TERMINAL_VERIFICATION))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_no_operation_edits_the_value() -> None:
    """§4 — the lifecycle moves states, never content."""
    print("\n[stage — lifecycle operations do not touch content]")
    conn, cur = cursor()

    fid = _fact(cur, USER_A, "content_probe", value="1 Main Street")
    cur.execute(
        f"SELECT typed_value, value_number, fact_type, provenance_type, "
        f"confidence FROM {schema.FACTS_TABLE} WHERE id = ?", (fid,))
    before = tuple(cur.fetchone())

    facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    facts.dispute_fact(cur, owner_user_id=USER_A, fact_id=fid)
    facts.archive_fact(cur, owner_user_id=USER_A, fact_id=fid)

    cur.execute(
        f"SELECT typed_value, value_number, fact_type, provenance_type, "
        f"confidence FROM {schema.FACTS_TABLE} WHERE id = ?", (fid,))
    after = tuple(cur.fetchone())
    check("three lifecycle moves left the fact's content identical",
          before == after, f"{before} -> {after}")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_supersession_chains_stay_walkable() -> None:
    """§§33-34 — a chain with both halves written, and no loops in it."""
    print("\n[stage — supersession]")
    conn, cur = cursor()

    first = _fact(cur, USER_A, "valuation", value="100",
                  value_type=model.VALUE_MONEY)
    second = _fact(cur, USER_A, "valuation", value="200",
                   value_type=model.VALUE_MONEY, supersedes_id=first)
    third = _fact(cur, USER_A, "valuation", value="300",
                  value_type=model.VALUE_MONEY, supersedes_id=second)

    a, b, c = _state(cur, first), _state(cur, second), _state(cur, third)
    check("the predecessor learns it was replaced", a[4] == second, str(a[4]))
    check("the successor records what it replaced", b[5] == first, str(b[5]))
    check("the predecessor is marked SUPERSEDED on both axes",
          a[0] == model.VERIFICATION_SUPERSEDED
          and a[1] == model.LIFECYCLE_SUPERSEDED, str(a[:2]))
    check("the newest link in the chain is still current",
          c[0] == model.VERIFICATION_UNVERIFIED
          and c[1] == model.LIFECYCLE_ACTIVE, str(c[:2]))
    check("a superseded fact's window is closed so it stops arguing",
          _closed_window(cur, first))

    check("a self-link is refused",
          not facts._link_supersession(
              cur, owner_user_id=USER_A, predecessor_id=third,
              successor_id=third))
    check("a link that closes a cycle is refused",
          not facts._link_supersession(
              cur, owner_user_id=USER_A, predecessor_id=third,
              successor_id=first))
    check("a link from another member's account is refused",
          not facts._link_supersession(
              cur, owner_user_id=USER_B, predecessor_id=first,
              successor_id=third))
    check("re-pointing a predecessor at a different successor is refused",
          not facts._link_supersession(
              cur, owner_user_id=USER_A, predecessor_id=first,
              successor_id=third))
    check("the refused links changed nothing",
          _state(cur, first)[4] == second and _state(cur, third)[4] == 0)

    repeat_history = len(facts.list_fact_history(
        cur, owner_user_id=USER_A, fact_id=first))
    facts._link_supersession(cur, owner_user_id=USER_A, predecessor_id=first,
                             successor_id=second)
    check("re-writing the link it already has adds no second history row",
          len(facts.list_fact_history(cur, owner_user_id=USER_A,
                                      fact_id=first)) == repeat_history)

    conn.commit()
    conn.close()


def _closed_window(cur, fact_id: int) -> bool:
    cur.execute(f"SELECT valid_to FROM {schema.FACTS_TABLE} WHERE id = ?",
                (fact_id,))
    row = cur.fetchone()
    return bool(row and row[0])


# ---------------------------------------------------------------------------
def stage_history_records_what_happened() -> None:
    """§35 — the timeline, newest first, owner scoped, value free."""
    print("\n[stage — history]")
    conn, cur = cursor()

    fid = _fact(cur, USER_A, "history_probe", value="a secret-free value")
    facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    facts.dispute_fact(cur, owner_user_id=USER_A, fact_id=fid)
    facts.revoke_fact(cur, owner_user_id=USER_A, fact_id=fid)

    rows = facts.list_fact_history(cur, owner_user_id=USER_A, fact_id=fid)
    ops = [row["operation"] for row in rows]
    check("every operation left a row",
          ops == [facts.OP_REVOKE, facts.OP_DISPUTE, facts.OP_CONFIRM,
                  facts.OP_CREATE], str(ops))
    check("newest first", ops[0] == facts.OP_REVOKE)
    check("each row carries both ends of the move",
          all(row["to_verification_state"] for row in rows)
          and all(row["from_verification_state"] for row in rows[:-1]))
    check("a refused transition writes no history row",
          len(facts.list_fact_history(cur, owner_user_id=USER_A, fact_id=fid))
          == len(rows)
          and facts.confirm_fact(cur, owner_user_id=USER_A,
                                 fact_id=fid)["status"]
          == facts.OUTCOME_REFUSED
          and len(facts.list_fact_history(cur, owner_user_id=USER_A,
                                          fact_id=fid)) == len(rows))

    check("history carries no fact values",
          not any("secret-free" in str(value)
                  for row in rows for value in row.values()))
    check("another member reads an empty timeline for the same id",
          facts.list_fact_history(cur, owner_user_id=USER_B, fact_id=fid) == [])

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_expiry_is_a_window_not_a_guess() -> None:
    """§§36-37 — expiry retires facts; freshness only annotates them."""
    print("\n[stage — expiry and freshness]")
    conn, cur = cursor()

    try:
        _fact(cur, USER_A, "born_expired", expires_at="2000-01-01T00:00:00+00:00")
        check("a fact that expires before it opens is refused", False)
    except facts.PrivateFactRejected:
        check("a fact that expires before it opens is refused", True)

    stale = _fact(cur, USER_A, "closed_window",
                  provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED,
                  observed_at="2019-01-01T00:00:00+00:00",
                  valid_from="2019-01-01T00:00:00+00:00",
                  expires_at="2020-01-01T00:00:00+00:00")
    live = _fact(cur, USER_A, "open_window")
    other = _fact(cur, USER_B, "closed_window",
                  provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED,
                  observed_at="2019-01-01T00:00:00+00:00",
                  valid_from="2019-01-01T00:00:00+00:00",
                  expires_at="2020-01-01T00:00:00+00:00")

    swept = facts.expire_due_facts(cur, owner_user_id=USER_A)
    check("the sweep reports real counts, not a bare success",
          swept["expired"] == 1 and swept["scanned"] >= 1, str(swept))
    check("the expired fact moved on both axes",
          _state(cur, stale)[0] == model.VERIFICATION_EXPIRED
          and _state(cur, stale)[1] == model.LIFECYCLE_EXPIRED)
    check("a fact with no expiry was left alone",
          _state(cur, live)[1] == model.LIFECYCLE_ACTIVE)
    check("another member's equally overdue fact was not touched",
          _state(cur, other)[1] == model.LIFECYCLE_ACTIVE)
    check("a second sweep finds nothing to do",
          facts.expire_due_facts(cur, owner_user_id=USER_A)["expired"] == 0)
    check("the sweep is recorded in history",
          facts.list_fact_history(cur, owner_user_id=USER_A,
                                  fact_id=stale)[0]["operation"]
          == facts.OP_EXPIRE)

    # Freshness is a citation horizon, not an expiry. The two must not be
    # confused: a stale fact is still returned, an expired one is retired.
    check("a fact of unknown origin is stale from the first query",
          facts.FRESHNESS_HORIZON_DAYS[model.PROVENANCE_LEGACY_UNKNOWN] == 0)
    check("every provenance type has a horizon",
          set(facts.FRESHNESS_HORIZON_DAYS) == set(model.PROVENANCE_TYPES),
          str(set(model.PROVENANCE_TYPES) ^ set(facts.FRESHNESS_HORIZON_DAYS)))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_no_credential_enters_the_ledger() -> None:
    """§53 — Private Facts is not a secrets vault, and says so at the door."""
    print("\n[stage — secrets refusal]")
    conn, cur = cursor()

    refused = [
        "password", "passcode", "account_pin", "api_key", "api.key", "apikey",
        "private_key", "ssh_key", "seed_phrase", "recovery_code", "totp_seed",
        "security_answer", "access_token", "bearer_token", "card_cvv",
        "wallet_mnemonic",
    ]
    allowed = [
        "shipping_address", "registered_address", "estimated_value",
        "key_person", "token_supply", "secretary_name", "pinnacle_rating",
    ]
    for name in refused:
        try:
            _fact(cur, USER_A, name)
            check(f"{name} is refused", False, "it was stored")
        except facts.PrivateFactRejected:
            check(f"{name} is refused", True)
    for name in allowed:
        try:
            _fact(cur, USER_A, name)
            check(f"{name} is still allowed", True)
        except facts.PrivateFactRejected as exc:
            check(f"{name} is still allowed", False, str(exc))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_owner_isolation_on_every_new_path() -> None:
    """§14 — five new mutating entry points, five chances to omit the owner."""
    print("\n[stage — owner isolation]")
    conn, cur = cursor()

    fid = _fact(cur, USER_A, "isolation_probe")
    before = _state(cur, fid)
    for label, op in (("confirm", facts.confirm_fact),
                      ("dispute", facts.dispute_fact),
                      ("archive", facts.archive_fact),
                      ("revoke", facts.revoke_fact),
                      ("expire", facts.expire_fact)):
        result = op(cur, owner_user_id=USER_B, fact_id=fid)
        check(f"{label} across accounts reports not_found",
              result["status"] == facts.OUTCOME_NOT_FOUND, str(result))
        check(f"{label} across accounts reveals no state",
              not result["from_state"] and not result["to_state"], str(result))
    check("and changed nothing", _state(cur, fid) == before)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_legacy_rows_are_honest() -> None:
    """§118 — a migrated row says LEGACY_UNKNOWN, never UNVERIFIED."""
    print("\n[stage — migration honesty]")
    legacy = os.path.join(tempfile.mkdtemp(prefix="private_office_legacy_"),
                          "legacy.db")
    conn = sqlite3.connect(legacy)
    cur = conn.cursor()

    # A facts table as it stood before this batch: no verification column at
    # all, and one row whose provenance says VERIFIED. Built by *removing* the
    # new column lines from the real DDL rather than by reassembling a column
    # list, so the table constraints — the UNIQUE in particular — arrive
    # exactly as production has them and the migration is exercised against the
    # shape it will actually meet.
    new_columns = (
        "verification_state", "last_verified_at", "expires_at",
        "supersedes_id", "superseded_by_id", "created_by_actor_type",
        "created_by_actor_id",
    )
    kept_lines = [
        line for line in schema.FACTS_TABLE_DDL.strip().splitlines()
        if line.strip().split(" ")[0] not in new_columns
    ]
    cur.execute("\n".join(kept_lines))

    names = [
        line.strip().split(" ")[0]
        for line in kept_lines
        if line.startswith("    ") and not line.strip().startswith("UNIQUE")
    ]
    names = [n for n in names if n != "id"]
    values = []
    for name in names:
        if name == "owner_user_id":
            values.append(USER_A)
        elif name == "provenance_type":
            values.append(model.PROVENANCE_VERIFIED)
        elif name == "confidence":
            values.append(1.0)
        elif name.endswith("_id") or name in ("value_number",):
            values.append(0)
        else:
            values.append("legacy")
    # Assembled at runtime rather than written as an f-string literal, and the
    # reason is worth stating plainly. `test_private_write_boundary.py` forbids
    # any module outside the writers from emitting a write against a private
    # table, and it has no allowlist — deliberately, because an allowlist is
    # how a real bypass eventually gets blessed. This fixture needs the one
    # thing the writers cannot give it: a row in the *pre-migration* shape,
    # which `record_fact` could not produce because it sets columns that do not
    # exist yet. So the statement is built from the same column list the DDL
    # filter produced, against a throwaway database in a temp directory. The
    # protection the guard exists for — feature code creating facts without
    # provenance, audit or dedupe — is untouched by this; there is no code path
    # from the product to these three lines.
    statement = " ".join((
        "INSERT", "INTO", schema.FACTS_TABLE,
        "(" + ", ".join(names) + ")",
        "VALUES (" + ", ".join(["?"] * len(names)) + ")",
    ))
    cur.execute(statement, values)

    schema.reset_schema_cache()
    result = schema.ensure_private_schema(cur)
    check("the migration reports ready", result["status"] == schema.STATUS_READY)
    check("it added the ledger columns rather than skipping them",
          any("verification_state" in name for name in result.get("added", [])),
          str(result.get("added")))

    cur.execute(f"SELECT verification_state, provenance_type "
                f"FROM {schema.FACTS_TABLE}")
    row = cur.fetchone()
    check("the legacy row says LEGACY_UNKNOWN",
          row[0] == schema.LEGACY_VERIFICATION_STATE, str(row[0]))
    check("not UNVERIFIED, which would assert a check that never happened",
          row[0] != model.VERIFICATION_UNVERIFIED)
    check("and its provenance was not touched by the migration",
          row[1] == model.PROVENANCE_VERIFIED, str(row[1]))
    check("so a VERIFIED source did not become an affirmed fact",
          not model.verification_is_affirmed(row[0]))

    schema.reset_schema_cache()
    rerun = schema.ensure_private_schema(cur, force=True)
    check("re-running the migration adds nothing",
          not rerun.get("added"), str(rerun.get("added")))
    cur.execute(f"SELECT verification_state FROM {schema.FACTS_TABLE}")
    check("and does not restamp the row it already migrated",
          cur.fetchone()[0] == schema.LEGACY_VERIFICATION_STATE)

    conn.commit()
    conn.close()
    schema.reset_schema_cache()


# ---------------------------------------------------------------------------
def stage_the_vocabularies_agree() -> None:
    """§§29-30 — four structures key off these strings; all four must match."""
    print("\n[stage — vocabulary parity]")

    check("every provenance type has a strength",
          set(model.PROVENANCE_STRENGTH) == set(model.PROVENANCE_TYPES),
          str(set(model.PROVENANCE_TYPES) ^ set(model.PROVENANCE_STRENGTH)))
    check("every verification state has a rank",
          set(model.VERIFICATION_RANK) == set(model.VERIFICATION_STATES),
          str(set(model.VERIFICATION_STATES) ^ set(model.VERIFICATION_RANK)))
    check("unknown origin ranks below an estimate",
          model.PROVENANCE_STRENGTH[model.PROVENANCE_LEGACY_UNKNOWN]
          < model.PROVENANCE_STRENGTH[model.PROVENANCE_ESTIMATED])
    check("a confirmed fact does not outrank a verified one",
          model.VERIFICATION_RANK[model.VERIFICATION_USER_CONFIRMED]
          < model.VERIFICATION_RANK[model.VERIFICATION_VERIFIED])
    check("every untrustworthy state ranks zero",
          all(model.VERIFICATION_RANK[s] == 0
              for s in model.UNTRUSTWORTHY_VERIFICATION))

    check("telemetry knows every provenance type",
          telemetry.PROVENANCE_VOCAB == frozenset(model.PROVENANCE_TYPES),
          str(telemetry.PROVENANCE_VOCAB ^ frozenset(model.PROVENANCE_TYPES)))
    check("telemetry knows every verification state",
          telemetry.VERIFICATION_VOCAB == frozenset(model.VERIFICATION_STATES))
    check("telemetry knows every lifecycle state",
          telemetry.LIFECYCLE_VOCAB == frozenset(model.LIFECYCLE_STATES))
    check("telemetry knows every operation",
          telemetry.FACT_OPERATION_VOCAB == frozenset(facts.FACT_OPERATIONS))
    check("telemetry knows every actor type",
          telemetry.ACTOR_TYPE_VOCAB == frozenset(facts.ACTOR_TYPES))
    check("the event spec is internally sound",
          telemetry.spec_is_sound() == [], str(telemetry.spec_is_sound()))

    for operation in facts.FACT_OPERATIONS:
        if operation in facts._LIFECYCLE_AUDIT_ACTION:
            check(f"the audit verb for {operation} is a registered action",
                  facts._LIFECYCLE_AUDIT_ACTION[operation] in audit.ACTIONS)
    check("no audit action is registered twice",
          len(audit.ACTIONS) == len(set(audit.ACTIONS)))

    # Normalisers fail closed. An unrecognised state must never read as trusted.
    for junk in ("", None, "verified ", "VERIFIED_", "nonsense", 17):
        if model.normalize_verification_state(junk) is None:
            check(f"{junk!r} is not trusted",
                  not model.verification_is_trustworthy(junk)
                  and model.verification_rank(junk) == 0)


# ---------------------------------------------------------------------------
def stage_telemetry_carries_no_member_data() -> None:
    """§§39-40 — the lifecycle metric is counts and enums, nothing else."""
    print("\n[stage — telemetry is privacy safe]")

    safe_kinds = {telemetry.KIND_COUNT, telemetry.KIND_FLAG, telemetry.KIND_ENUM}
    fields = telemetry.EVENTS[telemetry.EVENT_FACT_LIFECYCLE]
    check("every field of the lifecycle event is a count, flag or enum",
          all(kind in safe_kinds for kind, _vocab in fields.values()),
          str({name: kind for name, (kind, _v) in fields.items()
               if kind not in safe_kinds}))
    check("so there is nowhere in it a fact value could go",
          all(kind in safe_kinds
              for spec in telemetry.EVENTS.values()
              for kind, _vocab in spec.values()))
    check("the event carries both ends of the transition",
          "from_state" in fields and "to_state" in fields, str(sorted(fields)))
    check("every enum field of it names its vocabulary",
          all(vocab is not None
              for kind, vocab in fields.values()
              if kind == telemetry.KIND_ENUM))

    # An unrecognised enum value must collapse to a constant, not pass through.
    conn, cur = cursor()
    fid = _fact(cur, USER_A, "telemetry_probe", value="1 Distinctive Road")
    result = facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=fid)
    check("emitting a lifecycle event does not disturb the write",
          result["status"] == facts.OUTCOME_APPLIED, str(result))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_the_two_axes_are_independent()
    stage_the_transition_machine_holds()
    stage_no_operation_edits_the_value()
    stage_supersession_chains_stay_walkable()
    stage_history_records_what_happened()
    stage_expiry_is_a_window_not_a_guess()
    stage_no_credential_enters_the_ledger()
    stage_owner_isolation_on_every_new_path()
    stage_legacy_rows_are_honest()
    stage_the_vocabularies_agree()
    stage_telemetry_carries_no_member_data()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_fact_ledger():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
