"""Synthetic credential regressions. No provider or Railway network access."""
import json
import sqlite3

import pytest

from services.business_os.suppliers import schema, vault


@pytest.fixture
def vault_scope(monkeypatch):
    monkeypatch.setenv(vault.KEYRING_ENV, "test:" + "1f" * 32)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, "test")
    return dict(merchant_id="merchant-a", business_id="business-a", store_id="store-a",
                connection_id="connection-a", credential_reference="vault-reference-a")


@pytest.fixture
def credentials():
    return {"api_key": "fixture-sensitive-api-key", "access_token": "fixture-sensitive-access-token",
            "refresh_token": "fixture-sensitive-refresh-token", "open_id": "fixture-sensitive-open-id"}


def test_ciphertext_roundtrip_and_repr_redacted(vault_scope, credentials, caplog):
    sealed, key_id = vault.seal(credentials, **vault_scope)
    assert all(value not in sealed for value in credentials.values())
    opened = vault.unseal(sealed, key_id, **vault_scope)
    assert opened == credentials
    assert all(value not in repr(opened) and value not in str(opened) for value in credentials.values())
    assert all(value not in caplog.text for value in credentials.values())
    with pytest.raises(TypeError):
        json.dumps(opened)  # accidental ordinary API serialization must fail
    assert vault.seal(credentials, **vault_scope)[0] != sealed  # independent GCM nonces


@pytest.mark.parametrize("dimension", ["merchant_id", "business_id", "store_id", "connection_id", "credential_reference"])
def test_aad_each_tenant_dimension_is_authenticated(vault_scope, credentials, dimension):
    sealed, key_id = vault.seal(credentials, **vault_scope)
    changed = dict(vault_scope, **{dimension: "another-scope"})
    with pytest.raises(vault.VaultError):
        vault.unseal(sealed, key_id, **changed)


def test_aad_delimiter_collision_is_impossible(vault_scope, credentials):
    left = dict(vault_scope, merchant_id="a|b", business_id="c")
    right = dict(vault_scope, merchant_id="a", business_id="b|c")
    sealed, key_id = vault.seal(credentials, **left)
    with pytest.raises(vault.VaultError):
        vault.unseal(sealed, key_id, **right)


def test_tamper_wrong_key_and_unknown_version_fail_closed(vault_scope, credentials):
    sealed, key_id = vault.seal(credentials, **vault_scope)
    replacement = "A" if sealed[-4] != "A" else "B"
    tampered = sealed[:-4] + replacement + sealed[-3:]
    for value, key in ((tampered, key_id), (sealed, "unknown"), (sealed.replace("v1.", "v2."), key_id)):
        with pytest.raises(vault.VaultError):
            vault.unseal(value, key, **vault_scope)


@pytest.mark.parametrize("ring,active", [("", ""), ("invalid", ""), ("bad:short", "bad"),
    ("test:" + "1f" * 32, "absent"), ("test:" + "1f" * 32 + ",bad:not-a-key", "test")])
def test_no_plaintext_fallback(vault_scope, credentials, monkeypatch, ring, active):
    monkeypatch.setenv(vault.KEYRING_ENV, ring)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, active)
    assert not vault.available()
    with pytest.raises(vault.VaultError) as failure:
        vault.seal(credentials, **vault_scope)
    assert all(value not in str(failure.value) for value in credentials.values())


def test_rotation_retains_old_decrypt_key(vault_scope, credentials, monkeypatch):
    sealed, key_id = vault.seal(credentials, **vault_scope)
    monkeypatch.setenv(vault.KEYRING_ENV, "test:" + "1f" * 32 + ",next:" + "2e" * 32)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, "next")
    assert vault.unseal(sealed, key_id, **vault_scope) == credentials
    assert vault.seal(credentials, **vault_scope)[1] == "next"
    monkeypatch.setenv(vault.KEYRING_ENV, "next:" + "2e" * 32)
    with pytest.raises(vault.VaultError):
        vault.unseal(sealed, key_id, **vault_scope)


def test_database_stores_only_encrypted_bundle(vault_scope, credentials):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.ensure_schema(conn)
    reference = vault.save(conn, credentials, **vault_scope)
    rows = [dict(row) for row in conn.execute("SELECT * FROM business_os_supplier_credential_vault")]
    encoded = json.dumps(rows)
    assert all(value not in encoded for value in credentials.values())
    assert reference == vault_scope["credential_reference"]
    assert vault.load(conn, **vault_scope) == credentials
    for field in ("merchant_id", "business_id", "store_id", "connection_id"):
        with pytest.raises(vault.VaultError):
            vault.load(conn, **dict(vault_scope, **{field: "another-scope"}))
        with pytest.raises(vault.VaultError):
            vault.save(conn, credentials, **dict(vault_scope, **{field: "another-scope"}))
    conn.close()
