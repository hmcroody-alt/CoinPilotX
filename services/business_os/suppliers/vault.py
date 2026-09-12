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
    """The vault cannot serve this request. Base class, and a real outage.

    Everything raised here used to be exactly this: ``seal`` and ``unseal``
    wrapped their whole body in ``except Exception: raise VaultError() from
    None``, so a bundle with a misspelled field, a scope value that arrived as
    ``None``, a rotated-away key, a tampered row and an empty keyring were one
    answer -- ``credential_vault_unavailable``, 503, "try again later". Two of
    those are not outages, retrying them cannot help, and the operator sent to
    check the deployment's keyring is looking in the wrong place.

    The blanket catch also defeated the one diagnostic this package built for
    precisely this problem. ``business_os_supplier_routes._origin`` reports the
    innermost frame inside the supplier package so that "a dozen validators
    answering with the same opaque code" can be told apart -- but re-raising at
    the ``except`` line makes that frame the ``raise`` statement. Measured
    before this change: six distinct causes, two coordinates, and a genuine
    outage was byte-identical to a caller's typo.

    So failures are raised where they happen, and in three classes that differ
    by what the reader does next:

    * :class:`VaultError` -- the vault cannot operate: no keyring, unusable key
      material, no cipher. An operator fixes the deployment. Retryable. This is
      also the backstop for anything unanticipated, which is now a narrow set
      rather than the universal answer.
    * :class:`CredentialRequestInvalid` -- this module was handed something it
      cannot accept. That is a bug in a call site; no retry and no operator
      action will change it.
    * :class:`CredentialUnusable` -- the stored row cannot serve this request.
      Neither a retry nor a call-site fix helps; the credential itself has to be
      re-established.

    All three remain ``VaultError``, so every existing caller catches exactly
    what it caught before. What changes is what they say, and the fact that the
    line coordinate now points at the failure instead of at the handler -- which
    is what separates causes that share a class.
    """
    code = "credential_vault_unavailable"
    http_status = 503
    message = "Supplier credential storage is unavailable."

    def __init__(self):
        super().__init__(self.message)


class CredentialRequestInvalid(VaultError):
    """A call site handed this module something it cannot accept.

    A bundle missing a field, a scope value that is not a non-empty string, a
    misspelled keyword. Answering 503 told the caller to try again, which for a
    malformed call is a promise that cannot come true -- it produces a retry
    that fails identically and an operator who goes looking for a broken
    deployment. 500 is the honest answer: the mistake is ours and it is here.
    """
    code = "credential_request_invalid"
    http_status = 500
    message = "Supplier credential storage received a request it cannot accept."


class CredentialUnusable(VaultError):
    """The stored row exists-or-not, and either way cannot serve this request.

    Raised when the ciphertext does not open under this scope, when the key that
    sealed it is no longer in the ring, when the row is malformed, when there is
    no row, and when a credential reference is already held by a different
    tenant. The vault is working in every one of those: it is refusing, which is
    what an authenticated-scope design is for.

    The merchant's next move is the same for all of them -- re-establish the
    supplier connection -- and that is why they share an answer rather than
    being enumerated on the wire. Deliberately: "no such reference" and
    "reference belongs to someone else" must not be distinguishable from
    outside, or the pair becomes a way to enumerate other tenants' references.
    Inside, ``_origin``'s line number separates them for an operator who has the
    diagnostic flag on.
    """
    code = "credential_unusable"
    http_status = 409
    message = "The stored supplier credential cannot be used for this request."


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


