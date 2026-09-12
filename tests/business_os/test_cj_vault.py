"""Synthetic credential regressions. No provider or Railway network access."""
import base64
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


# --- what a failure means, and where it happened -----------------------------
#
# Every assertion above says "this must fail". None of them said what the
# failure *tells the reader*, and for most of this module's life the answer was
# the same for all of them: 503, "try again later", raised from one of two
# lines. A retry cannot fix a misspelled field, and an operator sent to check
# the keyring over a caller's typo is looking in the wrong place.


def _raises(fn):
    with pytest.raises(vault.VaultError) as failure:
        fn()
    return failure.value


def test_a_caller_mistake_is_not_an_outage(vault_scope, credentials):
    """Malformed input is our bug: 500, not a 503 promising a retry."""
    for call in (lambda: vault.seal({"api_key": "only-one-field"}, **vault_scope),
                 lambda: vault.seal(dict(credentials, api_key=""), **vault_scope),
                 lambda: vault.seal(credentials, **dict(vault_scope, store_id=None)),
                 lambda: vault.account_fingerprint("x", namespace="not-a-namespace"),
                 lambda: vault.account_fingerprint("", namespace="account")):
        failure = _raises(call)
        assert isinstance(failure, vault.CredentialRequestInvalid)
        assert (failure.code, failure.http_status) == ("credential_request_invalid", 500)


def test_a_misspelled_scope_keyword_is_named_as_ours(vault_scope, credentials):
    """The one caller mistake ``_aad`` cannot classify, because it never runs."""
    scope = dict(vault_scope)
    scope["stores_id"] = scope.pop("store_id")
    assert isinstance(_raises(lambda: vault.seal(credentials, **scope)),
                      vault.CredentialRequestInvalid)


def test_an_unopenable_credential_is_not_an_outage(vault_scope, credentials, monkeypatch):
    """The vault is working in each of these. It is refusing."""
    sealed, key_id = vault.seal(credentials, **vault_scope)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.ensure_schema(conn)
    vault.save(conn, credentials, **vault_scope)
    for call in (lambda: vault.unseal(sealed, key_id, **dict(vault_scope, store_id="elsewhere")),
                 lambda: vault.unseal(sealed, "never-in-the-ring", **vault_scope),
                 lambda: vault.unseal("v1.%%%%not-base64", key_id, **vault_scope),
                 lambda: vault.unseal("not-an-envelope", key_id, **vault_scope),
                 lambda: vault.unseal("v1.AAAAAAAA", key_id, **vault_scope),
                 lambda: vault.unseal(sealed[:-6] + "AAAAA=", key_id, **vault_scope),
                 lambda: vault.load(conn, **dict(vault_scope,
                                                 credential_reference="no-such-reference")),
                 lambda: vault.save(conn, credentials,
                                    **dict(vault_scope, merchant_id="someone-else"))):
        failure = _raises(call)
        assert isinstance(failure, vault.CredentialUnusable)
        assert (failure.code, failure.http_status) == ("credential_unusable", 409)
    conn.close()


