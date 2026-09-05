"""Application-level encryption for RESTRICTED field values.

What this actually protects against, stated plainly
---------------------------------------------------
AES-256-GCM, keys read from the process environment. That means:

* A database dump, a backup file, a replica, a stolen disk, a leaked query
  result or a support engineer with read access to ``private_record_fields``
  sees ciphertext.
* An attacker who has the application's environment — the running process, the
  deploy configuration, the platform's variable store — has the keys and can
  decrypt everything. Nothing here changes that.

Both halves matter. The first is a real and worthwhile boundary: it is the
boundary almost every actual disclosure of this kind of data crosses. The
second is why this module does not describe itself as end-to-end encryption,
zero-knowledge storage, or a hardware-backed vault, and why
:func:`describe` reports ``"application-level AES-256-GCM, keys in process
environment"`` rather than a marketing word. A member deciding whether to store
a passport number here deserves the true sentence.

Refuse rather than pretend
--------------------------
If no key is configured, :func:`encrypt` raises. It does not fall back to
base64, to a hash, to XOR against a constant, or to "store it plainly for now
and encrypt it later" — and the writer that calls it refuses the field rather
than storing it. This is the whole reason the module exists as its own file: an
encryption helper with a silent plaintext fallback is worse than no encryption
helper, because the schema, the API and the UI all go on saying the value is
protected. A store that cannot protect a passport number should decline to hold
it, visibly, at the moment of the write.

Binding, not just hiding
------------------------
Every ciphertext is authenticated against ``owner_user_id | record_key |
field_path``. GCM verifies that associated data on decrypt, so a ciphertext
lifted from one member's row and pasted into another's does not decrypt — it
fails authentication. The cross-account isolation of a restricted value is
therefore a cryptographic property and not only a WHERE clause, which matters
because the WHERE clause is the thing an injection or a mistaken join defeats.

Rotation
--------
``PRIVATE_OFFICE_FIELD_KEYS`` is a keyring: ``id:key,id:key``. Every ciphertext
records the id that produced it, so retiring a key is removing it from the
active slot rather than rewriting rows. Old ids stay in the ring until nothing
references them; :func:`key_ids` and the ``cipher_key_id`` column together
answer "what still needs re-encrypting".
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re

LOGGER = logging.getLogger("private_office.field_crypto")

#: Comma-separated ``key_id:material`` pairs. Material is 32 bytes, given as
#: standard base64, url-safe base64 or hex.
ENV_KEYRING = "PRIVATE_OFFICE_FIELD_KEYS"
#: Which id new ciphertext is written with. Defaults to the first in the ring.
ENV_ACTIVE_KEY = "PRIVATE_OFFICE_FIELD_KEY_ACTIVE"

SCHEME = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12

#: The description :func:`describe` returns and the API repeats. It is written
#: to be true rather than reassuring — see the module docstring.
MECHANISM = "application-level AES-256-GCM, keys in process environment"

_KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,31}$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/_\-]+={0,2}$")


class FieldCryptoUnavailable(RuntimeError):
    """No usable key. The caller must refuse the write, not downgrade it."""


class FieldCryptoError(ValueError):
    """A ciphertext could not be produced or read.

    The message never contains plaintext, key material, or which of several
    reasons applied. A decrypt that reports "wrong key" separately from "wrong
    owner" is an oracle, and error strings end up in logs.
    """


# ---------------------------------------------------------------------------
# Keyring
# ---------------------------------------------------------------------------
def _decode_key(material: str) -> bytes | None:
    text = str(material or "").strip()
    if not text:
        return None
    if len(text) == KEY_BYTES * 2:
        try:
            return binascii.unhexlify(text)
        except (binascii.Error, ValueError):
            return None
    if not _B64_RE.match(text):
        return None
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == KEY_BYTES:
            return raw
    return None


def _keyring() -> dict[str, bytes]:
    """Parse the environment keyring. Never raises, never logs key material.

    Read on every call rather than cached. The cost is a string split; the
    benefit is that a key rotated in the platform's variable store takes effect
    on the next request rather than on the next deploy, and that a test can set
    and unset the variable without reaching into module state.
    """
    ring: dict[str, bytes] = {}
    raw = os.environ.get(ENV_KEYRING, "") or ""
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key_id, _, material = entry.partition(":")
        key_id = key_id.strip().lower()
        if not _KEY_ID_RE.match(key_id):
            # Logged by id-shape only. The material is never in a log line,
            # not even to say it was malformed.
            LOGGER.warning("PRIVATE_FIELD_KEY_ID_INVALID length=%d", len(key_id))
            continue
        decoded = _decode_key(material)
        if decoded is None:
            LOGGER.warning("PRIVATE_FIELD_KEY_MATERIAL_INVALID key_id=%s", key_id)
            continue
        ring[key_id] = decoded
    return ring


def key_ids() -> tuple[str, ...]:
    """Ids in the ring, sorted. Never the material."""
    return tuple(sorted(_keyring()))


def active_key_id() -> str:
    """The id new ciphertext is written with, or ``""`` when there is none."""
    ring = _keyring()
    if not ring:
        return ""
    requested = str(os.environ.get(ENV_ACTIVE_KEY, "") or "").strip().lower()
    if requested and requested in ring:
        return requested
    if requested:
        # Naming a key that is not in the ring is a configuration error, and
        # the safe reading is "this deploy cannot write restricted fields"
        # rather than "quietly use a different key than the one you named" —
        # the second silently defeats a rotation.
        LOGGER.error("PRIVATE_FIELD_ACTIVE_KEY_ABSENT key_id=%s", requested)
        return ""
    return sorted(ring)[0]


def available() -> bool:
    """Can this process store a RESTRICTED value at all?

    Callers use this to decide whether to *offer* restricted storage, so that a
    member is told up front rather than after typing a passport number into a
    form that then refuses to save.
    """
    if not active_key_id():
        return False
    try:
        _aesgcm(active_key_id())
    except Exception:
        return False
    return True


def describe() -> dict:
    """Status for an operator or a status endpoint. Contains no key material."""
    ids = key_ids()
    return {
        "available": available(),
        "mechanism": MECHANISM if ids else "",
        "scheme": SCHEME,
        "active_key_id": active_key_id(),
        "key_count": len(ids),
        # Deliberately not a claim of anything stronger. See the module
        # docstring: an attacker holding the application environment holds the
        # keys, and a status endpoint that implied otherwise would be the
        # fabricated compliance claim the mission forbids.
        "protects_against": "database-level disclosure (dumps, backups, replicas, table reads)",
        "does_not_protect_against": "an attacker with the application environment",
    }


def _aesgcm(key_id: str):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ring = _keyring()
    material = ring.get(key_id)
    if material is None:
        raise FieldCryptoUnavailable(f"no key for id {key_id!r}")
    return AESGCM(material)


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------
def associated_data(owner_user_id: int, record_key: str, field_path: str) -> bytes:
    """The tuple a ciphertext is bound to.

    Owner, record and field. Moving a ciphertext to a different owner, a
    different record or a different field of the same record all break
    authentication, which turns three separate classes of mistake — a bad join,
    a copied row in a migration, a field renamed without re-encrypting — into a
    loud failure instead of a value surfacing under the wrong label.
    """
    return "|".join((
        str(int(owner_user_id)),
        str(record_key or ""),
        str(field_path or ""),
    )).encode("utf-8")


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------
def encrypt(plaintext: str, *, owner_user_id: int, record_key: str,
            field_path: str) -> tuple[str, str]:
    """Return ``(ciphertext, key_id)``.

    Raises :class:`FieldCryptoUnavailable` when no key is configured. There is
    no fallback and there must never be one — see the module docstring.
    """
    key_id = active_key_id()
    if not key_id:
        raise FieldCryptoUnavailable(
            "no private office field key is configured; "
            "restricted values cannot be stored")

    if plaintext is None:
        raise FieldCryptoError("nothing to encrypt")

    cipher = _aesgcm(key_id)
    nonce = os.urandom(NONCE_BYTES)
    aad = associated_data(owner_user_id, record_key, field_path)
    try:
        sealed = cipher.encrypt(nonce, str(plaintext).encode("utf-8"), aad)
    except Exception as exc:  # pragma: no cover - library-level failure
        LOGGER.error("PRIVATE_FIELD_ENCRYPT_FAILED error=%s", exc.__class__.__name__)
        raise FieldCryptoError("could not encrypt") from None

    token = ".".join((
        SCHEME,
        key_id,
        base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(sealed).decode("ascii").rstrip("="),
    ))
    return token, key_id


def token_key_id(token: str) -> str:
    """The key id inside a ciphertext, or ``""``.

    Lets a rotation sweep read the column rather than the value, so counting
    what still needs re-encrypting never decrypts anything.
    """
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != SCHEME:
        return ""
    return parts[1] if _KEY_ID_RE.match(parts[1]) else ""


def decrypt(token: str, *, owner_user_id: int, record_key: str,
            field_path: str) -> str:
    """Recover a plaintext. Raises on anything that is not exactly right.

    Every failure — malformed token, unknown key, wrong owner, tampered
    ciphertext — raises the same exception with the same message. Telling them
    apart would let a caller use the error as an oracle for which member a
    ciphertext belongs to.
    """
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != SCHEME or not _KEY_ID_RE.match(parts[1] or ""):
        raise FieldCryptoError("could not decrypt")

    _scheme, key_id, nonce_b64, sealed_b64 = parts
    try:
        nonce = base64.urlsafe_b64decode(nonce_b64 + "=" * (-len(nonce_b64) % 4))
        sealed = base64.urlsafe_b64decode(sealed_b64 + "=" * (-len(sealed_b64) % 4))
    except (binascii.Error, ValueError):
        raise FieldCryptoError("could not decrypt") from None

    if len(nonce) != NONCE_BYTES:
        raise FieldCryptoError("could not decrypt")

    try:
        cipher = _aesgcm(key_id)
    except FieldCryptoUnavailable:
        # A key that has been removed from the ring is a real operational
        # state — a rotation that retired it too early — and it must be
        # distinguishable to an *operator*, who can see the log, without being
        # distinguishable to a *caller*, who only sees the exception.
        LOGGER.error("PRIVATE_FIELD_DECRYPT_KEY_ABSENT key_id=%s", key_id)
        raise FieldCryptoError("could not decrypt") from None

    aad = associated_data(owner_user_id, record_key, field_path)
    try:
        opened = cipher.decrypt(nonce, sealed, aad)
    except Exception:
        raise FieldCryptoError("could not decrypt") from None

    try:
        return opened.decode("utf-8")
    except UnicodeDecodeError:
        raise FieldCryptoError("could not decrypt") from None


def generate_key() -> str:
    """A fresh 32-byte key, url-safe base64. For operators seeding the ring.

    Deliberately here rather than in a script: the one place that knows the
    required length and encoding should also be the place that produces them,
    so an operator does not have to guess and end up with a 16-byte key that
    silently fails to parse and leaves restricted storage unavailable.
    """
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii").rstrip("=")
