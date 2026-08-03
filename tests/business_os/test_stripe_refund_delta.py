"""A cumulative refund total must not be posted as if it were a delta.

## The defect

The handler mapped every refund event with:

    amount = _coerce_amount_cents(obj.get("amount_refunded"), obj.get("amount"))

On a Stripe Charge, `amount_refunded` is the *cumulative* total refunded against
that charge, and `charge.refunded` fires again on every partial refund carrying a
larger running total. A $5 refund followed by a $3 refund produced one event
saying 500 and a second saying 800. The two events have different ids, so the
ledger's idempotency key — derived from the event id — correctly admitted both,
and $13 left the ledger against $8 of actual refunds.

The idempotency was never the broken part. The field was. Which is why no
existing test caught it: every test replayed the *same* event, and replaying the
same event was always handled correctly.

## The fix these tests pin

Mapping is keyed on the **refund**, not the event. A `charge.refunded` expands
`refunds.data` into one posting per Refund object, each keyed
`stripe:refund:<refund_id>` and each carrying its own `amount` — a real delta.
`refund.created` and `charge.refunded` describing the same refund therefore
collapse onto one ledger entry, which per-event-id keying could never do.

Where a Charge arrives with no expandable refund list, the cumulative total is
kept but the handler subtracts what has already been posted for that charge,
found via the `related_object` tag every refund posting carries.

Executable two ways:

    python -m pytest tests/business_os/test_stripe_refund_delta.py
    python tests/business_os/test_stripe_refund_delta.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_refunddelta_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402
from services.business_os.payments import webhook_inbox  # noqa: E402
from services.business_os.payments import stripe_ledger_handler as slh  # noqa: E402

USER = 42
ACCOUNT = f"user:{USER}"


def setup_module(module=None):
    ledger.ensure_schema()
    webhook_inbox.ensure_schema()


def _reset():
    conn = db.connect()
    for t in ("ledger_entries", "ledger_transactions", "ledger_balances",
              "provider_webhook_events"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _fund(cents=100_000):
    """Put real money in the account so refunds are not blocked by overdraft.

    The overdraft guard is a backstop, not the subject of these tests; without
    funding, an over-refund would be refused and the double-count would hide
    behind a LedgerError rather than showing up as a wrong balance.
    """
    slh.handle_stripe_event({
        "id": "evt_fund", "type": "payment_intent.succeeded",
        "data": {"object": {"amount": cents, "currency": "usd",
                            "metadata": {"pulse_user_id": USER}}},
    })
    assert ledger.get_balance(ACCOUNT) == cents


def _refunded_total():
    """Every cent this account has had debited by a refund posting."""
    conn = db.connect()
    try:
        return int(conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM ledger_transactions "
            "WHERE entry_type = 'refund' AND source_account = ?", (ACCOUNT,)
        ).fetchone()[0])
    finally:
        conn.close()


def _refund_rows():
    conn = db.connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM ledger_transactions WHERE entry_type = 'refund'"
        ).fetchone()[0])
    finally:
        conn.close()


def _signed_sum():
    conn = db.connect()
    try:
        return int(conn.execute(
            "SELECT COALESCE(SUM(signed_amount_cents),0) FROM ledger_entries"
        ).fetchone()[0])
    finally:
        conn.close()


def _refund(rid, amount, charge="ch_1"):
    return {"id": rid, "object": "refund", "amount": amount, "currency": "usd",
            "charge": charge, "metadata": {"pulse_user_id": USER}}


def _charge_refunded(event_id, cumulative, refunds, charge="ch_1"):
    """A `charge.refunded` event: cumulative total plus the refund list."""
    return {
        "id": event_id, "type": "charge.refunded",
        "data": {"object": {
            "id": charge, "object": "charge", "currency": "usd",
            "amount_refunded": cumulative,
            "refunds": {"object": "list", "data": refunds},
            "metadata": {"pulse_user_id": USER},
        }},
    }


# --------------------------------------------------------------------------

def test_two_partials_debit_the_sum_of_the_partials_not_the_running_total():
    """The headline regression. Fails against the pre-fix mapping.

    Before the fix this debited 500 then 800 — 1300 against 800 of real refunds.
    """
    _reset()
    _fund()

    slh.handle_stripe_event(
        _charge_refunded("evt_1", 500, [_refund("re_1", 500)]))
    after_first = _refunded_total()

    slh.handle_stripe_event(
        _charge_refunded("evt_2", 800, [_refund("re_1", 500), _refund("re_2", 300)]))
    after_second = _refunded_total()

    assert after_first == 500, after_first
    assert after_second == 800, (
        f"posted {after_second} against 800 of actual refunds — the cumulative "
        f"total was treated as a delta")
    assert _refund_rows() == 2, "the redescribed first refund posted twice"
    assert ledger.get_balance(ACCOUNT) == 100_000 - 800
    assert _signed_sum() == 0


def test_refund_created_and_charge_refunded_collapse_onto_one_entry():
    """Two event families describing one refund are one money movement.

    This is the property per-event-id keying could not have: the ids differ, so
    the ledger had no way to know it was being told the same thing twice.
    """
    _reset()
    _fund()

    first = slh.handle_stripe_event({
        "id": "evt_rc", "type": "refund.created",
        "data": {"object": _refund("re_9", 700)},
    })
    second = slh.handle_stripe_event(
        _charge_refunded("evt_cr", 700, [_refund("re_9", 700)]))

    assert first["posted"] is True
    assert first["duplicate"] is False
    assert second["duplicate"] is True, (
        "charge.refunded re-posted a refund that refund.created had already posted")
    assert _refunded_total() == 700, _refunded_total()
    assert _refund_rows() == 1
    assert ledger.get_balance(ACCOUNT) == 100_000 - 700


def test_replaying_the_identical_event_is_still_a_no_op():
    """The property that already held must survive the rewrite."""
    _reset()
    _fund()
    evt = _charge_refunded("evt_same", 400, [_refund("re_s", 400)])
    slh.handle_stripe_event(evt)
    slh.handle_stripe_event(evt)
    slh.handle_stripe_event(evt)
    assert _refunded_total() == 400
    assert _refund_rows() == 1


def test_cumulative_fallback_subtracts_what_is_already_posted():
    """A Charge with no expandable refund list still must not post a total.

    Stripe does not always include `refunds.data` — it is a paginated sub-list
    and can arrive empty on a large charge. The total is then all we have, so
    the handler subtracts what it already posted for that charge.
    """
    _reset()
    _fund()
    bare_1 = {"id": "evt_b1", "type": "charge.refunded",
              "data": {"object": {"id": "ch_bare", "object": "charge",
                                  "currency": "usd", "amount_refunded": 500,
                                  "metadata": {"pulse_user_id": USER}}}}
    bare_2 = {"id": "evt_b2", "type": "charge.refunded",
              "data": {"object": {"id": "ch_bare", "object": "charge",
                                  "currency": "usd", "amount_refunded": 800,
                                  "metadata": {"pulse_user_id": USER}}}}
    slh.handle_stripe_event(bare_1)
    assert _refunded_total() == 500, _refunded_total()
    slh.handle_stripe_event(bare_2)
    assert _refunded_total() == 800, (
        f"{_refunded_total()} posted against an 800 cumulative total")
    assert ledger.get_balance(ACCOUNT) == 100_000 - 800


def test_cumulative_fallback_sees_refunds_posted_through_the_expanded_path():
    """The two paths must share a view of what this charge has refunded.

    If the delta were computed from `provider_reference` — which holds the refund
    id on the expanded path and the charge id here — the sum would come back
    zero and the full cumulative total would post on top of the individual
    refunds. That is the original defect reintroduced through the back door,
    which is why every refund posting carries a `related_object` charge tag.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(
        _charge_refunded("evt_x1", 500, [_refund("re_x", 500, charge="ch_mix")],
                         charge="ch_mix"))
    assert _refunded_total() == 500

    bare = {"id": "evt_x2", "type": "charge.refunded",
            "data": {"object": {"id": "ch_mix", "object": "charge",
                                "currency": "usd", "amount_refunded": 800,
                                "metadata": {"pulse_user_id": USER}}}}
    slh.handle_stripe_event(bare)
    assert _refunded_total() == 800, (
        f"{_refunded_total()} posted: the fallback could not see the refund the "
        f"expanded path had already posted for this charge")


