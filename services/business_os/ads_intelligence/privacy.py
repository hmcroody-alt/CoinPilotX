"""Privacy control plane for the advertising intelligence layer.

Two jobs, both of which have to be impossible to bypass rather than merely
documented:

1. **Pseudonymisation.** Nothing in ``ads_intel_*`` stores a raw user id. The
   viewer is a ``subject_ref``: a keyed digest of the account id. Keyed rather
   than plain-hashed because the user id space is small and enumerable — a bare
   ``sha256(user_id)`` over a few million sequential integers is reversible on a
   laptop in minutes, which would make the analytics store a second, unguarded
   copy of the user table. Deterministic rather than random because deletion and
   per-user export have to stay a single exact lookup; a random mapping would
   need its own lookup table, which is the identifier we were trying not to keep.

2. **Purpose limitation.** Every stored signal carries a privacy class, and
   consumers ask :func:`allows` before using it. Restricting what a signal may
   be used for then becomes a data change, rather than an audit of every reader.

The forbidden-source denylist is checked at ingest. It lists *origins* (message
bodies, call audio, health, biometrics) rather than topics, because the realistic
failure is a well-meaning caller piping an entire surface's activity into the ad
system — not someone typing a sensitive word into a category field.

Nothing here touches the database.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading

from . import taxonomy

_SALT_ENV = "ADS_INTEL_SUBJECT_SALT"

# Used only when the env var is unset. Determinism matters more than secrecy in
# development — a per-process random salt would make yesterday's dev rows
# unjoinable to today's and would silently break local deletion tests. In
# production the env var must be set; we warn exactly once if it is not.
_DEV_FALLBACK_SALT = "ads-intel-dev-salt-not-for-production"

_warned = False
_warn_lock = threading.Lock()


def _salt() -> str:
    global _warned
    value = (os.environ.get(_SALT_ENV) or "").strip()
    if value:
        return value
    if not _warned:
        with _warn_lock:
            if not _warned:
                _warned = True
                logging.warning(
                    "ADS_INTEL_SUBJECT_SALT is unset; using the development "
                    "fallback salt. Subject pseudonymisation is NOT secret "
                    "until this is set in the environment.")
    return _DEV_FALLBACK_SALT


def _digest(namespace: str, raw: str) -> str:
    """Keyed digest, namespaced so the same id in two roles is two refs.

    Without the namespace, a user id and a session id that happened to collide
    numerically would produce the same ref, silently merging two subjects.
    """
    msg = f"{namespace}:{raw}".encode("utf-8")
    return hmac.new(_salt().encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def subject_ref(user_id) -> str | None:
    """Pseudonymous, stable reference for a viewer. ``None`` for anonymity.

    Callers pass the raw account id; this is the only place that id is allowed
    to enter the subsystem, and it does not survive the call.
    """
    if user_id is None:
        return None
    raw = str(user_id).strip()
    if not raw or raw.lower() in ("none", "null", "0"):
        return None
    return _digest("subject", raw)


def session_ref(session_id) -> str | None:
    """Pseudonymous reference for a delivery session.

    Sessions are the unit for frequency capping and quick-skip detection, so
    they must be stable within a session and uncorrelatable across them. A
    client-supplied opaque session id is digested the same way as a user id.
    """
    if session_id is None:
        return None
    raw = str(session_id).strip()
    if not raw:
        return None
    return _digest("session", raw)


def allows(privacy_class: str, purpose: str) -> bool:
    """Whether a signal of this class may be used for this purpose.

    Unknown classes and unknown purposes both deny. Failing closed matters here:
    a typo in a privacy class must not silently widen what a signal may be used
    for, and a new purpose must be added to the taxonomy deliberately.
    """
    perms = taxonomy.PRIVACY_CLASS_PERMISSIONS.get(str(privacy_class or ""))
    if not perms:
        return False
    return bool(perms.get(str(purpose or ""), False))


def is_forbidden_source(source) -> bool:
    """Whether a signal origin is barred from this subsystem entirely."""
    return str(source or "").strip().lower() in taxonomy.FORBIDDEN_SIGNAL_SOURCES


def classify_event(event_name: str) -> str:
    """Default privacy class for an event name.

    Conversions and engagement are ordinary product behaviour. Anything the
    system infers about *dislike* is measurement-only: a hide or a report must
    shape reporting and fraud review, but letting it shape targeting turns a
    complaint into a profile attribute.
    """
    name = str(event_name or "")
    if name in taxonomy.NEGATIVE_EVENTS:
        return "measurement_only"
    return "product_signal"


def retention_days(record_class: str) -> int:
    """Retention for a record class; 0 when the class is unknown.

    Zero rather than a generous default, so an unclassified record is visible as
    a bug in the retention sweep instead of being kept forever by accident.
    """
    return int(taxonomy.RETENTION_DAYS.get(str(record_class or ""), 0))
