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

  This suite shipped a version that got the *same* answer wrong a second way, and
  the second way is worth naming because the first fix hid it. Reading the latch
  fixed "is anything re-reading"; it did not fix "has anything re-read *this*".
  The latch flips to DRAINING within seconds of the first tick — ``run_once``
  records completion as its last statement even on a tick that claimed no work —
  while confirmations land one listing at a time, twenty jobs a tick, hourly. So
  the whole catalogue sits in "reconciler up, this listing never confirmed" for
  hours, and this file asserted REFUSE for that combination. Measured against
  production: 22 of 22 drop-shipped sources have ``last_synced_at IS NULL``, all
  reading ``sync_state='SYNCED'``, so the data looked healthy and enabling the
  worker would have refused every drop-shipped checkout. Now
  ``test_a_running_reconciler_allows_a_listing_it_has_not_reached_yet``, with
  ``test_a_confirmation_that_existed_and_went_stale_still_refuses`` holding the
  other half so the fix cannot degrade into "allow everything".
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
from services.business_os.suppliers import (fulfillment, revisions, store_policy,
                                            worker)

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
                value="S", status=None, provider_variant_id="20001",
                price_cents=2000, cost_cents=800):
    """One variant row, carrying the provider id that makes it identifiable.

    ``provider_variant_id`` defaults to ``bind``'s default, so the default
    ``bind`` + ``add_variant`` pair is a listing whose binding names a variant it
    actually has — which is what every live production listing is. It used to
    default to NULL, which made the default fixture a listing bound to a variant
    that did not exist: harmless while the gate only counted stock states across
    all rows, and invisible, because nothing in the suite asked the one question
    that can tell those two shapes apart.

    Pass a distinct id for a *sibling* variant. A listing is bound to exactly one
    supplier variant, so two rows sharing an id is drift, not a multi-variant
    product, and ``_bound_variant`` would resolve it by position.

    ``price_cents``/``cost_cents`` are parameters because the margin check reads
    them. The defaults are deliberately profitable ($20.00 against $8.00) so that
    a test about stock or sync is not silently also a test about money.
    """
    variant_id = variants.upsert_variant(
        cur, listing_id=listing_id, seller_user_id=SELLER,
        options=[{"name": "Size", "value": value}], sku=f"SKU-{value}",
        provider_variant_id=provider_variant_id,
        price_cents=price_cents, cost_cents=cost_cents, currency="USD",
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


def test_a_sibling_in_stock_does_not_rescue_a_sold_out_bound_variant(cur):
    """The stock question is about the bound variant, not the catalogue.

    This test used to assert the opposite, on the premise that "PulseSoc has no
    buyer-side variant selection, so a multi-variant listing arrives here with
    nothing chosen". That premise was wrong in both directions. The lanes do name
    a variant — the cart line carries ``variant_id`` and ``price_authority``
    says a named variant "wins outright" — and more fundamentally it does not
    matter whether anyone names one, because ``drafts._sold_variant`` is explicit
    that a drop-shipped listing "does not sell its variants. It sells exactly the
    variant named by ``provider_variant_id``", the other rows being "catalogue --
    what the supplier offers -- not stock this listing can sell".

    So allowing the sale here let a listing through on the strength of stock
    nobody could order, and ``create_intent`` would then refuse the only line it
    was willing to place — after the charge. Production measurement of this exact
    shape on 2026-10-03: zero listings, so the correction costs no live sales and
    closes the hole before it is first hit.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0,
                value="S", provider_variant_id="20001")
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK, stock_quantity=4,
                value="M", provider_variant_id="20002")
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT


def test_a_sold_out_sibling_does_not_block_the_bound_variant(cur):
    """The same rule in the direction that keeps a listing selling.

    The converse of the test above, and the reason the rule is variant-exact
    rather than merely stricter: a catalogue row selling out must not take a
    listing off sale when the variant that would actually ship is in stock.
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK, stock_quantity=4,
                value="S", provider_variant_id="20001")
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0,
                value="M", provider_variant_id="20002")
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
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK, stock_quantity=0,
                value="S", provider_variant_id="20001")
    add_variant(cur, stock_state=schema.STOCK_IN_STOCK, stock_quantity=9, value="M",
                status="archived", provider_variant_id="20002")
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT


