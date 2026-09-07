"""Additive supplier connection metadata and isolated encrypted credential storage.

Business/store ownership remains in canonical Business OS tables. No credential
column exists on the public connection row. Vault values are authenticated
ciphertext, never an alternative plaintext store. All SQL uses services.db.
"""
from services import db


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_connections (
            id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, business_id TEXT NOT NULL,
            store_id TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'CJ',
            connection_type TEXT NOT NULL DEFAULT 'API_KEY', external_account_id TEXT NOT NULL,
            external_shop_id TEXT NOT NULL, status TEXT NOT NULL,
            credential_reference TEXT NOT NULL, access_expires_at TEXT NOT NULL,
            refresh_expires_at TEXT NOT NULL, quota_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            quota_json TEXT, last_verified_at TEXT, last_sync_at TEXT,
            refresh_lease_token TEXT, refresh_lease_until TEXT,
            version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_store_provider "
                     "ON business_os_supplier_connections (business_id, store_id, provider)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_supplier_account "
                     "ON business_os_supplier_connections (external_account_id, merchant_id)")
        # Unique ownership claim closes a concurrent first-connect race between
        # different merchants. Several stores of the same owner may share CJ.
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_account_owners (
            account_reference TEXT PRIMARY KEY, merchant_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_credential_vault (
            credential_reference TEXT PRIMARY KEY, merchant_id TEXT NOT NULL,
            business_id TEXT NOT NULL, store_id TEXT NOT NULL, connection_id TEXT NOT NULL,
            ciphertext TEXT NOT NULL, key_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_vault_connection "
                     "ON business_os_supplier_credential_vault (connection_id)")
        if owned:
            conn.commit()
    except Exception:
        if owned:
            conn.rollback()
        raise
    finally:
        if owned:
            conn.close()
