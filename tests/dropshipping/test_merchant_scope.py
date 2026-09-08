"""A merchant with a store can use dropshipping. That is the whole subject.

The bug this file exists to prevent coming back
-----------------------------------------------
PulseSoc has two merchant identities that were never joined:

* ``marketplace_sellers`` — what the Store dashboard means by "your store", and
  the row whose ``status`` decides whether someone may sell at all.
* ``business_os_business`` + ``business_os_store_storefront`` — a management
  workspace that nothing creates automatically.

The supplier gateway only ever asked the second one. A merchant trading as
"M&W Store · Open for orders" was therefore told "Dropshipping needs a business
first" — asked to invent a second identity in order to connect a supplier to the
store they already had.

Two properties have to hold at once, and it is easy to fix one by breaking the
other:

1. **A store is enough.** An approved seller resolves to a usable scope without
   any Business OS row, and no row is created on their behalf.
2. **Tenancy did not move.** The seller branch skips Business OS RBAC, so it has
   to prove ownership itself. Merchant A must not reach merchant B's supplier
   account by naming B's scope, and every test that asserts (1) is worthless
   without the ones that assert (2).

Runs alone: sets ``DATABASE_URL`` at import, before ``services.db`` reads it.
"""

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="scope-test-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services.business_os.suppliers import connections, merchant_scope  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402

