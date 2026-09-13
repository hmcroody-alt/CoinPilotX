"""Transaction-time supplier revalidation — the gate before the money. §22.

What this file is defending
---------------------------
``§23``/``§24`` keep a listing in step with its supplier on a 900-second cadence.
``services/marketplace_supplier_checkout.py`` covers the gap between ticks: the
last instant before a buyer is charged, when a sell-out that happened four
minutes ago is still invisible to every row a checkout lane reads.

The gate is a pure function over stored evidence, so this file is a pure unit
suite — in-memory SQLite, schema built by ``ensure_supplier_schema`` so the tables
under test are the ones production gets. There is no provider call here and there
must never be one; §37 says real CJ spend is zero, and the module's whole design
argument is that a checkout may not make a network call while holding a write
transaction open.

The three ways this gate goes quietly wrong
-------------------------------------------
Each of these passes an obvious test suite and is the reason a specific test
below exists:

* **It refuses everything.** ``supplier_worker`` has a Procfile entry but runs
  dark — ``CJ_RECONCILIATION_ENABLED`` and ``CJ_NETWORK_ENABLED`` are both unset,
  and the drain latch is only written past both gates — and ``link_source`` never
  writes ``last_synced_at``, so on this deployment every drop-shipped listing has
  a NULL confirmation. A gate that demanded freshness
  anyway would take the whole drop-shipped catalogue off sale on deploy and call
  it a safety feature. The strictness is therefore derived from the reconciler's
  own drain latch, and the latch has *four* states — the draft of this module had
  three, and omitting ``DRAIN_STALLED`` inverted the design: a worker that ran
  once and died reads as healthy forever, so freshness gets demanded of a
  reconciler that stopped. That is
  ``test_a_stalled_reconciler_does_not_refuse_every_checkout``.
* **It fails open silently.** ``reconciliation_evidence`` catches every exception
  and reports "never drained", which is correct for a deployment that has not
  initialised the supplier subsystem and catastrophic if a column simply got
  renamed — the gate would stop demanding freshness and nothing would say so.
  Hence the structural test pinning the latch's column names against the code
  that writes them.
* **It cannot read its own evidence.** ``last_synced_at`` has exactly one
  production writer (``revisions.py:632``, via ``_row_time``). If that format and
  this parser disagree, every listing looks unconfirmed forever. Pinned by
  feeding the real writer's output to the real parser rather than by a literal.

Unknown is not sold out, here as everywhere: a gate that read UNKNOWN stock as a
refusal would invent the sell-out §1 forbids fabricating.
"""

import inspect
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_supplier_checkout as gate
from services import marketplace_supplier_schema as schema
from services import marketplace_variants as variants
from services.business_os.suppliers import fulfillment, revisions, worker

SELLER = 1001

DROPSHIP = 10      # a supplier-fulfilled listing
STOCKED = 20       # imported, but the merchant holds the stock
UNSOURCED = 30     # authored in PulseSoc, no supplier at all
DROPSHIP_B = 40    # a second supplier-fulfilled listing, for multi-line baskets

NOW = datetime(2026, 9, 13, 12, 0, 0)

