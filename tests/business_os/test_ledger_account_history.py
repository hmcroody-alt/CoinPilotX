"""Ledger read side — `list_account_transactions`.

This is the query the Payments activity feed renders from, so the tests care
about the properties that make a money feed trustworthy rather than about the
happy path alone:

  1. rows come back newest first and carry a stable transaction id
  2. the sign is taken from the requested account's point of view, not guessed
     from the entry type — the same posting is +N to one side and -N to the other
  3. an account set is unioned server-side, and a posting that touches two
     requested accounts yields one row per side
  4. keyset pagination visits every row exactly once, with no overlap and no gap
  5. non-posted transactions stay visible wearing their real status
  6. currency and entry-type filters do not leak rows
  7. a malformed metadata blob degrades to None instead of raising

Runs hermetically against a throwaway SQLite file. Executable two ways:

    python -m pytest tests/business_os/test_ledger_account_history.py
    python tests/business_os/test_ledger_account_history.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ledger_hist_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402

SELLER = "seller_payable:u1"
ESCROW = "mkt_order_escrow:o1"
ESCROW2 = "mkt_order_escrow:o2"
INTAKE = "external:intake"
REVENUE = "platform:revenue"


def setup_module(module=None):
    ledger.ensure_schema()


def _reset():
    conn = db.connect()
    for t in ("ledger_entries", "ledger_transactions", "ledger_balances"):
        try:
            conn.execute("DELETE FROM " + t)
        except Exception:
            pass
    conn.commit()
    conn.close()


def _post(key, amount, source, destination, entry_type="capture", **kw):
    return ledger.post_entry(
        idempotency_key=key, actor="test", amount_cents=amount, currency="usd",
        entry_type=entry_type, source=source, destination=destination, **kw)


# --- 1. ordering and identity ----------------------------------------------
def test_newest_first_with_stable_transaction_ids():
    _reset()
    _post("k1", 1000, INTAKE, ESCROW)
    _post("k2", 2000, INTAKE, ESCROW)
    _post("k3", 3000, INTAKE, ESCROW)

    page = ledger.list_account_transactions(ESCROW)
    amounts = [t["amount_cents"] for t in page["transactions"]]
    assert amounts == [3000, 2000, 1000], amounts

    for t in page["transactions"]:
        assert t["transaction_id"], "every row must carry a stable id"
        assert t["cursor"], "every row must be addressable for pagination"
    ids = [t["transaction_id"] for t in page["transactions"]]
    assert len(set(ids)) == 3, "transaction ids must be unique"


# --- 2. the sign belongs to the account, not to the entry type --------------
def test_sign_is_relative_to_the_requested_account():
    _reset()
    # Escrow has to actually hold the money first — the ledger refuses to debit
    # an account into overdraft, which is itself the behaviour we want.
    _post("cap0", 900, INTAKE, ESCROW)
    # Settlement: escrow pays the seller. One posting, two opposite meanings.
    _post("settle1", 900, ESCROW, SELLER, entry_type="settle")

    seller_row = ledger.list_account_transactions(SELLER)["transactions"][0]
    escrow_row = ledger.list_account_transactions(ESCROW)["transactions"][0]

    assert seller_row["signed_amount_cents"] == 900, "money entered the payable account"
    assert escrow_row["signed_amount_cents"] == -900, "money left escrow"
    assert seller_row["entry_type"] == escrow_row["entry_type"] == "settle", (
        "identical entry_type on both sides is exactly why the type cannot "
        "be used to infer a direction")
    assert seller_row["transaction_id"] == escrow_row["transaction_id"]


# --- 3. the union happens server-side ---------------------------------------
def test_account_set_is_unioned_and_both_sides_appear():
    _reset()
    _post("cap1", 500, INTAKE, ESCROW)
    _post("cap2", 700, INTAKE, ESCROW2)
    _post("settle2", 500, ESCROW, SELLER, entry_type="settle")

    page = ledger.list_account_transactions([SELLER, ESCROW, ESCROW2])
    assert len(page["transactions"]) == 4, (
        "3 postings, but the settlement touches two requested accounts, so it "
        "contributes two rows: one per side")

    settle_rows = [t for t in page["transactions"] if t["entry_type"] == "settle"]
    assert len(settle_rows) == 2
    assert {r["account"] for r in settle_rows} == {SELLER, ESCROW}
    assert sorted(r["signed_amount_cents"] for r in settle_rows) == [-500, 500]

    # Requesting a single account never sees the other side.
    only_seller = ledger.list_account_transactions(SELLER)
    assert len(only_seller["transactions"]) == 1


def test_duplicate_accounts_do_not_duplicate_rows():
    _reset()
    _post("d1", 100, INTAKE, SELLER)
    page = ledger.list_account_transactions([SELLER, SELLER, SELLER])
    assert page["accounts"] == [SELLER]
    assert len(page["transactions"]) == 1


# --- 4. keyset pagination is exact ------------------------------------------
def test_pagination_covers_every_row_exactly_once():
    _reset()
    for i in range(25):
        _post("p%d" % i, 100 + i, INTAKE, SELLER)

    seen = []
    cursor = None
    pages = 0
    while True:
        page = ledger.list_account_transactions(SELLER, limit=7, before_cursor=cursor)
        seen.extend(t["transaction_id"] for t in page["transactions"])
        pages += 1
        assert pages < 20, "pagination failed to terminate"
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
        assert cursor is not None

    assert len(seen) == 25, "every row visited"
    assert len(set(seen)) == 25, "no row visited twice"


def test_limit_is_clamped_and_bad_cursor_is_rejected():
    _reset()
    _post("c1", 100, INTAKE, SELLER)
    assert len(ledger.list_account_transactions(SELLER, limit=10 ** 6)["transactions"]) <= \
        ledger.MAX_LIST_LIMIT
    assert len(ledger.list_account_transactions(SELLER, limit=0)["transactions"]) >= 1
    try:
        ledger.list_account_transactions(SELLER, before_cursor="not-a-number")
    except ledger.LedgerError:
        pass
    else:
        raise AssertionError("a non-numeric cursor must be rejected, not ignored")


def test_empty_account_set_is_an_empty_page_not_an_error():
    _reset()
    page = ledger.list_account_transactions([])
    assert page["transactions"] == [] and page["has_more"] is False


# --- 5. failed money stays visible ------------------------------------------
def test_non_posted_transactions_keep_their_real_status():
    _reset()
    _post("v1", 400, INTAKE, SELLER)
    conn = db.connect()
    conn.execute("UPDATE ledger_transactions SET status = 'void' WHERE idempotency_key = 'v1'")
    conn.commit()
    conn.close()

    rows = ledger.list_account_transactions(SELLER)["transactions"]
    assert len(rows) == 1, "a voided transaction is not swept out of the feed"
    assert rows[0]["status"] == "void"


# --- 6. filters do not leak --------------------------------------------------
def test_entry_type_and_currency_filters():
    _reset()
    _post("t1", 100, INTAKE, SELLER, entry_type="capture")
    _post("t2", 200, INTAKE, SELLER, entry_type="refund")
    _post("t3", 300, INTAKE, SELLER, entry_type="refund")

    refunds = ledger.list_account_transactions(SELLER, entry_types=["refund"])
    assert [t["amount_cents"] for t in refunds["transactions"]] == [300, 200]

    one = ledger.list_account_transactions(SELLER, entry_types="capture")
    assert [t["amount_cents"] for t in one["transactions"]] == [100]

    assert ledger.list_account_transactions(SELLER, currency="eur")["transactions"] == []


# --- 7. bad metadata does not take down the feed ----------------------------
def test_malformed_metadata_degrades_to_none():
    _reset()
    _post("m1", 100, INTAKE, SELLER, metadata={"order_id": "o9"})
    good = ledger.list_account_transactions(SELLER)["transactions"][0]
    assert good["metadata"] == {"order_id": "o9"}

    conn = db.connect()
    conn.execute("UPDATE ledger_transactions SET metadata_json = '{not json' "
                 "WHERE idempotency_key = 'm1'")
    conn.commit()
    conn.close()

    row = ledger.list_account_transactions(SELLER)["transactions"][0]
    assert row["metadata"] is None, "unreadable metadata is reported absent"
    assert row["amount_cents"] == 100, "the money on the row is still correct"


def test_related_object_survives_the_round_trip():
    _reset()
    _post("r1", 100, INTAKE, SELLER, related_object="order:o1", reason="Captured.")
    row = ledger.list_account_transactions(SELLER)["transactions"][0]
    assert row["related_object"] == "order:o1", "the deep link target must survive"
    assert row["reason"] == "Captured."


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