SELLER_ID = "7001"          # approved seller, no Business OS row  (the M&W case)
WORKSPACE_ID = "7002"       # Business OS business + storefront, no seller row
PENDING_ID = "7003"         # seller row, not approved
NOBODY_ID = "7004"          # neither
BOTH_ID = "7005"            # approved seller *and* a Business OS workspace
CONTEXT = {"account_status": "active", "access_enabled": True}


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_sellers (
            user_id INTEGER PRIMARY KEY, display_name TEXT, business_name TEXT,
            status TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business (
            business_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
            display_name TEXT, legal_name TEXT, created_at TEXT,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_store_storefront (
            storefront_id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business_members (
            business_id TEXT NOT NULL, user_id TEXT NOT NULL,
            role TEXT NOT NULL, status TEXT NOT NULL)""")

        conn.execute("INSERT INTO marketplace_sellers (user_id, display_name, status) "
                     "VALUES (?,?,'approved')", (SELLER_ID, "M&W Store"))
        conn.execute("INSERT INTO marketplace_sellers (user_id, display_name, status) "
                     "VALUES (?,?,'pending')", (PENDING_ID, "Not Live Yet"))
        conn.execute("INSERT INTO marketplace_sellers (user_id, display_name, status) "
                     "VALUES (?,?,'approved')", (BOTH_ID, "Seller Side"))

        for owner, business, store in ((WORKSPACE_ID, "biz-w", "store-w"),
                                       (BOTH_ID, "biz-both", "store-both")):
            conn.execute("INSERT INTO business_os_business "
                         "(business_id, owner_user_id, display_name, created_at, status) "
                         "VALUES (?,?,?,?, 'active')",
                         (business, owner, "Workspace Ltd", "2026-01-01T00:00:00Z"))
            conn.execute("INSERT INTO business_os_store_storefront "
                         "(storefront_id, business_id, status) VALUES (?,?,'active')",
                         (store, business))
        connection_schema.ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()
    yield


def resolve(user_id):
    conn = db.connect()
    try:
        return merchant_scope.resolve(conn, user_id)
    finally:
        conn.close()


def seed_connection(connection_id, business, store, owner):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_supplier_connections "
            "(id, merchant_id, business_id, store_id, provider, connection_type, "
            " external_account_id, external_shop_id, status, credential_reference, "
            " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
            "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
            (connection_id, owner, business, store, f"acct-{connection_id}",
             f"shop-{connection_id}", f"cred-{connection_id}",
             "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z",
             "2026-09-07T00:00:00Z", "2026-09-07T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1 — a store is enough
# ---------------------------------------------------------------------------

def test_approved_seller_resolves_without_any_business_os_row():
    # The exact reported case: an active PulseSoc store, no workspace.
    result = resolve(SELLER_ID)
    assert result["status"] == "ok"
    assert result["source"] == "MARKETPLACE_SELLER"
    assert result["business_id"] == result["store_id"] == f"mkt-seller:{SELLER_ID}"


def test_seller_scope_carries_the_store_name_buyers_see():
    # Not the account holder's name — that is the bug marketplace_seller_identity
    # exists to prevent, and this scope feeds a merchant-facing header.
    assert resolve(SELLER_ID)["store_name"] == "M&W Store"


def test_resolving_a_seller_creates_no_business_and_no_storefront():
    # "Just auto-create a business row" would seed a second commerce authority:
    # rows nothing else reads, drifting from the seller record.
    resolve(SELLER_ID)
    conn = db.connect()
    try:
        businesses = conn.execute("SELECT COUNT(*) AS n FROM business_os_business").fetchone()["n"]
        stores = conn.execute("SELECT COUNT(*) AS n FROM business_os_store_storefront").fetchone()["n"]
    finally:
        conn.close()
    assert (businesses, stores) == (2, 2)      # only the two seeded workspaces


def test_business_os_merchants_keep_the_scope_they_already_had():
    result = resolve(WORKSPACE_ID)
    assert result["status"] == "ok"
    assert result["source"] == "BUSINESS_OS"
    assert (result["business_id"], result["store_id"]) == ("biz-w", "store-w")


# ---------------------------------------------------------------------------
# 2 — the gaps are different from each other
# ---------------------------------------------------------------------------

def test_a_user_with_no_store_at_all_is_the_only_no_store_case():
    assert resolve(NOBODY_ID) == {"status": "missing", "gap": "NO_STORE"}


def test_a_seller_awaiting_review_is_not_told_they_have_no_store():
    # They applied. Telling them to go and set a store up sends them back to a
    # form they already filled in.
    assert resolve(PENDING_ID) == {"status": "missing", "gap": "STORE_PENDING_REVIEW"}


def test_a_workspace_without_a_storefront_says_so():
    conn = db.connect()
    try:
        conn.execute("INSERT INTO business_os_business "
                     "(business_id, owner_user_id, created_at, status) "
                     "VALUES ('biz-bare', ?, '2026-01-01T00:00:00Z', 'active')", (NOBODY_ID,))
        conn.commit()
    finally:
        conn.close()
    assert resolve(NOBODY_ID) == {"status": "missing", "gap": "NO_STOREFRONT"}


def test_an_archived_workspace_does_not_count_as_a_store():
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_business SET status='archived' WHERE business_id='biz-w'")
        conn.commit()
    finally:
        conn.close()
    assert resolve(WORKSPACE_ID)["status"] == "missing"


# ---------------------------------------------------------------------------
# 3 — a live connection is never orphaned by a later identity change
# ---------------------------------------------------------------------------

def test_a_workspace_created_later_does_not_move_an_existing_connection():
    # Merchant connects a supplier as a seller, then creates a workspace. If the
    # workspace branch won from then on, their connected supplier would silently
    # vanish from the hub.
    business, store = merchant_scope.seller_scope(BOTH_ID)
    seed_connection("conn-seller", business, store, BOTH_ID)
    result = resolve(BOTH_ID)
    assert result["source"] == "MARKETPLACE_SELLER"
    assert result["business_id"] == business


def test_a_workspace_connection_keeps_the_workspace_scope():
    seed_connection("conn-workspace", "biz-both", "store-both", BOTH_ID)
    assert resolve(BOTH_ID)["business_id"] == "biz-both"


def test_without_any_connection_a_workspace_merchant_prefers_the_workspace():
    assert resolve(BOTH_ID)["source"] == "BUSINESS_OS"


# ---------------------------------------------------------------------------
# 4 — tenancy did not move
# ---------------------------------------------------------------------------

def _authorize(business_id, store_id, actor):
    conn = db.connect()
    try:
        return connections._authorize(conn, business_id, store_id, actor, context=CONTEXT)
    finally:
        conn.close()


def test_owner_of_a_seller_scope_is_authorized_as_that_merchant():
    business, store = merchant_scope.seller_scope(SELLER_ID)
    assert _authorize(business, store, SELLER_ID) == SELLER_ID


def test_another_merchant_cannot_authorize_against_a_seller_scope():
    business, store = merchant_scope.seller_scope(SELLER_ID)
    with pytest.raises(connections.SupplierConnectionError) as caught:
        _authorize(business, store, WORKSPACE_ID)
    # 404, not 403: a 403 would confirm that this merchant's store exists.
    assert caught.value.http_status == 404


def test_a_seller_scope_for_a_user_with_no_seller_row_is_refused():
    business, store = merchant_scope.seller_scope(NOBODY_ID)
    with pytest.raises(connections.SupplierConnectionError):
        _authorize(business, store, NOBODY_ID)


def test_a_seller_awaiting_review_cannot_connect_a_supplier():
    business, store = merchant_scope.seller_scope(PENDING_ID)
    with pytest.raises(connections.SupplierConnectionError) as caught:
        _authorize(business, store, PENDING_ID)
    assert caught.value.http_status == 403


def test_a_suspended_seller_loses_supplier_access():
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_sellers SET status='suspended' WHERE user_id=?", (SELLER_ID,))
        conn.commit()
    finally:
        conn.close()
    business, store = merchant_scope.seller_scope(SELLER_ID)
    with pytest.raises(connections.SupplierConnectionError):
        _authorize(business, store, SELLER_ID)


def test_a_mismatched_pair_is_not_treated_as_a_seller_scope():
    # Pairing one merchant's seller business with another's store must not be
    # read as "a seller scope" and waved through on the owner check.
    business, _ = merchant_scope.seller_scope(SELLER_ID)
    _, other_store = merchant_scope.seller_scope(WORKSPACE_ID)
    assert merchant_scope.seller_scope_owner(business, other_store) is None
    with pytest.raises(connections.SupplierConnectionError):
        _authorize(business, other_store, SELLER_ID)


def test_a_non_numeric_owner_is_not_a_seller_scope():
    # Guards the parse itself: anything that is not a plain user id falls through
    # to Business OS, which will not find it.
    assert merchant_scope.seller_scope_owner("mkt-seller:' OR 1=1--",
                                             "mkt-seller:' OR 1=1--") is None
    assert merchant_scope.seller_scope_owner("biz-w", "store-w") is None


def test_unauthenticated_resolution_is_refused():
    for actor in (None, "", "   "):
        with pytest.raises(merchant_scope.ScopeError) as caught:
            resolve(actor)
        assert caught.value.http_status == 401