#: The latch table exactly as `fulfillment.ensure_schema` declares it. Copied
#: rather than created by calling that function, because `ensure_schema` opens its
#: own `db.connect()` against the real database and this suite must stay inside
#: its in-memory cursor. `test_the_latch_columns_this_gate_reads_are_the_ones_
#: fulfillment_writes` is what keeps the copy honest.
DRAIN_TICKS_DDL = ("CREATE TABLE business_os_supplier_drain_ticks ("
                   "scope TEXT PRIMARY KEY, started_at DOUBLE PRECISION, "
                   "completed_at DOUBLE PRECISION)")


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            title TEXT,
            price_label TEXT,
            status TEXT,
            approval_status TEXT,
            quantity INTEGER
        )
    """)
    for listing_id, title in ((DROPSHIP, 'Dropshipped tee'),
                              (STOCKED, 'Self-stocked tee'),
                              (UNSOURCED, 'Hand-authored tee'),
                              (DROPSHIP_B, 'Second dropshipped tee')):
        cursor.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, price_label, "
            "status, approval_status, quantity) VALUES (?, ?, ?, '$20.00', 'live', "
            "'approved', 5)", (listing_id, SELLER, title))
    cursor.execute(DRAIN_TICKS_DDL)
    result = schema.ensure_supplier_schema(cursor, force=True)
    assert result["status"] == schema.STATUS_READY, result
    yield cursor
    conn.close()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def bind(cur, *, listing_id=DROPSHIP, mode=None, sync_state=None,
         provider_variant_id="20001"):
    """Give a listing a supplier, through the only writer of that fact."""
    return variants.link_source(
        cur, listing_id=listing_id, seller_user_id=SELLER, provider="cj",
        provider_product_id="10001", provider_variant_id=provider_variant_id,
        fulfillment_mode=mode or schema.MODE_DROPSHIP, sync_state=sync_state)


def add_variant(cur, *, listing_id=DROPSHIP, stock_state=None, stock_quantity=5,
                value="S", status=None):
    variant_id = variants.upsert_variant(
        cur, listing_id=listing_id, seller_user_id=SELLER,
        options=[{"name": "Size", "value": value}], sku=f"SKU-{value}",
        price_cents=2000, cost_cents=800, currency="USD",
        stock_state=stock_state or schema.STOCK_IN_STOCK,
        stock_quantity=stock_quantity)
    if status:
        cur.execute("UPDATE marketplace_listing_variants SET status=? WHERE id=?",
                    (status, int(variant_id)))
    return variant_id


def confirm(cur, *, listing_id=DROPSHIP, age_seconds=60, sync_state=None):
    """Backdate a supplier confirmation.

    A direct UPDATE, because ``link_source`` has no ``last_synced_at`` parameter —
    the column's only production writer is ``revisions``, after a read it actually
    applied. Driving a whole revision here would test §23 again rather than this
    gate; the format that writer uses is pinned separately, against the writer's
    own helper.
    """
    # `.replace(tzinfo=utc)` before `.timestamp()`, not after: bare
    # `naive.timestamp()` interprets the value as *local* time, and the first draft
    # of this helper did exactly that — putting every "backdated" confirmation
    # hours in the future, where a negative age sails through a `>` comparison and
    # made every ALLOW assertion below pass for the wrong reason.
    moment = (NOW - timedelta(seconds=age_seconds)).replace(tzinfo=timezone.utc)
    stamp = revisions._row_time(moment.timestamp())
    cur.execute("UPDATE marketplace_product_sources SET last_synced_at=?, sync_state=? "
                "WHERE listing_id=?",
                (stamp, sync_state or schema.SYNC_SYNCED, int(listing_id)))


def latch(cur, *, started=None, completed=None):
    """Set the drain latch. An upsert, because `scope` is the primary key.

    ``fulfillment._mark_drain`` writes it with ``ON CONFLICT(scope) DO UPDATE`` for
    the same reason: there is one row per scope for the life of the deployment.
    """
    cur.execute("INSERT INTO business_os_supplier_drain_ticks (scope, started_at, "
                "completed_at) VALUES('worker', ?, ?) ON CONFLICT(scope) DO UPDATE "
                "SET started_at=excluded.started_at, completed_at=excluded.completed_at",
                (started, completed))


def draining(cur):
    """The one latch state that means confirmations are actually being refreshed."""
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 30, completed=moment - 10)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["running"] is True, evidence
    return evidence


# ---------------------------------------------------------------------------
# Whose business this is
# ---------------------------------------------------------------------------

def test_a_listing_with_no_supplier_is_not_this_gates_business(cur):
    """NOT_APPLICABLE, and distinctly not ALLOW.

    A hand-authored listing has no supplier state to revalidate. Collapsing this
    into ALLOW would make "nothing to check" and "checked and satisfied" the same
    answer, and a lane could no longer tell them apart in an audit.
    """
    decision = gate.evaluate(cur, listing_id=UNSOURCED, now=NOW)
    assert decision["decision"] == gate.NOT_APPLICABLE
    assert decision["reason"] == "no_supplier"


def test_a_listing_the_merchant_stocks_themselves_is_not_this_gates_business(cur):
    """MODE_STOCKED is the merchant's count, not the supplier's.

    Imported from CJ, held in the merchant's own warehouse. A supplier's opinion
    about stock it is not shipping must not refuse the sale — the same rule
    ``revisions.apply_supplier_read`` follows when it skips STOCKED sources rather
    than writing over a merchant's count. Sold out at the supplier, too, to prove
    the mode check runs *before* the stock check and not after it.
    """
    bind(cur, listing_id=STOCKED, mode=schema.MODE_STOCKED)
    add_variant(cur, listing_id=STOCKED, stock_state=schema.STOCK_OUT_OF_STOCK)
    decision = gate.evaluate(cur, listing_id=STOCKED, now=NOW)
    assert decision["decision"] == gate.NOT_APPLICABLE
    assert decision["reason"] == "merchant_stocked"


def test_a_malformed_listing_reference_does_not_break_a_checkout(cur):
    """``coerce_listing_id`` raises; a checkout may not 500 on it.

    No lane can actually reach this — all three resolve the listing from
    ``marketplace_listings`` first — but the gate is called inside an open write
    transaction, and an exception escaping there costs the buyer their order for a
    programmer error. A reference naming no listing has no supplier row.
    """
    decision = gate.evaluate(cur, listing_id="not-a-listing", now=NOW)
    assert decision["decision"] == gate.NOT_APPLICABLE
    assert decision["reason"] == "no_supplier"


# ---------------------------------------------------------------------------
# Sold out: the claim that needs the strongest evidence
# ---------------------------------------------------------------------------

def test_a_supplier_that_sold_out_every_variant_refuses_the_charge(cur):
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT
    assert decision["code"] == gate.REASON_SOLD_OUT
    assert decision["message"]


def test_a_sell_out_refuses_even_though_nothing_has_reconciled(cur):
    """Positively-bad state does not need a fresh confirmation to be true.

    This is the asymmetry that makes the unverified path safe. "We cannot confirm
    anything" is a reason to allow-and-mark; "the supplier told us there are none
    left" is a fact that did not expire because nobody asked again. If this test
    and ``test_a_deployment_that_never_reconciled_allows_and_says_so`` did not
    both pass, the never-drained path would be a blanket bypass.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "NO_DRAIN_HAS_EVER_RUN"
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT


def test_one_orderable_variant_is_enough_to_allow_the_sale(cur):
    """A sell-out means *every* active variant, because no lane names one.

    PulseSoc has no buyer-side variant selection, so a multi-variant listing
    arrives here with nothing chosen. Refusing because one variant sold out would
    be a guess about which the buyer wanted, and it would take a sellable listing
    off sale.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0, value="S")
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK, stock_quantity=4, value="M")
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["reason"] != gate.REASON_SOLD_OUT


def test_unknown_stock_is_not_treated_as_sold_out(cur):
    """§1/§7. The refusal must be the supplier's claim, never our inference.

    ``STOCK_UNKNOWN`` is what a failed or never-attempted read leaves behind, and
    it is the state every drop-shipped variant sits in on a deployment with no
    reconciler. Reading it as a sell-out would refuse the entire catalogue while
    reporting a supplier fact that no supplier ever stated.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_UNKNOWN, stock_quantity=None)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["reason"] != gate.REASON_SOLD_OUT


def test_an_archived_variant_does_not_keep_a_sold_out_listing_on_sale(cur):
    """Only active variants count toward "is anything sellable".

    ``archive_variant`` retires a variant without deleting it, because an order
    already placed must still be able to name what was bought. Counting a retired
    row as orderable would let one archived in-stock variant hold a genuinely
    sold-out listing open for charges.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0, value="S")
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK, stock_quantity=9, value="M",
                status="archived")
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT


def test_a_listing_with_no_variants_at_all_is_not_refused_as_sold_out(cur):
    """No rows is an absence of evidence, not evidence of a sell-out.

    ``gateway.bind_product`` writes a source row and no variants, so a listing sits
    in exactly this state between import and its first inventory read. ``all()`` on
    an empty list is True, which is why the sell-out branch is guarded on the list
    being non-empty — drop that guard and every freshly imported product refuses.
    """
    bind(cur)
    assert variants.variants_for(cur, DROPSHIP) == []
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["reason"] != gate.REASON_SOLD_OUT


# ---------------------------------------------------------------------------
# Freshness, and who is entitled to demand it
# ---------------------------------------------------------------------------

def test_a_deployment_that_never_reconciled_allows_and_says_so(cur):
    """The honest answer when freshness was never on offer.

    Allowed, because nothing about this listing got worse when the gate shipped —
    and marked, because the sale genuinely went through without a current
    confirmation and a post-mortem is entitled to know which ones did.
    """
    bind(cur)
    add_variant(cur)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["unverified"] is True
    assert decision["evidence_state"] == "NO_DRAIN_HAS_EVER_RUN"


def test_a_running_reconciler_refuses_a_listing_it_has_never_confirmed(cur):
    """Once something *is* re-reading, silence means a break rather than an absence.

    ``link_source`` never writes ``last_synced_at``, so a NULL here is the state
    every listing starts in. What changes its meaning is the reconciler being up:
    a listing it should have confirmed and has not is a listing whose supplier
    state is unknown for a reason.
    """
    bind(cur)
    add_variant(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION
    assert decision["confirmation_age_seconds"] is None


def test_a_freshly_confirmed_listing_is_allowed_without_reservation(cur):
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["unverified"] is False
    assert decision["sync_state"] == schema.SYNC_SYNCED


@pytest.mark.parametrize("age,expected", [
    (gate.CONFIRMATION_MAX_AGE_SECONDS - 1, gate.DECISION_ALLOW),
    (gate.CONFIRMATION_MAX_AGE_SECONDS + 1, gate.DECISION_REFUSE),
])
def test_the_confirmation_window_is_enforced_at_its_stated_edge(cur, age, expected):
    """Both sides of the boundary, so a flipped comparison cannot hide.

    A ``>=`` where the module has ``>`` is invisible to a test that only checks a
    confirmation from last week.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=age)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == expected