def test_a_cumulative_total_that_is_already_covered_posts_nothing():
    """A late duplicate total must move no money at all, not a negative amount."""
    _reset()
    _fund()
    slh.handle_stripe_event(
        _charge_refunded("evt_c1", 900, [_refund("re_c", 900, charge="ch_late")],
                         charge="ch_late"))
    bare = {"id": "evt_c2", "type": "charge.refunded",
            "data": {"object": {"id": "ch_late", "object": "charge",
                                "currency": "usd", "amount_refunded": 900,
                                "metadata": {"pulse_user_id": USER}}}}
    res = slh.handle_stripe_event(bare)
    assert res["posted"] is False and res["duplicate"] is True, res
    assert _refunded_total() == 900
    assert _signed_sum() == 0


def _bare_charge_refunded(event_id, cumulative, charge="ch_1"):
    """`charge.refunded` with no expanded `refunds.data`.

    This is the *default* shape on current Stripe API versions: `refunds` is a
    paginated sub-list and is no longer expanded automatically, so the handler
    takes the cumulative fallback on an ordinary single refund — not just on the
    large-charge edge case the fallback was originally written for.
    """
    return {"id": event_id, "type": "charge.refunded",
            "data": {"object": {"id": charge, "object": "charge",
                                "currency": "usd", "amount_refunded": cumulative,
                                "metadata": {"pulse_user_id": USER}}}}


