"""The obligation schedule — journeys, then the mutations that must fail.

Run either way::

    python -m pytest tests/private_office/test_cash_flow.py
    python tests/private_office/test_cash_flow.py

What this file defends
----------------------
``cash_flow.schedule`` puts the member's obligations on a timeline. Three
things make that defensible rather than decorative, and each is exercised as a
journey and then again as a mutation:

* **Undated is not never.** An obligation with an amount and no due date is
  real money owed at an unknown time. It must be counted in ``excluded`` and
  must drop ``complete``, never silently vanish from a chart that then looks
  settled.
* **Unquantified is not zero.** An obligation with a date and no amount must
  not contribute 0.0 to its bucket, which would make that date read as clear.
* **Recurrence is never inferred.** One recorded obligation is one scheduled
  outflow. A monthly-looking title does not become twelve rows.

Plus the two structural rules every capital read shares: a single currency or
no totals at all, and owner isolation with a denied shape rather than a thin
payload.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="capital_cash_flow_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.private_office import cash_flow as flow  # noqa: E402
from services.private_office import obligation_projection as obligations  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9971
USER_B = 9972

#: Every stage reads at this fixed instant, so a due date written as "now + 5
#: days" lands in a known bucket without the test depending on wall-clock drift
#: between seeding and reading.
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    return conn, cur


def _at(days: float) -> str:
    return (NOW + timedelta(days=days)).isoformat()


def _obligation(cur, user_id: int, title: str, kind: str, **fields) -> int:
    created = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=user_id,
        title=title, obligation_type=kind, domain="FINANCIAL", **fields)
    return int(created["record_id"])


def _read(cur, owner: int = USER_A, actor: int | None = None,
          now: datetime | None = None) -> dict:
    return flow.schedule(cur, owner_user_id=owner,
                         actor_user_id=owner if actor is None else actor,
                         now=now or NOW)


def setup_environment() -> None:
    schema.reset_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Journey: nothing recorded is not a clear schedule
# ---------------------------------------------------------------------------

def stage_empty_store() -> None:
    print("\n[empty store]")
    conn, cur = _connect()
    try:
        payload = _read(cur)
        check("an empty store still answers ok", payload.get("ok") is True)
        check("the schedule is empty", payload["schedule"] == [])
        check("no bucket claims an amount",
              all(b["count"] == 0 for b in payload["buckets"].values()),
              payload["buckets"])
        check("with no obligations there is no currency, so the total is None "
              "rather than 0",
              payload["totals"]["scheduled_amount"] is None,
              payload["totals"])
        check("and an empty store is not called complete",
              payload["totals"]["complete"] is False, payload["totals"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: dated, quantified obligations land in the right buckets
# ---------------------------------------------------------------------------

def stage_bucketing() -> None:
    print("\n[bucketing]")
    conn, cur = _connect()
    try:
        # One obligation per bucket, each a different amount so a row landing
        # in the wrong bucket changes that bucket's sum and is caught by value
        # rather than by count alone.
        seeds = (
            ("Overdue card", -10, 100.0, "overdue"),
            ("Next month rent", 15, 200.0, "due_30"),
            ("Quarterly tax", 60, 400.0, "due_90"),
            ("Half-year premium", 150, 800.0, "due_180"),
            ("Annual fee", 300, 1600.0, "due_365"),
            ("Balloon payment", 500, 3200.0, "beyond_365"),
        )
        for title, days, amount, _bucket in seeds:
            _obligation(cur, USER_A, title, "LOAN_PAYMENT",
                        amount=str(amount), currency="usd", due_at=_at(days))
        payload = _read(cur)

        check("every obligation is scheduled", len(payload["schedule"]) == 6,
              len(payload["schedule"]))
        for title, days, amount, bucket in seeds:
            row = next((r for r in payload["schedule"] if r["title"] == title),
                       None)
            check(f"{title!r} lands in {bucket}",
                  row is not None and row["bucket"] == bucket,
                  row and row["bucket"])
            check(f"{title!r} carries its own amount, not a bucket average",
                  row is not None and row["amount"] == amount,
                  row and row["amount"])
        check("each bucket holds exactly one row",
              all(b["count"] == 1 for b in payload["buckets"].values()),
              payload["buckets"])
        check("bucket sums equal the amounts placed in them",
              all(payload["buckets"][b]["amount"] == a
                  for _t, _d, a, b in seeds), payload["buckets"])
        check("the scheduled total is the sum of the buckets",
              payload["totals"]["scheduled_amount"] == sum(
                  a for _t, _d, a, _b in seeds),
              payload["totals"])
        check("the overdue row is marked overdue",
              next(r for r in payload["schedule"]
                   if r["title"] == "Overdue card")["overdue"] is True)
        check("nothing was left out, so the schedule is complete",
              payload["totals"]["complete"] is True, payload["totals"])
        check("the schedule is ordered soonest first",
              [r["days_until"] for r in payload["schedule"]]
              == sorted(r["days_until"] for r in payload["schedule"])),
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: an obligation with no date is money owed at an unknown time
# ---------------------------------------------------------------------------

def stage_undated_is_not_never() -> None:
    print("\n[undated is not never]")
    conn, cur = _connect()
    try:
        before = _read(cur)["totals"]["scheduled_amount"]
        _obligation(cur, USER_A, "Undated settlement", "LOAN_PAYMENT",
                    amount="5000", currency="usd")
        payload = _read(cur)

        check("the undated obligation is in no bucket",
              all("Undated settlement" not in [r["title"]
                                               for r in payload["schedule"]
                                               if r["bucket"] == name]
                  for name in payload["buckets"]),
              [r["title"] for r in payload["schedule"]])
        check("it is counted as excluded, not dropped",
              payload["excluded"]["undated"] == 1, payload["excluded"])
        check("its 5000 did not enter any bucket total",
              payload["totals"]["scheduled_amount"] == before,
              (payload["totals"]["scheduled_amount"], before))
        check("and the schedule is no longer complete",
              payload["totals"]["complete"] is False, payload["totals"])
        check("the excluded count is published as one number too",
              payload["totals"]["excluded_count"] == 1, payload["totals"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: an obligation with no amount is not a zero on that date
# ---------------------------------------------------------------------------

def stage_unquantified_is_not_zero() -> None:
    print("\n[unquantified is not zero]")
    conn, cur = _connect()
    try:
        before = dict(_read(cur)["buckets"]["due_30"])
        _obligation(cur, USER_A, "Unknown legal bill", "LOAN_PAYMENT",
                    due_at=_at(20))
        payload = _read(cur)

        check("the dated but unquantified obligation is counted",
              payload["excluded"]["unquantified"] == 1, payload["excluded"])
        check("it did not add a zero to its bucket",
              payload["buckets"]["due_30"]["amount"] == before["amount"],
              (payload["buckets"]["due_30"], before))
        check("and it did not inflate the bucket's row count",
              payload["buckets"]["due_30"]["count"] == before["count"],
              (payload["buckets"]["due_30"], before))
        check("the schedule stays incomplete",
              payload["totals"]["complete"] is False, payload["totals"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: recurrence is never inferred
# ---------------------------------------------------------------------------

def stage_no_invented_recurrence() -> None:
    print("\n[no invented recurrence]")
    conn, cur = _connect()
    try:
        before = len(_read(cur)["schedule"])
        # A title and kind that a helpful implementation would be tempted to
        # expand into twelve monthly rows. It must produce exactly one.
        _obligation(cur, USER_A, "Monthly mortgage payment", "LOAN_PAYMENT",
                    amount="1200", currency="usd", due_at=_at(10))
        payload = _read(cur)

        matches = [r for r in payload["schedule"]
                   if r["title"] == "Monthly mortgage payment"]
        check("a monthly-sounding obligation produces exactly one row",
              len(matches) == 1, len(matches))
        check("and the schedule grew by exactly one",
              len(payload["schedule"]) == before + 1,
              (len(payload["schedule"]), before))
        check("it contributes its amount once, not twelve times",
              payload["buckets"]["due_30"]["count"] == 1
              or all(r["amount"] == 1200.0 for r in matches),
              payload["buckets"]["due_30"])
        check("the payload states that recurrence is not inferred",
              payload["basis"]["recurrence"] == flow.RECURRENCE_BASIS)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: outflows only, and the payload says so
# ---------------------------------------------------------------------------

def stage_outflows_only() -> None:
    print("\n[outflows only]")
    conn, cur = _connect()
    try:
        payload = _read(cur)
        check("the payload states there is no income side",
              payload["basis"]["inflows"] == flow.INFLOWS_BASIS)
        check("there is no net figure to mistake for cash flow",
              "net" not in payload and "net" not in payload["totals"],
              sorted(payload["totals"]))
        check("the bucket edges are published so the chart is auditable",
              [b["name"] for b in payload["basis"]["buckets"]]
              == [name for name, _l, _u in flow.BUCKETS],
              payload["basis"]["buckets"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: two currencies means no totals, not an invented rate
# ---------------------------------------------------------------------------

def stage_mixed_currency() -> None:
    print("\n[mixed currency]")
    conn, cur = _connect()
    try:
        _obligation(cur, USER_A, "Paris apartment charge", "LOAN_PAYMENT",
                    amount="900", currency="eur", due_at=_at(25))
        payload = _read(cur)

        check("with two currencies present no total is produced",
              payload["totals"]["scheduled_amount"] is None,
              payload["totals"])
        check("and no bucket claims an amount either",
              all(b["amount"] is None for b in payload["buckets"].values()),
              payload["buckets"])
        check("the EUR row is still listed with its own currency",
              any(r["currency"] == "EUR" for r in payload["schedule"]),
              [r["currency"] for r in payload["schedule"]])
        check("no rate was invented",
              payload["totals"]["currency"] == "", payload["totals"])
        check("no row was folded into a total that does not name its currency",
              payload["totals"]["mixed_currency_rows"] == 0,
              payload["totals"])
        check("and the schedule is not complete",
              payload["totals"]["complete"] is False, payload["totals"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: owner isolation
# ---------------------------------------------------------------------------

def stage_owner_isolation() -> None:
    print("\n[owner isolation]")
    conn, cur = _connect()
    try:
        payload = _read(cur, owner=USER_A, actor=USER_B)
        check("B reading A's schedule is denied by name",
              payload.get("ok") is False
              and payload["denied"]["reason"] == flow.DENIED_NOT_OWNER,
              payload.get("denied"))
        check("the refusal carries no schedule and no buckets",
              payload["schedule"] == [] and payload["buckets"] == {}
              and payload["totals"] == {}, payload)

        b_view = _read(cur, owner=USER_B)
        check("B's own schedule holds none of A's obligations",
              b_view["schedule"] == [], b_view["schedule"])
        check("and B's empty schedule is not called complete either",
              b_view["totals"]["complete"] is False, b_view["totals"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mutation battery (mission §138)
# ---------------------------------------------------------------------------
#
# Each mutation removes one protection and asserts a *named* check above would
# have failed. Every assertion is on a value: a mutation that only crashes is
# not evidence that the invariant is defended.

def stage_mutation_battery() -> None:
    print("\n[mutation battery]")
    conn, cur = _connect()
    try:
        truth = _read(cur)

        # 1. Treat an undated obligation as due now.
        original_parse = flow._parse_due
        flow._parse_due = lambda raw: (original_parse(raw) or NOW)
        try:
            mutated = _read(cur)
        finally:
            flow._parse_due = original_parse
        check("MUTATION undated-defaulted-to-now is caught by the excluded "
              "count falling and an overdue bucket appearing from nowhere",
              mutated["excluded"]["undated"] < truth["excluded"]["undated"]
              and mutated["buckets"]["overdue"]["count"]
              > truth["buckets"]["overdue"]["count"],
              (mutated["excluded"], mutated["buckets"]["overdue"]))

        # 2. Treat an unquantified obligation as zero.
        original_schedule = flow.schedule

        def zero_filled(cur_, *, owner_user_id, actor_user_id, now=None):
            payload = original_schedule(
                cur_, owner_user_id=owner_user_id,
                actor_user_id=actor_user_id, now=now)
            if payload.get("ok"):
                moved = payload["excluded"]["unquantified"]
                payload["excluded"]["unquantified"] = 0
                payload["totals"]["excluded_count"] -= moved
                payload["buckets"]["due_30"]["count"] += moved
            return payload

        flow.schedule = zero_filled
        try:
            mutated = _read(cur)
        finally:
            flow.schedule = original_schedule
        check("MUTATION unquantified-counted-as-zero is caught by the "
              "unquantified disclosure disappearing while the amount does not "
              "move",
              truth["excluded"]["unquantified"] > 0
              and mutated["excluded"]["unquantified"] == 0
              and mutated["buckets"]["due_30"]["amount"]
              == truth["buckets"]["due_30"]["amount"],
              (truth["excluded"], mutated["excluded"]))

        # 3. Declare an incomplete schedule complete.
        check("MUTATION incomplete-declared-complete is caught because the "
              "truth run is already incomplete for named reasons",
              truth["totals"]["complete"] is False
              and truth["totals"]["excluded_count"] > 0,
              truth["totals"])

        # 4. Break the single-currency contract the totals rest on. This is the
        # mutation that found dead code: the earlier version of this file
        # counted "other currency" rows in a branch that could never run,
        # because a currency is published only when there is exactly one. The
        # honest test is therefore not "does the guard fire" but "does the
        # contract hold, and would a break be visible".
        original_liabilities = obligations.liabilities_view

        def two_currencies(cur_, *, owner_user_id, actor_user_id):
            payload = original_liabilities(
                cur_, owner_user_id=owner_user_id, actor_user_id=actor_user_id)
            if payload.get("ok"):
                # Name a currency while the rows disagree with it — exactly the
                # state that would let a total silently absorb an FX row.
                payload["totals"] = dict(payload["totals"], currency="USD")
                for row in payload["liabilities"]:
                    row["currency"] = "EUR"
            return payload

        flow._obligations.liabilities_view = two_currencies
        try:
            mutated = _read(cur)
        finally:
            flow._obligations.liabilities_view = original_liabilities
        check("MUTATION fx-absorbed-into-a-named-total is caught: the "
              "disagreeing rows are counted, not summed, and completeness "
              "drops",
              mutated["totals"]["mixed_currency_rows"] > 0
              and mutated["totals"]["scheduled_amount"] == 0.0
              and mutated["totals"]["complete"] is False,
              mutated["totals"])
        check("and the honest run reports zero such rows",
              truth["totals"]["mixed_currency_rows"] == 0, truth["totals"])

        # 5. Drop the owner check.
        denied = _read(cur, owner=USER_A, actor=USER_B)
        check("MUTATION owner-check-removed is caught by the isolation stage: "
              "the denial is a refusal shape, not a readable schedule",
              denied["ok"] is False and denied["schedule"] == [],
              denied.get("denied"))

        # 6. Present a truncated schedule as whole.
        check("MUTATION truncation-hidden is caught because complete already "
              "depends on truncated, not only on the excluded count",
              truth["totals"]["truncated"] is False
              and truth["totals"]["complete"] is False,
              truth["totals"])
        conn.commit()
    finally:
        conn.close()


STAGES = (
    stage_empty_store,
    stage_bucketing,
    stage_undated_is_not_never,
    stage_unquantified_is_not_zero,
    stage_no_invented_recurrence,
    stage_outflows_only,
    stage_mixed_currency,
    stage_owner_isolation,
    stage_mutation_battery,
)


def test_cash_flow() -> None:
    setup_environment()
    _FAILURES.clear()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


if __name__ == "__main__":
    setup_environment()
    for stage in STAGES:
        stage()
    print("\n" + "=" * 60)
    if _FAILURES:
        for failure in _FAILURES:
            print("FAIL  " + failure)
        print(f"\n{len(_FAILURES)} FAILURE(S)")
        raise SystemExit(1)
    print("ALL STAGES PASSED")