@pytest.mark.parametrize("sync_state", [
    schema.SYNC_STALE, schema.SYNC_ERROR, schema.SYNC_DISCONNECTED, schema.SYNC_REMOVED])
def test_a_recent_read_that_failed_refuses_despite_a_fresh_timestamp(cur, sync_state):
    """The timestamp says when a read last *succeeded*; the state says the last one did not.

    A listing whose supplier has become unreachable keeps whatever confirmation
    time it last earned. Trusting the clock alone would charge buyers for the
    whole duration of a supplier outage.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=30, sync_state=sync_state)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION


def test_the_window_is_three_of_the_reconcilers_own_inventory_cadences(cur):
    """Derived, not chosen — and pinned, because the derivation is a comment otherwise.

    The window tolerates two missed inventory ticks. If somebody slows the
    reconciler's cadence, the tolerance has to move with it or this gate silently
    starts refusing healthy listings; if somebody speeds it up, the window
    silently becomes wider than the gap §22 exists to close. Either way the
    constant and the cadence must be changed together, and this is what says so.
    """
    assert gate.CONFIRMATION_MAX_AGE_SECONDS == 3 * worker.CADENCE["inventory"]


# ---------------------------------------------------------------------------
# The drain latch: four states, and only one of them is strict
# ---------------------------------------------------------------------------

def test_a_stalled_reconciler_does_not_refuse_every_checkout(cur):
    """The bug this module shipped its first draft with.

    A worker that ran once and died leaves ``completed_at`` set forever. Reading
    that as DRAINING makes the gate demand freshness of something that stopped
    refreshing, which refuses every drop-shipped checkout for as long as the
    incident lasts — an outage amplified into a store-wide one. A stalled worker
    is not re-reading anything, which is the same situation as a worker that was
    never deployed, so it takes the same allow-and-mark path.
    """
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 60, completed=moment - fulfillment.DRAIN_STALL_SECONDS - 60)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "DRAIN_STALLED"
    assert evidence["running"] is False

    bind(cur)
    add_variant(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["unverified"] is True
    assert decision["evidence_state"] == "DRAIN_STALLED"


def test_a_reconciler_that_has_never_completed_a_tick_does_not_demand_freshness(cur):
    """Started, never finished — it has produced no confirmations to be stale against."""
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 30, completed=None)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "TICKING_BUT_NOT_COMPLETING"
    assert evidence["running"] is False


def test_only_a_draining_reconciler_makes_this_gate_strict(cur):
    """The enumeration itself, so a state cannot be quietly added to it."""
    assert gate.RUNNING_STATES == ("DRAINING",)


def test_a_stall_is_measured_against_the_window_fulfillment_owns(cur):
    """One authority for the stall window. §21.

    ``fulfillment.drain_status`` and this gate are two readers of one latch. A
    second, restated stall constant here would let them disagree about whether a
    worker has stopped — the admin surface calling it healthy while checkout
    treats it as dead, or the reverse.
    """
    assert gate.DRAIN_STALL_SECONDS is fulfillment.DRAIN_STALL_SECONDS
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 60, completed=moment - fulfillment.DRAIN_STALL_SECONDS + 30)
    assert gate.reconciliation_evidence(cur, now=NOW)["state"] == "DRAINING"


def test_a_deployment_without_the_latch_table_is_not_an_error(cur):
    """The supplier subsystem may simply not be initialised here.

    A checkout must not 500 because a table it only consults for advice is
    absent, and a deployment without that table has certainly never drained.
    """
    cur.execute("DROP TABLE business_os_supplier_drain_ticks")
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "NO_DRAIN_HAS_EVER_RUN"
    assert evidence["running"] is False


def test_the_latch_columns_this_gate_reads_are_the_ones_fulfillment_writes(cur):
    """Structural, because the fail-open is silent.

    ``reconciliation_evidence`` swallows every exception by design — a missing
    table is a legitimate state. The cost of that tolerance is that a renamed
    table or column also reads as "never drained", so the gate would stop
    demanding freshness and nothing anywhere would report it. Comparing the two
    sides against the source that writes them is the only way this stays true
    without a live worker in the suite.
    """
    written = inspect.getsource(fulfillment.ensure_schema)
    read = inspect.getsource(gate.reconciliation_evidence)
    for name in ("business_os_supplier_drain_ticks", "started_at", "completed_at"):
        assert name in written, f"{name} is no longer written by fulfillment"
        assert name in read, f"{name} is no longer read by the checkout gate"
    assert "scope" in written and "scope='worker'" in read


# ---------------------------------------------------------------------------
# Reading the evidence the reconciler actually writes
# ---------------------------------------------------------------------------

def test_the_timestamp_format_the_reconciler_writes_is_one_this_gate_can_read(cur):
    """The integration seam with no type to protect it.

    ``last_synced_at`` is TEXT with exactly one production writer
    (``revisions``, via ``_row_time``). If that spelling and this parser ever
    disagree, ``_parse`` returns None, every listing reads as never-confirmed, and
    a running reconciler refuses the entire drop-shipped catalogue. Asserted by
    handing the real writer's output to the real parser — a literal here would
    pass while production broke.
    """
    stamp = revisions._row_time(NOW.replace(tzinfo=timezone.utc).timestamp())
    assert gate._parse(stamp) == NOW
    assert gate._age_seconds(stamp, NOW) == 0


@pytest.mark.parametrize("stamp", [
    "2026-09-13T12:00:00", "2026-09-13T12:00:00Z", "2026-09-13T12:00:00+00:00"])
def test_every_spelling_of_the_same_instant_reads_as_the_same_instant(cur, stamp):
    """Naive is what this column holds; the suffixed forms must not crash a checkout."""
    assert gate._parse(stamp) == NOW


def test_a_confirmation_dated_in_the_future_is_not_evidence_of_freshness(cur):
    """Found by this suite's own bug, which is why it is worth a test.

    The helper above originally called ``.timestamp()`` on a naive datetime, which
    Python reads as local time — every "backdated" confirmation landed hours in
    the future. A negative age passes ``age > MAX`` trivially, so the gate read
    those rows as fresh and every ALLOW assertion in this file passed for the
    wrong reason. The same shape arrives in production from clock skew between the
    writer and this reader, or from a bad write: a stamp ahead of now is not a
    recent read. Tolerated up to :data:`CLOCK_SKEW_TOLERANCE_SECONDS`, because
    refusing checkouts over NTP drift would be its own outage.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=-(gate.CLOCK_SKEW_TOLERANCE_SECONDS + 60))
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION

    confirm(cur, age_seconds=-(gate.CLOCK_SKEW_TOLERANCE_SECONDS - 60))
    tolerated = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert tolerated["decision"] == gate.DECISION_ALLOW


