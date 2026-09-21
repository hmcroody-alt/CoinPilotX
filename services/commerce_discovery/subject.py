"""Time, ids, the privacy-safe viewer reference, and the placement token.

Dependency-light on purpose: every other module in this package imports this
one, so it must import nothing from the package except ``config``. That is what
keeps ``eligibility`` → ``ranking`` → ``engine`` → ``events`` a straight line
rather than a cycle.

The viewer reference
--------------------

Downstream, a viewer is a 32-hex ``subject_ref`` and nothing else. The raw
``user_id`` never reaches a ``commerce_discovery_*`` row.

This is not decoration. The tables answer questions like "what has this person
been shown, and what did they reject" — which is a behavioural profile, and the
kind of table that gets exported to a BI tool by someone who never read this
file. A salted hash keeps the frequency caps and suppressions working exactly as
well (they only ever need *stable*, never *reversible*) while making the export
useless as a profile of a named person.

The salt differs from advertising's by design. A shared salt would let anyone
holding both tables join an organic viewer to a paid viewer — reconstructing
exactly the cross-class linkage the promotion wall exists to prevent, without
either table containing a user id.

The placement token
-------------------

``impression_token`` is an HMAC over the placement id. It proves a client was
handed *this* placement, and nothing more. It carries no authority of its own:
listing, seller, promotion class and score are always read back from the stored
placement row, so quoting a token can never substitute a different product or
reclassify a paid placement as organic.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config


# --- time -------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return iso(now_utc())


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 (optionally ``Z``-suffixed) timestamp to aware UTC.

    Returns ``None`` for anything unparseable. Callers treat ``None`` as "no
    usable timestamp" and fall back to a conservative answer — an unreadable
    ``expires_at`` reads as expired, not as valid forever.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def window_start_iso(seconds: int) -> str:
    """ISO timestamp ``seconds`` ago — the left edge of a rolling window."""
    return iso(now_utc() - timedelta(seconds=max(1, int(seconds))))


def expiry_iso(seconds: int) -> str:
    return iso(now_utc() + timedelta(seconds=max(1, int(seconds))))


def is_expired(value: Any) -> bool:
    """True when ``value`` is a past timestamp **or** is unreadable.

    Fails closed. An expired placement is a harmless no-op; a placement that
    never expires because its timestamp was corrupt is an event-log write path
    with no lifetime at all.
    """
    parsed = parse_iso(value)
    if parsed is None:
        return True
    return parsed <= now_utc()


# --- ids --------------------------------------------------------------------
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def sid(value: Any) -> str:
    return str(value)


# --- privacy-safe viewer reference ------------------------------------------
def subject_ref(user_id: Any) -> str:
    """Stable, non-reversible reference for one viewer."""
    raw = f"{config.subject_salt()}:{sid(user_id)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# --- placement token --------------------------------------------------------
def make_token(placement_id: str) -> str:
    return hmac.new(
        config.token_secret(), placement_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_token(placement_id: str, token: Any) -> bool:
    if not token or not isinstance(token, str):
        return False
    return hmac.compare_digest(make_token(placement_id), token.strip())