def test_an_absent_reference_and_a_stolen_one_are_indistinguishable(vault_scope, credentials):
    """Otherwise the pair enumerates other tenants' credential references."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.ensure_schema(conn)
    vault.save(conn, credentials, **vault_scope)
    absent = _raises(lambda: vault.load(conn, **dict(vault_scope,
                                                    credential_reference="scv_does-not-exist")))
    stolen = _raises(lambda: vault.load(conn, **dict(vault_scope, merchant_id="another-merchant")))
    assert (absent.code, absent.http_status) == (stolen.code, stolen.http_status)
    assert str(absent) == str(stolen)
    conn.close()


def test_a_real_outage_still_says_retry(vault_scope, credentials, monkeypatch):
    """The class that survived the split has to keep meaning what it meant."""
    monkeypatch.setenv(vault.KEYRING_ENV, "")
    for call in (lambda: vault.seal(credentials, **vault_scope),
                 lambda: vault.unseal("v1.AAAA", "test", **vault_scope),
                 vault.require_available):
        failure = _raises(call)
        assert type(failure) is vault.VaultError
        assert (failure.code, failure.http_status) == ("credential_vault_unavailable", 503)


def test_every_class_is_still_a_vaulterror(vault_scope, credentials):
    """The compatibility claim, stated where a refactor would break it.

    Nine assertions in this file and two call sites in ``connections`` catch
    ``VaultError``. Splitting the classification must not have narrowed any of
    them.
    """
    assert issubclass(vault.CredentialRequestInvalid, vault.VaultError)
    assert issubclass(vault.CredentialUnusable, vault.VaultError)
    for failure in (_raises(lambda: vault.seal({}, **vault_scope)),
                    _raises(lambda: vault.unseal("junk", "test", **vault_scope))):
        assert isinstance(failure, vault.VaultError)
        assert all(value not in str(failure) and value not in repr(failure)
                   for value in credentials.values())


def test_no_failure_carries_the_thing_that_failed(vault_scope, credentials):
    """A failure may say what went wrong. It may never say what was in the bundle.

    The split multiplied the raise sites from two to eleven, and every one of
    them is somewhere a later "make this easier to debug" could attach the
    offending value. The route never renders ``str(exc)`` — but ordinary logging
    does, and a vault whose failure path prints an access token is worse than one
    that answered 503 to everything.
    """
    for call in (lambda: vault.seal(dict(credentials, open_id=""), **vault_scope),
                 lambda: vault.seal(dict(credentials, legacy_field="x"), **vault_scope),
                 lambda: vault.seal(dict(credentials, api_key="x" * 16385), **vault_scope),
                 lambda: vault.seal(credentials, **dict(vault_scope, store_id=None))):
        failure = _raises(call)
        rendered = "%s%r%r" % (failure, failure, failure.args)
        assert all(value not in rendered for value in credentials.values())

    # The unseal-side shape check is the dangerous one: by the time it runs, the
    # bundle it is rejecting has already been decrypted. Built here by hand,
    # because `seal` refuses to produce a row of this shape.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ring, active = vault._ring()
    nonce = b"\x00" * 12
    drifted = json.dumps(dict(credentials, legacy_field="x")).encode()
    blob = AESGCM(ring[active]).encrypt(nonce, drifted, vault._aad(**vault_scope))
    envelope = "v1." + base64.b64encode(nonce + blob).decode("ascii")
    failure = _raises(lambda: vault.unseal(envelope, active, **vault_scope))
    assert isinstance(failure, vault.CredentialUnusable)
    rendered = "%s%r%r" % (failure, failure, failure.args)
    assert all(value not in rendered for value in credentials.values())


def test_the_diagnostic_can_finally_tell_the_causes_apart(vault_scope, credentials):
    """The anti-vacuity test: this is the one that failed before the change.

    ``business_os_supplier_routes._origin`` exists so that many validators
    sharing one opaque code can be separated by an operator. A blanket
    ``except Exception: raise VaultError()`` is the one shape it cannot see
    through -- re-raising at the handler makes the handler the reported frame.
    Measured before this change: six distinct causes, two coordinates.
    """
    from services import business_os_supplier_routes as routes

    sealed, key_id = vault.seal(credentials, **vault_scope)
    causes = {
        "bundle missing a field": lambda: vault.seal({"api_key": "a"}, **vault_scope),
        "scope value is not a string": lambda: vault.seal(credentials,
                                                          **dict(vault_scope, store_id=None)),
        "not a v1 envelope": lambda: vault.unseal("nope", key_id, **vault_scope),
        "sealing key retired": lambda: vault.unseal(sealed, "gone", **vault_scope),
        "ciphertext is not base64": lambda: vault.unseal("v1.%%%%", key_id, **vault_scope),
        "envelope too short": lambda: vault.unseal("v1.AAAAAAAA", key_id, **vault_scope),
        "wrong scope (AAD refuses)": lambda: vault.unseal(sealed, key_id,
                                                          **dict(vault_scope, store_id="other")),
    }
    origins = {label: routes._origin(_raises(call)) for label, call in causes.items()}
    assert all(origin and origin.startswith("vault.py:") for origin in origins.values())
    assert len(set(origins.values())) == len(causes), origins