def test_an_unreadable_timestamp_is_not_evidence_of_freshness(cur):
    """Garbage in the column is the same as nothing in it — and must not raise.

    Refused rather than allowed, because with a reconciler running an
    unreadable confirmation is not a confirmation.
    """
    bind(cur)
    add_variant(cur)
    cur.execute("UPDATE marketplace_product_sources SET last_synced_at='whenever', "
                "sync_state=? WHERE listing_id=?", (schema.SYNC_SYNCED, DROPSHIP))
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION


# ---------------------------------------------------------------------------
# What leaves this module
# ---------------------------------------------------------------------------

def test_every_answer_carries_every_key(cur):
    """A caller must never have to ask whether a field exists.

    Three lanes read this dict. A key present only on refusals is a
    ``KeyError`` in whichever lane forgets, at the moment a buyer is being charged.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK)
    confirm(cur)
    answers = [
        gate.evaluate(cur, listing_id=UNSOURCED, now=NOW),
        gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW),
        gate.evaluate(cur, listing_id=DROPSHIP, now=NOW),
    ]
    expected = {"decision", "reason", "message", "code", "unverified",
                "evidence_state", "sync_state", "confirmation_age_seconds"}
    for answer in answers:
        assert expected <= set(answer), answer


def test_a_refusal_tells_the_buyer_nothing_about_the_merchants_supplier(cur):
    """§27 applies to a refusal as much as to a success.

    "Our CJ connection is down" tells a buyer that this merchant drop-ships and
    from whom — the merchant's commercial arrangement, which they did not choose
    to publish. Both messages also have to say the buyer was not charged, because
    a refusal at this seam happens before the charge and a buyer who thinks
    otherwise opens a dispute.
    """
    for code in gate.REFUSAL_CODES:
        message = gate.MESSAGES[code]
        lowered = message.lower()
        for leak in ("cj", "supplier", "provider", "warehouse", "connection"):
            assert leak not in lowered, f"{code} names {leak}: {message}"
        assert "not been charged" in lowered


# ---------------------------------------------------------------------------
# The basket entry point the lanes actually call
# ---------------------------------------------------------------------------

def test_a_basket_is_refused_whole_on_its_first_bad_line(cur):
    """The lanes charge per basket, so there is no partial outcome to report."""
    bind(cur, listing_id=DROPSHIP_B, provider_variant_id="20002")
    add_variant(cur, listing_id=DROPSHIP_B, stock_state=schema.STOCK_OUT_OF_STOCK,
                stock_quantity=0)
    bind(cur)
    add_variant(cur)
    confirm(cur)
    draining(cur)

    screened = gate.screen(cur, [DROPSHIP_B, DROPSHIP], now=NOW)
    assert screened["refusal"]["reason"] == gate.REASON_SOLD_OUT
    assert screened["refused_listing_id"] == DROPSHIP_B
    # Stopped at the first refusal rather than judging the rest of the basket.
    assert DROPSHIP not in screened["decisions"]


def test_a_basket_every_line_of_which_is_sellable_is_not_refused(cur):
    bind(cur)
    add_variant(cur)
    confirm(cur)
    draining(cur)
    screened = gate.screen(cur, [DROPSHIP, UNSOURCED], now=NOW)
    assert screened["refusal"] is None
    assert screened["refused_listing_id"] is None
    assert screened["decisions"][DROPSHIP]["decision"] == gate.DECISION_ALLOW
    assert screened["decisions"][UNSOURCED]["decision"] == gate.NOT_APPLICABLE


def test_one_basket_is_judged_against_one_reading_of_the_latch(cur):
    """The latch is read once per checkout, not once per line.

    Two lines of one cart are being judged at one instant against one reconciler.
    Re-reading per line would let a basket refuse its second line on evidence its
    first was allowed under — and the read is a query on the caller's cursor, so
    the cost is real and paid per line.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur)
    draining(cur)

    reads = {"count": 0}
    real_execute = cur.execute

    class Counting:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "business_os_supplier_drain_ticks" in sql:
                reads["count"] += 1
            return real_execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    gate.screen(Counting(cur), [DROPSHIP, DROPSHIP, DROPSHIP], now=NOW)
    assert reads["count"] == 1, f"latch read {reads['count']} times for one basket"