def test_a_cumulative_total_then_its_own_refund_event_posts_once():
    """The headline for the second defect: the netting has to run both ways.

    `charge.refunded` (no refund list, total 500) posts 500 under
    `stripe:charge_refund_total:ch_1:500`. `refund.created` for the refund that
    total described then posted *another* 500 under `stripe:refund:re_1` — a
    different key, so the ledger had no objection, and $10 left against a $5
    refund.

    The subtraction existed, but only one of the two paths performed it: a
    cumulative event netted off the individual refunds it could see, while an
    individual refund arriving afterwards saw nothing and added. On current
    Stripe API versions this is the ordering that actually happens, so this is a
    live path, not a hypothetical one.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_cum", 500))
    assert _refunded_total() == 500

    res = slh.handle_stripe_event({
        "id": "evt_ind", "type": "refund.created",
        "data": {"object": _refund("re_1", 500)},
    })
    assert _refunded_total() == 500, (
        f"{_refunded_total()} posted against a single 500 refund — the refund "
        f"event was added on top of the cumulative total that already described "
        f"it")
    assert res["posted"] is False and res["duplicate"] is True, res
    assert ledger.get_balance(ACCOUNT) == 100_000 - 500
    assert _signed_sum() == 0


def test_the_absorbed_refund_event_stays_absorbed_on_replay():
    """No ledger row is written for an absorbed refund, so replay must re-derive.

    The absorbed posting is skipped rather than stored, which means the ledger's
    idempotency key is not what protects it — the arithmetic is. Replay it until
    it either holds or doesn't.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_r0", 500))
    evt = {"id": "evt_r1", "type": "refund.created",
           "data": {"object": _refund("re_1", 500)}}
    for _ in range(4):
        slh.handle_stripe_event(evt)
    assert _refunded_total() == 500, _refunded_total()
    assert _refund_rows() == 1