def _aesgcm():
    """The cipher, or a real outage. A missing ``cryptography`` is a deployment."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except Exception:
        raise VaultError() from None


def require_available():
    cipher = _aesgcm()
    ring, active = _ring()
    try:
        cipher(ring[active])
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
        raise CredentialRequestInvalid()
    material = ("pulsesoc:cj:" + namespace + ":v1:" + value).encode("utf-8")
    prefix = "cja_" if namespace == "account" else "pending_"
    return prefix + hmac.new(require_index_available(), material, hashlib.sha256).hexdigest()


def _aad(*, merchant_id, business_id, store_id, connection_id, credential_reference):
    parts = ["pulsesoc:supplier-vault:v1:CJ", merchant_id, business_id, store_id,
             connection_id, credential_reference]
    if any(not isinstance(v, str) or not v for v in parts):
        raise CredentialRequestInvalid()
    return json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _scoped_aad(scope):
    """``_aad(**scope)``, with a misspelled or missing keyword named as our bug.

    ``TypeError`` here is a call site passing the wrong scope keywords -- the one
    failure that ``_aad`` itself cannot classify, because it never gets called.
    """
    try:
        return _aad(**scope)
    except TypeError:
        raise CredentialRequestInvalid() from None


def _validate(bundle, error):
    """Shape check. The *caller* says what a violation means.

    Identical bytes, two meanings: in ``seal`` a malformed bundle is a call site
    handing us junk, in ``unseal`` it is a stored row that has drifted from the
    shape this version reads. Same predicate, different reader, different answer.
    """
    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:
        raise error()
    if any(not isinstance(v, str) or not v or len(v) > 16384 for v in bundle.values()):
        raise error()


def seal(bundle, **scope):
    """Encrypt a complete bundle before any database operation is attempted.

    Each failure is raised on the line that found it, so the route's ``_origin``
    diagnostic reports the check rather than a shared handler. The trailing
    ``except`` is the backstop for the encryption itself -- which, having gotten
    a validated bundle, a built AAD and a key out of the ring, really is an
    outage if it fails.
    """
    cipher = _aesgcm()
    _validate(bundle, CredentialRequestInvalid)
    aad = _scoped_aad(scope)
    ring, active = _ring()
    try:
        nonce = os.urandom(12)
        plaintext = json.dumps(dict(bundle), sort_keys=True, separators=(",", ":")).encode()
        sealed = cipher(ring[active]).encrypt(nonce, plaintext, aad)
    except Exception:
        raise VaultError() from None
    return "v1." + base64.b64encode(nonce + sealed).decode("ascii"), active


def unseal(ciphertext, key_id, **scope):
    """Open a stored row under this exact scope, or refuse and say which way.

    Everything between the keyring and the returned bundle is a statement about
    the *row*, not about the deployment: a stored value that will not open is
    :class:`CredentialUnusable` however it fails to open. They share one wire
    code on purpose -- the merchant's move is the same for all of them -- and
    ``_origin``'s line number is what separates them for an operator.
    """
    cipher = _aesgcm()
    aad = _scoped_aad(scope)
    ring, _ = _ring()
    if not isinstance(ciphertext, str) or not ciphertext.startswith("v1."):
        raise CredentialUnusable()
    if key_id not in ring:
        # Sealed under a key that has since left the ring. Rotation retires
        # ciphertext; it does not resurrect it.
        raise CredentialUnusable()
    try:
        raw = base64.b64decode(ciphertext[3:], validate=True)
    except Exception:
        raise CredentialUnusable() from None
    if len(raw) < 29:
        raise CredentialUnusable()
    try:
        plain = cipher(ring[key_id]).decrypt(raw[:12], raw[12:], aad)
    except Exception:
        # Wrong scope, tampered row, or truncated tag -- AES-GCM does not tell
        # them apart, and neither should we.
        raise CredentialUnusable() from None
    try:
        bundle = json.loads(plain)
    except Exception:
        raise CredentialUnusable() from None
    _validate(bundle, CredentialUnusable)
    return SecretBundle(bundle)


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
            # The reference exists and belongs to a different scope. Answered
            # identically to "no such reference" on the wire, deliberately.
            raise CredentialUnusable()
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
        raise CredentialUnusable()
    return unseal(row["ciphertext"], row["key_id"], **scope)