def test_a_sell_out_reaches_the_buyer_as_a_code_their_client_already_knows(cur):
    """One fact, one buyer-facing code, whichever subsystem noticed it.

    ``mobile-native/src/api/marketplaceErrors.ts`` maps a fixed vocabulary to
    copy. A supplier sell-out is the same fact to a buyer as any other sell-out, so
    it travels as ``OUT_OF_STOCK`` and gets the copy that already exists. Inventing
    a second code for one fact would make the buyer's screen depend on which part
    of PulseSoc found out.
    """
    copy = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "mobile-native", "src", "api", "marketplaceErrors.ts")
    with open(copy, encoding="utf-8") as handle:
        client = handle.read()

    wire = gate.refusal_code({"reason": gate.REASON_SOLD_OUT})
    assert wire == "OUT_OF_STOCK"
    assert f"  {wire}: \"" in client, f"{wire} is not in the client's copy map"


def test_the_unconfirmed_refusal_carries_its_own_prose_because_the_client_cannot_map_it(cur):
    """The deliberate gap, asserted so it cannot become an accident.

    ``SUPPLIER_UNCONFIRMED`` has no client mapping on purpose: the nearest existing
    code, ``ITEM_UNAVAILABLE``, reads as permanent and this state is transient, so
    that copy would tell the buyer to give up on an item that will be back. It
    therefore relies on ``buyerErrorCopy``'s fallback to server prose for a handled
    4xx — which only works if the message is complete on its own, and only stays
    true while that fallback exists. Both halves are checked here, because if
    somebody adds the code to the client this test should fail and be deleted
    rather than quietly keep passing.
    """
    copy = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "mobile-native", "src", "api", "marketplaceErrors.ts")
    with open(copy, encoding="utf-8") as handle:
        client = handle.read()

    wire = gate.refusal_code({"reason": gate.REASON_STALE_CONFIRMATION})
    assert wire == gate.REASON_STALE_CONFIRMATION
    assert wire not in client
    assert "error.message" in client, ("the client no longer falls back to server "
                                       "prose; this refusal now needs a mapped code")
    message = gate.MESSAGES[gate.REASON_STALE_CONFIRMATION]
    assert "not been charged" in message.lower()
    assert "try again" in message.lower()