def test_a_later_partial_still_posts_after_an_earlier_one_was_absorbed():
    """Absorbing must not become swallowing. The second refund is real money.

    Sequence: cumulative 500, its refund event (absorbed), then a genuinely new
    $3 refund. Stripe fires `charge.refunded` again with the new running total,
    which is what re-establishes the truth — so the charge ends at 800, and the
    second refund event is absorbed in its turn.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_p0", 500))
    slh.handle_stripe_event({"id": "evt_p1", "type": "refund.created",
                             "data": {"object": _refund("re_1", 500)}})
    slh.handle_stripe_event(_bare_charge_refunded("evt_p2", 800))
    slh.handle_stripe_event({"id": "evt_p3", "type": "refund.created",
                             "data": {"object": _refund("re_2", 300)}})
    assert _refunded_total() == 800, (
        f"{_refunded_total()} against 800 of actual refunds")
    assert ledger.get_balance(ACCOUNT) == 100_000 - 800


def test_the_refund_event_may_arrive_before_the_cumulative_one():
    """The ordering that already worked must keep working.

    With no cumulative claim on the charge yet, an individual refund is the only
    account of it and posts in full; the cumulative event that follows then nets
    to zero. This is the direction the one-way subtraction handled correctly, and
    the fix must not have traded one ordering for the other.
    """
    _reset()
    _fund()
    first = slh.handle_stripe_event({"id": "evt_i1", "type": "refund.created",
                                     "data": {"object": _refund("re_1", 500)}})
    assert first["posted"] is True and first["duplicate"] is False, first
    second = slh.handle_stripe_event(_bare_charge_refunded("evt_i2", 500))
    assert second["posted"] is False and second["duplicate"] is True, second
    assert _refunded_total() == 500
    assert _refund_rows() == 1


def test_two_individual_refunds_with_no_cumulative_event_both_post():
    """The cap keys on a cumulative claim, and there is none here.

    A charge whose refunds only ever arrive as `refund.created` events has no
    asserted total, so each refund is new money. If the cap were applied
    unconditionally this would post 500 and lose the 300.
    """
    _reset()
    _fund()
    slh.handle_stripe_event({"id": "evt_n1", "type": "refund.created",
                             "data": {"object": _refund("re_1", 500)}})
    slh.handle_stripe_event({"id": "evt_n2", "type": "refund.created",
                             "data": {"object": _refund("re_2", 300)}})
    assert _refunded_total() == 800, _refunded_total()
    assert _refund_rows() == 2


def test_one_charges_cumulative_claim_does_not_absorb_anothers_refund():
    """The cap is scoped per charge, like the delta it mirrors.

    A cumulative total asserted for `ch_a` says nothing about `ch_b`. Sharing the
    claim would silently drop a real refund — the same class of loss as the
    double-count, in the other direction.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_ca", 500, charge="ch_a"))
    slh.handle_stripe_event({"id": "evt_cb", "type": "refund.created",
                             "data": {"object": _refund("re_b", 300, charge="ch_b")}})
    assert _refunded_total() == 800, (
        f"{_refunded_total()}: ch_a's cumulative claim absorbed ch_b's refund")


def test_an_expanded_refund_list_is_not_absorbed_by_its_own_event():
    """The cap must not fire on the path that never needed it.

    A `charge.refunded` carrying `refunds.data` posts the individual refunds
    directly. Those postings run within the same event that would otherwise have
    asserted the total, and there is no prior claim for them to be netted
    against — so they must post in full.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_charge_refunded(
        "evt_e1", 800, [_refund("re_e1", 500), _refund("re_e2", 300)]))
    assert _refunded_total() == 800, _refunded_total()
    assert _refund_rows() == 2


def test_a_refund_naming_no_charge_is_never_absorbed():
    """Without a charge there is nothing to compare against, so it posts.

    Failing the other way would let an unattributable refund be silently dropped
    because some unrelated charge happened to hold a claim.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_o1", 500))
    res = slh.handle_stripe_event({
        "id": "evt_o2", "type": "refund.created",
        "data": {"object": {"id": "re_orphan", "object": "refund", "amount": 250,
                            "currency": "usd",
                            "metadata": {"pulse_user_id": USER}}},
    })
    assert res["posted"] is True, res
    assert _refunded_total() == 750, _refunded_total()


def test_the_charge_state_reports_the_claimed_total_not_the_delta_posted():
    """The two numbers differ, and reading the wrong one reinstates the bug.

    A cumulative posting's `amount_cents` is the delta it moved — 300 on an 800
    total that already had 500 posted. The claim over the charge is 800. If the
    cap compared against the delta it would leave 500 of headroom and the
    individual refunds would post into it.
    """
    _reset()
    _fund()
    slh.handle_stripe_event(_bare_charge_refunded("evt_s1", 500, charge="ch_s"))
    slh.handle_stripe_event(_bare_charge_refunded("evt_s2", 800, charge="ch_s"))
    posted, established = slh._charge_refund_state("ch_s", "usd")
    assert posted == 800, posted
    assert established == 800, (
        f"established total {established}: read from the delta the second event "
        f"moved (300) rather than the total it asserted (800)")


