"""Server-only supplier credential references, using application AES-256-GCM.

Follows Private Office's field_crypto design, with a SEPARATE key namespace and
unambiguous structured AAD. Protects database dumps, not a compromised process
or deployment environment. No fallback to a private-office/session/signing key.
Caller must authorize before reaching this internal module. No logging here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from collections import UserDict
from collections.abc import Mapping
from datetime import datetime, timezone

from services.private_office.field_crypto import _decode_key

KEYRING_ENV = "SUPPLIER_CREDENTIAL_KEYS"
ACTIVE_KEY_ENV = "SUPPLIER_CREDENTIAL_KEY_ACTIVE"
INDEX_KEY_ENV = "SUPPLIER_ACCOUNT_INDEX_KEY"
_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
_FIELDS = frozenset({"api_key", "access_token", "refresh_token", "open_id"})


class VaultError(RuntimeError):
    code = "credential_vault_unavailable"
    http_status = 503

    def __init__(self):
        super().__init__("Supplier credential storage is unavailable.")


class SecretBundle(UserDict):
    """Internal only. Safe repr; ordinary JSON encoders refuse serialization.

    Explicit conversion to dict remains privileged code and is done only in the
    encryption primitive. This is a tripwire, not protection from hostile code.
    """
    def __repr__(self):
        return "<SupplierCredentials REDACTED>"

    __str__ = __repr__


def _ring():
    ring = {}
    try:
        for entry in os.environ.get(KEYRING_ENV, "").split(","):
            key_id, material = entry.strip().split(":", 1)
            raw = _decode_key(material)
            if not _KEY_ID.fullmatch(key_id) or raw is None or key_id in ring:
                raise ValueError
            ring[key_id] = raw
        active = os.environ.get(ACTIVE_KEY_ENV, "") or next(iter(ring))
        if active not in ring:
            raise ValueError
        return ring, active
    except Exception:
        raise VaultError() from None


def require_available():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ring, active = _ring()
        AESGCM(ring[active])
    except Exception:
        raise VaultError() from None


def available():
    try:
        require_available()
        return True
    except VaultError:
        return False


def require_index_available():
    # Stable identity index is deliberately independent of rotating ciphertext
    # keys. Rotation requires an explicit migration of ownership/quota indexes.
    raw = _decode_key(os.environ.get(INDEX_KEY_ENV, ""))
    if raw is None:
        raise VaultError()
    return raw


def account_fingerprint(value, *, namespace="account"):
    if namespace not in {"account", "auth"} or not isinstance(value, str) or not value:
        raise VaultError()
    material = ("pulsesoc:cj:" + namespace + ":v1:" + value).encode("utf-8")
    prefix = "cja_" if namespace == "account" else "pending_"
    return prefix + hmac.new(require_index_available(), material, hashlib.sha256).hexdigest()


def _aad(*, merchant_id, business_id, store_id, connection_id, credential_reference):
    parts = ["pulsesoc:supplier-vault:v1:CJ", merchant_id, business_id, store_id,
             connection_id, credential_reference]
    if any(not isinstance(v, str) or not v for v in parts):
        raise VaultError()
    return json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _validate(bundle):
    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:
        raise VaultError()
    if any(not isinstance(v, str) or not v or len(v) > 16384 for v in bundle.values()):
        raise VaultError()


def seal(bundle, **scope):
    """Encrypt a complete bundle before any database operation is attempted."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        _validate(bundle)
        ring, active = _ring()
        nonce = os.urandom(12)
        plaintext = json.dumps(dict(bundle), sort_keys=True, separators=(",", ":")).encode()
        sealed = AESGCM(ring[active]).encrypt(nonce, plaintext, _aad(**scope))
        return "v1." + base64.b64encode(nonce + sealed).decode("ascii"), active
    except Exception:
        raise VaultError() from None


def unseal(ciphertext, key_id, **scope):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ring, _ = _ring()
        if not isinstance(ciphertext, str) or not ciphertext.startswith("v1."):
            raise ValueError
        raw = base64.b64decode(ciphertext[3:], validate=True)
        if len(raw) < 29:
            raise ValueError
        plain = AESGCM(ring[key_id]).decrypt(raw[:12], raw[12:], _aad(**scope))
        bundle = json.loads(plain)
        _validate(bundle)
        return SecretBundle(bundle)
    except Exception:
        raise VaultError() from None


def save(conn, bundle, *, merchant_id, business_id, store_id, connection_id,
         credential_reference=None):
    ref = credential_reference or "scv_" + uuid.uuid4().hex
    scope = dict(merchant_id=merchant_id, business_id=business_id, store_id=store_id,
                 connection_id=connection_id, credential_reference=ref)
    ciphertext, key_id = seal(bundle, **scope)
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute("SELECT * FROM business_os_supplier_credential_vault "
                            "WHERE credential_reference = ?", (ref,)).fetchone()
    if existing:
        if any(str(existing[k]) != str(v) for k, v in scope.items()):
            raise VaultError()
        conn.execute("UPDATE business_os_supplier_credential_vault SET ciphertext=?, "
                     "key_id=?, version=version+1, updated_at=? WHERE credential_reference=?",
                     (ciphertext, key_id, now, ref))
    else:
        conn.execute("INSERT INTO business_os_supplier_credential_vault "
                     "(credential_reference, merchant_id, business_id, store_id, connection_id, "
                     "ciphertext, key_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (ref, merchant_id, business_id, store_id, connection_id, ciphertext,
                      key_id, now, now))
    return ref


def load(conn, *, merchant_id, business_id, store_id, connection_id, credential_reference):
    scope = dict(merchant_id=merchant_id, business_id=business_id, store_id=store_id,
                 connection_id=connection_id, credential_reference=credential_reference)
    row = conn.execute("SELECT ciphertext, key_id FROM business_os_supplier_credential_vault "
                       "WHERE credential_reference=? AND merchant_id=? AND business_id=? "
                       "AND store_id=? AND connection_id=?",
                       (credential_reference, merchant_id, business_id, store_id,
                        connection_id)).fetchone()
    if row is None:
        raise VaultError()
    return unseal(row["ciphertext"], row["key_id"], **scope)