def test_the_audit_helper_finds_a_lines_own_decision(cur):
    bind(cur)
    add_variant(cur)
    screened = gate.screen(cur, [DROPSHIP], now=NOW)
    assert gate.audit_for(screened, DROPSHIP)["supplier_unverified"]
    assert gate.audit_for(screened, UNSOURCED) == {}
    assert gate.audit_for(screened, "nonsense") == {}


def test_audit_records_the_unverified_sale_and_nothing_else(cur):
    """Only the handful worth a post-mortem, and nothing §27 forbids storing.

    An annotation on every order would bury the ones that matter, and a refusal
    never reaches a transaction row to be annotated.
    """
    bind(cur)
    add_variant(cur)
    unverified = gate.evaluate(cur, listing_id=DROPSHIP,
                               evidence=gate.reconciliation_evidence(cur, now=NOW), now=NOW)
    recorded = gate.audit(unverified)
    assert recorded["supplier_unverified"]["reconciliation"] == "NO_DRAIN_HAS_EVER_RUN"
    blob = repr(recorded).lower()
    for leak in ("cj", "10001", "cost", "secret", "token"):
        assert leak not in blob, f"audit leaked {leak}: {recorded}"

    confirm(cur)
    confirmed = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert confirmed["unverified"] is False
    assert gate.audit(confirmed) == {}
    assert gate.audit({"decision": gate.DECISION_REFUSE, "unverified": True}) == {}