def test_two_charges_do_not_share_a_refund_budget():
    """Deltas are scoped per charge; one charge's refunds are not another's."""
    _reset()
    _fund()
    slh.handle_stripe_event(
        _charge_refunded("evt_a", 500, [_refund("re_a", 500, charge="ch_a")],
                         charge="ch_a"))
    bare_b = {"id": "evt_b", "type": "charge.refunded",
              "data": {"object": {"id": "ch_b", "object": "charge",
                                  "currency": "usd", "amount_refunded": 500,
                                  "metadata": {"pulse_user_id": USER}}}}
    slh.handle_stripe_event(bare_b)
    assert _refunded_total() == 1000, (
        f"{_refunded_total()}: a second charge's refund was mistaken for the "
        f"first charge's, and money that should have moved did not")


def test_multiple_refunds_in_one_event_all_post():
    """One event, three refunds, three postings — not one and not the total."""
    _reset()
    _fund()
    res = slh.handle_stripe_event(_charge_refunded(
        "evt_multi", 600,
        [_refund("re_m1", 100), _refund("re_m2", 200), _refund("re_m3", 300)]))
    assert "postings" in res and len(res["postings"]) == 3, res
    assert _refunded_total() == 600
    assert _refund_rows() == 3


def test_unmapped_refund_still_routes_to_suspense():
    """Losing the account must not mean losing the money."""
    _reset()
    res = slh.handle_stripe_event({
        "id": "evt_un", "type": "refund.created",
        "data": {"object": {"id": "re_un", "object": "refund", "amount": 250,
                            "currency": "usd", "charge": "ch_un"}},
    })
    assert res["posted"] is True and res["unmapped"] is True, res
    assert ledger.get_balance(slh.SUSPENSE) == -250
    assert _signed_sum() == 0


def test_the_mapper_is_still_pure():
    """`map_stripe_postings` must not need a database.

    The delta arithmetic lives in the handler precisely so the mapping stays
    testable without one; a read sneaking into the mapper would make every
    future mapping test require a schema.
    """
    postings = slh.map_stripe_postings(
        _charge_refunded("evt_pure", 800, [_refund("re_p1", 500),
                                           _refund("re_p2", 300)]))
    assert [p["amount_cents"] for p in postings] == [500, 300], postings
    assert [p["idempotency_key"] for p in postings] == [
        "stripe:refund:re_p1", "stripe:refund:re_p2"]
    assert all(p["related_object"] == "stripe_charge:ch_1" for p in postings)
    assert not any("cumulative_for_charge" in p for p in postings)


def test_amount_refunded_is_no_longer_read_as_a_delta():
    """Pin the source, so a well-meaning revert is caught as a failure.

    Walks the AST rather than grepping, for two reasons. The docstrings explain
    the defect by quoting the old expression, so a substring search would match
    the warning as readily as a relapse. And `amount_refunded` legitimately
    appears in a type discriminator — `"amount_refunded" not in obj` tells a
    Charge from a Refund — which is a membership test, not a read.

    What must not come back is *fetching* the field as an amount: every
    `.get("amount_refunded")` has to land in a name that says total, because a
    name that says total is one a reviewer will not pass to `post_entry`.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(slh))
    reads = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if "amount_refunded" not in ast.unparse(node.value):
            continue
        reads += 1
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        assert names and all(("total" in n or "cumulative" in n) for n in names), (
            f"amount_refunded is read into {names}, a name that does not say it "
            f"is a running total: {ast.unparse(node)}")
    assert reads == 1, (
        f"expected exactly one read of amount_refunded (the cumulative "
        f"fallback); found {reads}")


# --------------------------------------------------------------------------

def _main():
    setup_module()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
