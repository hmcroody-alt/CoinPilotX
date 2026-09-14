"""§24 — what an approval owes the supplier, decided before anything is queued.

The defect this covers is quiet and expensive. A dropshipped product is read
from its supplier once, at import. Then it sits in the review queue, which is a
delay this pipeline *deliberately introduces* — that is the whole point of a
review step. Whatever the gap is between import and approval, the product goes
live carrying stock and cost numbers from the far side of it. The reviewer is
even shown the staleness: ``inspection`` raises ``SUPPLIER_STALE`` and
``SUPPLIER_NEVER_SYNCED`` as gaps. Before this, approving did nothing about
them, so the queue could flag a seven-day-old stock count and then publish it
unchanged.

``supplier_sync_plan`` is the decision half, and it is pure for one specific
reason rather than for tidiness: ``suppliers.worker.schedule`` opens its own
database connection and commits it. Called from inside the review batch's open
write transaction it reproduces the ``log_admin_audit`` failure exactly — the
insert is refused, the exception is swallowed, and the endpoint returns 200
having scheduled nothing. Keeping the decision here and the enqueue after the
commit is what makes both halves true, and a test that can run the decision
without a database is what makes the ordering provable rather than assumed.

Run standalone or with the rest of ``tests/business_os`` — it binds nothing::

    ./.venv/bin/python3 -m pytest tests/business_os/test_review_supplier_sync.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.business_os.marketplace import listing_review as rv  # noqa: E402


def source(**overrides):
    """A fully bound CJ product. Every skip case below removes one field."""
    row = {
        "listing_id": 41,
        "provider": "cj",
        "provider_product_id": "CJ-PROD-8891",
        "provider_variant_id": "CJ-VAR-3",
        "supplier_connection_id": "conn_7",
        "business_id": "biz_2",
        "store_id": "store_5",
    }
    row.update(overrides)
    return row


# -- only an approval spends supplier quota -----------------------------------


@pytest.mark.parametrize("action", [rv.REJECT, rv.REQUEST_CHANGES, rv.RESTRICT])
def test_a_negative_verdict_does_not_refresh_anything(action):
    """A rejected product cannot be bought, so refreshing its stock buys nothing.

    Supplier reads are rate-limited and metered. Spending one to learn the
    current inventory of a page no buyer can reach is quota taken from the
    approvals that actually need it, and on a bulk reject of forty items it is
    eighty calls for forty products nobody will see.
    """
    plan = rv.supplier_sync_plan(source(), action=action)
    assert plan["scheduled"] is False
    assert plan["skip_reason"] == "NOT_AN_APPROVAL"
    assert plan["jobs"] == []


def test_an_approval_queues_both_stock_and_cost():
    plan = rv.supplier_sync_plan(source(), action=rv.APPROVE)
    assert plan["scheduled"] is True
    assert plan["skip_reason"] == ""
    assert {job["kind"] for job in plan["jobs"]} == set(rv.SUPPLIER_SYNC_KINDS)


def test_the_two_kinds_are_stock_and_the_product_record():
    """Named explicitly, because they answer different questions.

    ``inventory`` is how many are left; ``product`` is what the supplier now
    charges and calls it. Approving on a fresh stock count and a four-day-old
    cost still publishes a margin nobody has checked, so one of the two is not
    enough.
    """
    assert set(rv.SUPPLIER_SYNC_KINDS) == {"product", "inventory"}


def test_every_job_carries_the_whole_connection_scope():
    """``worker.schedule`` stores the tuple and every later read matches on it.

    A job written with the connection id alone is not a job that runs with
    reduced scope — it is a row ``_claim`` can select but whose reads resolve
    against a scope that matches no binding. It sits in the table looking
    scheduled forever, which is the failure mode that reads as success.
    """
    for job in rv.supplier_sync_plan(source(), action=rv.APPROVE)["jobs"]:
        assert job["connection_id"] == "conn_7"
        assert job["business_id"] == "biz_2"
        assert job["store_id"] == "store_5"
        assert job["resource_id"] == "CJ-PROD-8891"


# -- a skip is a fact about the listing, not an error -------------------------


def test_a_hand_made_product_is_skipped_not_failed():
    """There is no supplier. Reporting that as a sync failure sends an operator
    looking for a broken CJ integration that is working perfectly."""
    plan = rv.supplier_sync_plan(None, action=rv.APPROVE)
    assert plan["scheduled"] is False
    assert plan["skip_reason"] == "MERCHANT_AUTHORED"
    assert "created in PulseSoc" in plan["note"]


@pytest.mark.parametrize("missing", ["supplier_connection_id", "business_id", "store_id"])
def test_a_partial_connection_scope_refuses_rather_than_queuing_a_dead_job(missing):
    """Any one of the three blank makes the job unreachable, so none is optional.

    This is the case worth refusing loudly: a listing that *does* have a
    supplier, so "no supplier to refresh" would be wrong, but cannot be reached
    through one. Queuing it anyway produces a row that is never claimed and
    never errors.
    """
    plan = rv.supplier_sync_plan(source(**{missing: ""}), action=rv.APPROVE)
    assert plan["scheduled"] is False
    assert plan["skip_reason"] == "UNBOUND_SUPPLIER"
    assert plan["jobs"] == []


def test_a_source_with_no_provider_product_id_is_its_own_reason():
    """Distinct from UNBOUND_SUPPLIER because the repair is different: one needs
    the connection reconnected, the other needs the import re-run."""
    plan = rv.supplier_sync_plan(source(provider_product_id=""), action=rv.APPROVE)
    assert plan["skip_reason"] == "NO_PROVIDER_PRODUCT"


def test_whitespace_is_not_a_supplier_binding():
    """``" "`` is truthy in Python and empty in every sense that matters here."""
    plan = rv.supplier_sync_plan(source(store_id="   "), action=rv.APPROVE)
    assert plan["skip_reason"] == "UNBOUND_SUPPLIER"


# -- no skip can reach an operator without words ------------------------------


def test_every_skip_reason_has_a_sentence():
    """The mirror of the ``GAP_NOTES`` rule. A reason code rendered raw on an
    admin page is a string an operator has to guess the meaning of, and
    ``UNBOUND_SUPPLIER`` is exactly the kind of guess that goes wrong."""
    for code, note in rv.SYNC_SKIP_NOTES.items():
        assert note.strip(), code
        assert note.strip().endswith("."), code
        assert note != code


def test_no_plan_can_return_a_skip_code_with_no_note():
    """Checked by construction over every shape that produces a skip, so a new
    branch added later without a note fails here rather than on the page."""
    shapes = [
        (None, rv.REJECT), (None, rv.APPROVE),
        (source(supplier_connection_id=""), rv.APPROVE),
        (source(provider_product_id=""), rv.APPROVE),
    ]
    for row, action in shapes:
        plan = rv.supplier_sync_plan(row, action=action)
        assert plan["skip_reason"] in rv.SYNC_SKIP_NOTES
        assert plan["note"] == rv.SYNC_SKIP_NOTES[plan["skip_reason"]]


# -- it decides, it does not act ----------------------------------------------


def test_the_plan_does_not_mutate_the_source_row():
    row = source()
    before = dict(row)
    rv.supplier_sync_plan(row, action=rv.APPROVE)
    assert row == before


def test_the_planner_touches_no_database():
    """Proven by source, not by hoping the fixture would have failed.

    The whole ordering argument rests on this function being safe to call
    inside an open write transaction. A future edit that reaches for a cursor
    to look something up would be safe in tests — where a connection is
    available — and a lock-up in the route.
    """
    import inspect

    body = inspect.getsource(rv.supplier_sync_plan)
    for forbidden in ("db.", "connect(", "execute(", "cur.", "commit("):
        assert forbidden not in body, forbidden