def test_archiving_the_bound_variant_refuses_rather_than_falling_back(cur):
    """A binding that names a retired row proves nothing will ship.

    ``_orderable`` drops archived rows before the binding is resolved, so the
    bound id matches nothing and the listing is refused as unbound even though
    the column is set. That is deliberate, and it is ``drafts._sold_variant``'s
    stated contract: ``None`` "both when nothing is bound and when the bound id
    names a variant this listing does not have, which is drift rather than
    absence and is reported as the same problem".

    The alternative — falling back to any other active variant — is the guess
    this whole mission exists to refuse, and it would place an order for an item
    the seller never chose.
    """
    bind(cur)
    add_variant(cur, value="S", provider_variant_id="20001", status="archived")
    add_variant(cur, value="M", provider_variant_id="20002")
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_UNBOUND


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


def test_a_running_reconciler_allows_a_listing_it_has_not_reached_yet(cur):
    """The inverted assertion, and the reason it inverted.

    This test asserted REFUSE for one draft, on the argument that once something is
    re-reading, silence means a break. The argument is wrong about *when* the
    silence starts. ``worker.run_once`` writes its completion latch as the last
    statement of a tick and reaches it even having claimed zero jobs, so the latch
    reads DRAINING seconds after the worker boots; ``revisions`` writes
    ``last_synced_at`` one listing at a time, twenty jobs a tick, on an hourly
    product cadence. Every listing therefore spends hours in exactly this state,
    and it is not a break — it is a queue.

    Measured on production before this changed: 22 of 22 drop-shipped sources have
    ``last_synced_at IS NULL``, because ``link_source`` never writes the column and
    both ``importer`` and ``gateway`` create sources through it. The REFUSE version
    of this test was therefore a specification for taking 100% of the drop-shipped
    catalogue off sale the moment the worker was enabled, while telling every buyer
    to "try again shortly".

    Allowed and marked, then — the annotation says ``NEVER``, so the sale is
    distinguishable afterwards from one made under a stalled reconciler.
    """
    bind(cur)
    add_variant(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["unverified"] is True
    assert decision["confirmation"] == gate.CONFIRMATION_NEVER
    assert decision["confirmation_age_seconds"] is None
    # The latch is not misreported to make the allow look clean: the annotation
    # says a reconciler *was* running and this listing still had no confirmation,
    # which is the pair a post-mortem needs.
    assert decision["evidence_state"] == "DRAINING"


def test_a_never_confirmed_listing_still_refuses_when_the_supplier_said_sold_out(cur):
    """The allow above is about absent evidence, not about ignoring evidence.

    The danger in widening the never-confirmed case is that it becomes a blanket
    pass. It must not: positively-bad state is orthogonal to freshness, and a
    supplier that said "none left" has not become less sold out by nobody asking
    again since. This is the line between "we have no reason to refuse" and "we
    have a reason and chose not to look at it".
    """
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT


def test_a_confirmation_that_existed_and_went_stale_still_refuses(cur):
    """The other half of the distinction, and the half that keeps §22 worth having.

    Never-confirmed and went-stale produce the same ``confirmation_age_seconds``
    answer under the old code — ``None`` — and opposite decisions under the new
    one. A fix that allowed the first by accidentally allowing both would leave
    this gate unable to catch the thing it was built for: a listing whose supplier
    state was known and has since gone unverifiable.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION
    assert decision["confirmation"] == gate.CONFIRMATION_KNOWN


def test_absent_and_unreadable_confirmations_are_told_apart(cur):
    """The exact conflation that caused the bug, pinned at the helper.

    One comparison — ``age is None`` — stood for both "no stamp was ever written"
    and "a stamp was written and cannot be parsed". They are opposite facts: the
    first is the normal state of a listing awaiting its first reconciliation, the
    second means something is broken. Asserted on the helper rather than only
    through ``evaluate`` so that a future refactor which re-merges them fails here,
    at the cause, instead of in whichever behavioural test happens to notice.
    """
    assert gate._confirmation(None, NOW)[0] == gate.CONFIRMATION_NEVER
    assert gate._confirmation("", NOW)[0] == gate.CONFIRMATION_NEVER
    assert gate._confirmation("   ", NOW)[0] == gate.CONFIRMATION_NEVER
    assert gate._confirmation("whenever", NOW)[0] == gate.CONFIRMATION_UNREADABLE
    assert gate._confirmation("2026-13-45T99:00:00", NOW)[0] == gate.CONFIRMATION_UNREADABLE


@pytest.mark.parametrize("stamp", [
    None, "", "   ", "whenever", "2026-13-45T99:00:00", "2026-09-13T12:00:00",
    "2026-09-13T12:00:00Z", "2026-09-13T12:00:00+00:00", 0, 12345])
def test_a_confirmation_has_an_age_exactly_when_it_is_readable(stamp):
    """The invariant ``evaluate`` compares ages without a None guard *because of*.

    ``evaluate`` runs ``age > CONFIRMATION_MAX_AGE_SECONDS`` with no
    ``age is None`` check, having returned on the two states that produce no age.
    That is only safe while ``_confirmation`` pairs KNOWN with a float and every
    other state with None. A defensive guard in ``evaluate`` would be dead code
    that reads as load-bearing, so the invariant is asserted here instead — if it
    is ever broken, a checkout raises ``TypeError`` on a buyer.
    """
    state, age = gate._confirmation(stamp, NOW)
    assert state in gate.CONFIRMATION_STATES
    if state == gate.CONFIRMATION_KNOWN:
        assert isinstance(age, float)
    else:
        assert age is None


@pytest.mark.parametrize("sync_state", list(gate.FAILED_SYNC_STATES))
def test_a_failed_read_refuses_even_with_no_confirmation_and_no_reconciler(cur, sync_state):
    """Affirmative failure outranks the never-confirmed allow, and the latch.

    This is the ordering hazard the widened allow created, and it was found by a
    test that tried to *document* the hazard instead of removing it. The first
    arrangement checked these states below the never-confirmed allow, which made
    the outcome depend on whether the failed state happened to carry a timestamp.
    It does today — ``revisions`` sets ``sync_state`` and ``last_synced_at`` in one
    UPDATE — so the bug was unobservable, and the test guarding it was a source
    scan for future writers. That scan went off immediately on ``drafts.py``
    *reading* these constants, which is the tell that the guard was the wrong
    shape: a heuristic over the whole tree, protecting an ordering that did not
    need to exist.

    So these are now checked in the same tier as a sold-out variant, on the same
    principle: an affirmative report of failure is evidence, it does not expire,
    and it does not need a reconciler to be running to still be true. DISCONNECTED
    in particular means the merchant's credential stopped working — charging a
    buyer against it cannot be right no matter what the latch says.

    No confirmation and no latch row here, which is the combination that previously
    allowed.
    """
    bind(cur, sync_state=sync_state)
    add_variant(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP,
                             evidence=gate.reconciliation_evidence(cur, now=NOW), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION
    assert decision["confirmation"] == gate.CONFIRMATION_NEVER


def test_the_default_sync_state_is_not_treated_as_a_failure(cur):
    """PENDING is the column DEFAULT, so including it would refuse every import.

    ``marketplace_product_sources.sync_state`` defaults to PENDING, and a source
    exists from the moment ``link_source`` runs — before any read. Adding PENDING
    to :data:`FAILED_SYNC_STATES` would therefore refuse every freshly imported
    listing: the same catalogue-wide refusal this suite's docstring is about,
    arriving through the failure tier instead of the freshness one, and it would
    look like correctness because PENDING is not SYNCED.
    """
    assert schema.SYNC_PENDING not in gate.FAILED_SYNC_STATES
    bind(cur, sync_state=schema.SYNC_PENDING)
    add_variant(cur)
    confirm(cur, age_seconds=60, sync_state=schema.SYNC_PENDING)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW


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
# A reconciler that is running and still losing
# ---------------------------------------------------------------------------

#: The job queue exactly as `worker.ensure_schema` declares it, copied for the
#: same reason `DRAIN_TICKS_DDL` is: that function opens its own `db.connect()`.
#: `test_the_queue_columns_this_gate_reads_are_the_ones_worker_writes` keeps the
#: copy honest.
SYNC_JOBS_DDL = ("CREATE TABLE business_os_supplier_sync_jobs ("
                 "id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, "
                 "business_id TEXT NOT NULL, store_id TEXT NOT NULL, "
                 "kind TEXT NOT NULL, resource_id TEXT NOT NULL, "
                 "available_at DOUBLE PRECISION NOT NULL, "
                 "lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, "
                 "lease_token TEXT, failures INTEGER NOT NULL DEFAULT 0, "
                 "last_verified_at DOUBLE PRECISION, evidence_hash TEXT, "
                 "last_error TEXT, UNIQUE(connection_id,kind,resource_id))")


def queue(cur, *, overdue_by, kind="inventory", resource_id="10001"):
    """Put one job at the front of the reconciler's queue, `overdue_by` seconds late.

    ``available_at`` is epoch seconds, matching ``worker.schedule``; a positive
    ``overdue_by`` therefore backdates it. One row is enough because the gate reads
    ``MIN(available_at)`` — the front of the queue is the whole measurement.
    """
    cur.execute(SYNC_JOBS_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    cur.execute("INSERT INTO business_os_supplier_sync_jobs (id, connection_id, "
                "business_id, store_id, kind, resource_id, available_at) "
                "VALUES(?, 'conn', 'biz', 'store', ?, ?, ?)",
                (f"job-{kind}-{resource_id}", kind, resource_id, moment - overdue_by))


def test_a_reconciler_outrun_by_its_own_queue_does_not_demand_freshness(cur):
    """The production incident of 2026-10-01, as a test.

    The latch said DRAINING — a worker was ticking and completing. Every source
    was ``SYNCED``, every variant ``IN_STOCK``, no job had ever failed, and
    snapshots were 45 seconds old. And 18 of the 37 purchasable listings were
    refused with ``SUPPLIER_UNCONFIRMED``, because ``worker.run_once`` reads 20
    jobs a tick on a 300s tick and 196 products queue 392 recurring jobs, so a full
    sweep took 5925s against a 2700s tolerance. The worst confirmation age (6146s)
    and the worst inventory revisit lag (6145s) were the same number: the staleness
    belonged to the queue, not to any supplier.

    A worker losing to its own backlog is not refreshing this listing inside the
    window, which is the same situation as one that stalled or was never deployed —
    so it takes the same allow-and-mark path, and says which it was.
    """
    latch_draining = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=latch_draining - 30, completed=latch_draining - 10)
    queue(cur, overdue_by=gate.CONFIRMATION_MAX_AGE_SECONDS + 60)

    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == gate.DRAIN_BEHIND
    assert evidence["running"] is False

    bind(cur)
    add_variant(cur)
    # Staler than the tolerance: the exact row that was refusing in production.
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["unverified"] is True
    assert decision["evidence_state"] == gate.DRAIN_BEHIND
    assert decision["message"] == ""
    # And the sale is queryable afterwards as one we could not vouch for.
    assert gate.audit(decision)["supplier_unverified"]["reconciliation"] == gate.DRAIN_BEHIND


def test_a_queue_inside_the_window_still_demands_freshness(cur):
    """The other half, and the one that stops this being a disabled gate.

    A reconciler keeping up is exactly the situation tier 2 was written for: it
    reached other listings and not this one, so this one's silence means something.
    If the backlog branch above swallowed this case too, the fix would have removed
    the gate rather than corrected it.
    """
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 30, completed=moment - 10)
    queue(cur, overdue_by=gate.CONFIRMATION_MAX_AGE_SECONDS - 60)

    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "DRAINING"
    assert evidence["running"] is True

    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_STALE_CONFIRMATION


def test_a_sold_out_listing_still_refuses_while_the_queue_is_behind(cur):
    """Tier 1 is not traded away to get tier 2 unstuck.

    Widening the absent-evidence case is only defensible while affirmative bad
    evidence still refuses through it. A backlog is an absence of reassurance; a
    supplier saying "none left" is a statement, and it does not become less true
    because the queue is late.
    """
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 30, completed=moment - 10)
    queue(cur, overdue_by=gate.CONFIRMATION_MAX_AGE_SECONDS + 60)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == gate.DRAIN_BEHIND

    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=evidence, now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_SOLD_OUT

    # And so does a source whose last read affirmatively failed.
    bind(cur, listing_id=DROPSHIP_B, sync_state=schema.SYNC_DISCONNECTED)
    add_variant(cur, listing_id=DROPSHIP_B)
    failed = gate.evaluate(cur, listing_id=DROPSHIP_B, evidence=evidence, now=NOW)
    assert failed["decision"] == gate.DECISION_REFUSE
    assert failed["reason"] == gate.REASON_STALE_CONFIRMATION


def test_absence_of_queue_evidence_never_grants_leniency(cur):
    """Three ways to learn nothing about the backlog; none of them relaxes the gate.

    The asymmetry is the safety property of the backlog branch: it widens what this
    gate allows only on a positive, measured arrears. A missing table, an empty
    queue and a queue whose front is still in the future all leave the gate exactly
    as strict as it was before that branch existed — otherwise a renamed table
    would silently stop this gate demanding freshness, which is the failure mode
    ``test_the_latch_columns...`` exists to prevent on the other read.
    """
    moment = NOW.replace(tzinfo=timezone.utc).timestamp()
    latch(cur, started=moment - 30, completed=moment - 10)

    # 1. The jobs table does not exist on this deployment at all.
    assert gate.reconciliation_evidence(cur, now=NOW)["state"] == "DRAINING"

    # 2. It exists and is empty — no work queued, so none of it is late.
    cur.execute(SYNC_JOBS_DDL)
    assert gate.reconciliation_evidence(cur, now=NOW)["state"] == "DRAINING"

    # 3. The front of the queue is not due yet.
    queue(cur, overdue_by=-3600)
    assert gate.reconciliation_evidence(cur, now=NOW)["state"] == "DRAINING"
    assert gate._queue_overdue_by(cur, now=NOW) == 0.0


def test_the_backlog_threshold_is_the_confirmation_tolerance_itself(cur):
    """One constant, both halves. §21.

    The gate refuses a confirmation older than ``CONFIRMATION_MAX_AGE_SECONDS``, so
    the only coherent question to ask of the reconciler is whether it is dispatching
    inside that same window. A second, independent arrears constant could drift
    until the gate again demanded a freshness its queue was never going to deliver
    — which is precisely the defect being fixed, reintroduced as a tuning mistake.
    """
    source = inspect.getsource(gate.reconciliation_evidence)
    assert "CONFIRMATION_MAX_AGE_SECONDS" in source
    assert gate.DRAIN_BEHIND not in gate.RUNNING_STATES


def test_the_queue_columns_this_gate_reads_are_the_ones_worker_writes(cur):
    """Structural, because this read fails open silently too.

    ``_queue_overdue_by`` returns 0.0 on any exception, so a renamed table or
    column reads as "no backlog" — which keeps the gate strict rather than
    relaxing it, but would mean the backlog branch had quietly stopped working and
    nothing would report it. Same reasoning as the latch's structural test, same
    remedy.
    """
    written = inspect.getsource(worker.ensure_schema)
    read = inspect.getsource(gate._queue_overdue_by)
    for name in ("business_os_supplier_sync_jobs", "available_at"):
        assert name in written, f"{name} is no longer written by worker"
        assert name in read, f"{name} is no longer read by the checkout gate"


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
    assert gate._confirmation(stamp, NOW) == (gate.CONFIRMATION_KNOWN, 0)


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
                stock_quantity=0, provider_variant_id="20002")
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


def test_the_audit_separates_a_queued_listing_from_a_broken_reconciler(cur):
    """Two unverified sales, two different incidents, and the row has to say which.

    Widening the allow means more ``unverified`` sales, and they are no longer all
    the same event. "The reconciler was up and had not reached this listing yet" is
    expected and self-clearing — it should appear in bulk for a few hours after the
    worker is enabled and then stop. "Nothing was re-reading at all" is an outage.
    Both write an annotation, and a count of annotations that cannot tell them apart
    is a metric that alarms on the normal case and hides the abnormal one.

    This is what makes the widening auditable rather than merely permissive: the
    trade was "allow and mark", and a mark that omits the distinguishing fact has
    not held up the second half.
    """
    bind(cur)
    add_variant(cur)

    queued = gate.audit(gate.evaluate(cur, listing_id=DROPSHIP,
                                      evidence=draining(cur), now=NOW))
    assert queued["supplier_unverified"]["reconciliation"] == "DRAINING"
    assert queued["supplier_unverified"]["confirmation"] == gate.CONFIRMATION_NEVER

    dark = gate.audit(gate.evaluate(
        cur, listing_id=DROPSHIP,
        evidence={"state": "NO_DRAIN_HAS_EVER_RUN", "running": False}, now=NOW))
    assert dark["supplier_unverified"]["reconciliation"] == "NO_DRAIN_HAS_EVER_RUN"

    assert queued["supplier_unverified"] != dark["supplier_unverified"], (
        "both unverified cases produce the same annotation, so a post-mortem "
        "cannot tell a queued listing from a reconciler that never ran")


# ---------------------------------------------------------------------------
# §1/§2/§3/§24 — the two facts that were known before the charge and not asked
# ---------------------------------------------------------------------------

def policy(cur, *, allowance_cents, business_id="biz", store_id="store"):
    """A store import policy row, for the freight half of landed cost.

    Written with a plain INSERT rather than through ``store_policy.save``: that
    writer calls ``ensure_schema`` on its own ``db.connect()``, which would leave
    this suite's in-memory cursor and touch the real database. The columns are
    taken from ``store_policy``'s own DDL, and
    ``test_the_policy_columns_this_gate_reads_are_the_ones_store_policy_declares``
    is what keeps the copy honest.
    """
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {store_policy.TABLE} ("
        "business_id TEXT NOT NULL, store_id TEXT NOT NULL, "
        "pricing_type TEXT, pricing_value REAL, "
        "shipping_allowance_cents INTEGER, auto_publish INTEGER, "
        "marketplace_autolist INTEGER, created_at TEXT, updated_at TEXT, "
        "PRIMARY KEY (business_id, store_id))")
    cur.execute(
        f"INSERT INTO {store_policy.TABLE} (business_id, store_id, pricing_type, "
        "pricing_value, shipping_allowance_cents, auto_publish, "
        "marketplace_autolist, created_at, updated_at) "
        "VALUES (?, ?, 'TARGET_MARGIN', 68.0, ?, 0, 0, '', '')",
        (business_id, store_id, allowance_cents))


def bind_in_store(cur, *, listing_id=DROPSHIP, provider_variant_id="20001"):
    """A binding that also names the store, so the policy row is reachable."""
    return variants.link_source(
        cur, listing_id=listing_id, seller_user_id=SELLER, provider="cj",
        provider_product_id="10001", provider_variant_id=provider_variant_id,
        business_id="biz", store_id="store",
        fulfillment_mode=schema.MODE_DROPSHIP)


def test_an_unbound_listing_is_refused_before_the_charge(cur):
    """§1. Production listing 35, and the whole reason this reason exists.

    Live, approved, priced, in stock, and bound to nothing. Every availability
    question this gate used to ask returns "fine", so it charged the buyer and
    then ``gateway.get_product_binding`` raised ``product_binding_required``
    (409) because there was no variant to order. Refusing here is the same
    refusal, moved to the side of the payment where it is still free.
    """
    bind(cur, provider_variant_id=None)
    add_variant(cur)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_UNBOUND
    assert decision["bound"] is False


def test_a_bound_listing_is_not_refused_as_unbound(cur):
    """The control for the test above: the refusal is about the binding, not the shape."""
    bind(cur)
    add_variant(cur)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["bound"] is True


def test_selling_below_landed_cost_is_refused(cur):
    """§2/§3. A sale that is known to lose money must not reach payment.

    Measured in production 2026-10-01: 477 of 3797 variants priced below their
    landed cost, across 20 listings the reconciler had *already* flagged
    ``SELLING_BELOW_COST``. The system knew, wrote it down, and sold anyway.
    """
    bind(cur)
    add_variant(cur, price_cents=500, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_NEGATIVE_MARGIN
    assert decision["charged_minor"] == 500
    assert decision["basis_cost_minor"] == 800


def test_a_profitable_sale_below_the_sellers_target_still_sells(cur):
    """§5/§6. The gate refuses a loss, never a disappointing margin.

    The store's configured rule is ``TARGET_MARGIN 68.0``, which on an $8.00 cost
    wants $25.00. This sale makes $1.00. It is nowhere near the seller's target
    and it is still profit, and §5 is explicit that the two questions are
    different: "Distinguish LOSS SAFETY from SELLER TARGET."

    This is the test that would fail if anyone ever wired the target into this
    gate. Production has 112 variants in exactly this band — above cost, below
    target — and a gate enforcing the preference would have taken them off sale.
    """
    bind(cur)
    add_variant(cur, price_cents=900, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["reason"] != gate.REASON_NEGATIVE_MARGIN


def test_break_even_is_not_a_loss(cur):
    """The boundary, pinned: the comparison is strict.

    Price exactly equal to landed cost loses nothing, so refusing it would be
    enforcing a margin preference of "more than zero" — which is still a
    preference. An off-by-one here is the difference between a loss gate and a
    profit gate.
    """
    bind(cur)
    add_variant(cur, price_cents=800, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW

    bind(cur, listing_id=DROPSHIP_B, provider_variant_id="20002")
    add_variant(cur, listing_id=DROPSHIP_B, provider_variant_id="20002",
                price_cents=799, cost_cents=800)
    confirm(cur, listing_id=DROPSHIP_B)
    one_cent_short = gate.evaluate(cur, listing_id=DROPSHIP_B,
                                   evidence=draining(cur), now=NOW)
    assert one_cent_short["reason"] == gate.REASON_NEGATIVE_MARGIN


def test_an_unknown_supplier_cost_does_not_invent_a_loss(cur):
    """§1/§7 again: the refusal must be a fact, never an inference.

    A variant with no stored cost is what an import that never read a price
    leaves behind. Treating the absence as zero would call every such sale
    profitable; treating it as infinite would refuse the catalogue. Neither is
    something a supplier said, so the loss check declines to answer.
    """
    bind(cur)
    add_variant(cur, cost_cents=None)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["basis_cost_minor"] is None


def test_freight_is_part_of_the_cost_being_compared(cur):
    """§4. Buyer-facing shipping is free; the freight still costs the seller.

    $15.00 against a $8.00 item looks like profit and is a $1.00 loss once the
    store's declared $8.00 freight allowance is added. §4 forbids changing the
    landed-cost formula, so this reads the same ``pricing.basis`` the readiness
    survey does — the gate and the report cannot disagree about what a product
    costs.
    """
    policy(cur, allowance_cents=800)
    bind_in_store(cur)
    add_variant(cur, price_cents=1500, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_REFUSE
    assert decision["reason"] == gate.REASON_NEGATIVE_MARGIN
    assert decision["basis_cost_minor"] == 1600


def test_an_undeclared_freight_allowance_does_not_manufacture_a_loss(cur):
    """The same listing, with no policy row: the item cost is all we can prove.

    This is the lenient direction on purpose. A store that never declared an
    allowance has told us nothing about freight, and inventing a number would
    refuse sales on a cost nobody stated. It also means the gate is weakest
    exactly where the data is thinnest, which is why §23's sentinel reports the
    below-cost population separately rather than relying on this check alone.
    """
    bind_in_store(cur)
    add_variant(cur, price_cents=1500, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert decision["decision"] == gate.DECISION_ALLOW
    assert decision["basis_cost_minor"] == 800


def test_the_price_the_lane_is_actually_charging_wins_over_the_stored_one(cur):
    """§24/§25. A cart holds a price; the stored one may have moved since.

    The lanes pass the figure the charge is built from — the cart's
    ``price_snapshot_minor``, the offer's accepted ``amount_minor``, buy-now's
    ``unit_price_minor``. Judging the stored price instead would check a number
    nobody is paying, which is how a cart added before a cost rise becomes a
    loss that the gate waves through.
    """
    bind(cur)
    add_variant(cur, price_cents=2000, cost_cents=800)
    confirm(cur)
    draining(cur)

    stored = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW)
    assert stored["decision"] == gate.DECISION_ALLOW

    stale = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW,
                          price_minor=500)
    assert stale["decision"] == gate.DECISION_REFUSE
    assert stale["reason"] == gate.REASON_NEGATIVE_MARGIN
    assert stale["charged_minor"] == 500


def test_a_lane_naming_no_price_falls_back_to_the_stored_one(cur):
    """A caller that cannot name a price must not thereby skip the check.

    ``price_minor=None`` means "this lane did not tell us", not "do not look".
    Defaulting to no comparison would make the loss check opt-in, and the first
    lane added later would opt out of it by saying nothing.
    """
    bind(cur)
    add_variant(cur, price_cents=500, cost_cents=800)
    confirm(cur)
    decision = gate.evaluate(cur, listing_id=DROPSHIP, evidence=draining(cur), now=NOW,
                             price_minor=None)
    assert decision["reason"] == gate.REASON_NEGATIVE_MARGIN
    assert decision["charged_minor"] == 500


def test_a_basket_price_map_is_read_per_listing(cur):
    """``screen`` must hand each line its own price, not the basket's first.

    Two lines, one profitable at the price being charged and one not. Keying the
    map wrongly — or passing one price to every line — turns a two-line cart into
    a coin flip about which line's economics get checked.
    """
    bind(cur)
    add_variant(cur, price_cents=2000, cost_cents=800)
    bind(cur, listing_id=DROPSHIP_B, provider_variant_id="20002")
    add_variant(cur, listing_id=DROPSHIP_B, provider_variant_id="20002",
                price_cents=2000, cost_cents=800)
    confirm(cur)
    confirm(cur, listing_id=DROPSHIP_B)
    draining(cur)

    screened = gate.screen(cur, [DROPSHIP, DROPSHIP_B],
                           prices={DROPSHIP: 2000, DROPSHIP_B: 100}, now=NOW)
    assert screened["refused_listing_id"] == DROPSHIP_B
    assert screened["refusal"]["reason"] == gate.REASON_NEGATIVE_MARGIN
    assert screened["decisions"][DROPSHIP]["decision"] == gate.DECISION_ALLOW


def test_both_new_refusals_reach_the_buyer_without_naming_the_seller_s_economics(cur):
    """§25. Truthful to the buyer, silent about the seller's cost base.

    "This seller priced below their own cost" is a true sentence and it is not
    the buyer's business, so the loss refusal deliberately shares its copy with
    the unbound one: both mean "you cannot buy this right now", which is the part
    that concerns the buyer. Neither is mapped onto ``OUT_OF_STOCK`` — the
    supplier has plenty — so the client falls back to the generic copy rather
    than being handed a lie.
    """
    for reason in (gate.REASON_UNBOUND, gate.REASON_NEGATIVE_MARGIN):
        message = gate.MESSAGES[reason]
        assert "not been charged" in message, reason
        for leak in ("cost", "margin", "supplier", "cj", "loss", "profit"):
            assert leak not in message.lower(), f"{reason} leaked {leak}: {message}"
        assert reason not in gate.WIRE_CODES, (
            f"{reason} is mapped to a buyer-facing code that misstates the cause")
    assert gate.WIRE_CODES[gate.REASON_SOLD_OUT] == "OUT_OF_STOCK"


def test_every_refusal_code_has_buyer_copy(cur):
    """A refusal with no message reaches the buyer as a blank screen."""
    for reason in gate.REFUSAL_CODES:
        assert gate.MESSAGES.get(reason), f"{reason} has no buyer message"


def test_bound_variant_agrees_with_the_publication_path(cur):
    """One question, two implementations, pinned rather than inspected.

    ``drafts._sold_variant`` decides which variant a listing *publishes* as, and
    ``_bound_variant`` decides which one it *sells*. If they ever disagree, a
    listing publishes one physical item and charges for another — the exact
    failure §11 and §24 are about. This is the test
    ``_bound_variant``'s docstring promises.

    Driven through both functions with the same inputs, including the two cases
    that are easy to get differently: nothing bound, and a binding naming a
    variant the listing does not have.
    """
    from services.business_os.suppliers import drafts

    rows = [{"provider_variant_id": "20001", "id": 1},
            {"provider_variant_id": "20002", "id": 2},
            {"provider_variant_id": None, "id": 3}]
    cases = [
        {"provider_variant_id": "20001"},
        {"provider_variant_id": "20002"},
        {"provider_variant_id": "  20001  "},
        {"provider_variant_id": "20099"},
        {"provider_variant_id": ""},
        {"provider_variant_id": None},
        {},
        None,
    ]
    for source in cases:
        mine = gate._bound_variant(rows, source)
        theirs = drafts._sold_variant(rows, source)
        assert (mine is None) == (theirs is None), source
        if mine is not None:
            assert mine["id"] == theirs["id"], source


def test_the_policy_columns_this_gate_reads_are_the_ones_store_policy_declares(cur):
    """The allowance read is a hand-written SELECT; this is what keeps it honest.

    ``_shipping_allowance`` cannot call ``store_policy.resolve_shipping_allowance``
    because that routes through ``get_policy`` → ``ensure_schema`` → DDL, and
    running DDL inside a checkout can block the route on PostgreSQL. The cost of
    that decision is a duplicated column name, so the name is pinned against the
    declaring module's own source.
    """
    assert "shipping_allowance_cents INTEGER" in inspect.getsource(store_policy), (
        "store_policy no longer declares shipping_allowance_cents; the gate's "
        "hand-written SELECT is now reading a column that does not exist")

    read = inspect.getsource(gate._shipping_allowance)
    # The table name is interpolated from store_policy rather than spelled out,
    # so that half of the duplication cannot drift at all. Only the column name
    # is copied, and that is the half this test exists for.
    assert "{store_policy.TABLE}" in read, (
        "the gate now names the policy table itself instead of deriving it from "
        "store_policy, so a rename there would leave this SELECT behind")
    assert "shipping_allowance_cents" in read


def test_the_allowance_read_runs_no_ddl(cur):
    """§24 forbids DDL at checkout, and the obvious helper does it.

    ``ensure_schema`` on a route's own connection can hang it on PostgreSQL,
    which is why this gate hand-rolls the SELECT. A refactor back to the
    convenient call would reintroduce that, silently and only under load.
    """
    statements = []
    real_execute = cur.execute

    class Watching:
        def execute(self, sql, *args, **kwargs):
            statements.append(str(sql))
            return real_execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(cur, name)

    policy(cur, allowance_cents=800)
    bind_in_store(cur)
    add_variant(cur, price_cents=2000, cost_cents=800)
    confirm(cur)
    gate.evaluate(Watching(), listing_id=DROPSHIP,
                  evidence=draining(cur), now=NOW)
    for sql in statements:
        head = sql.strip().upper()
        assert not head.startswith(("CREATE", "ALTER", "DROP")), sql
