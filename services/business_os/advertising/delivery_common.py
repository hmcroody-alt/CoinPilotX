"""Business OS — Advertising slice 7 shared delivery primitives.

Low-level, dependency-light helpers shared by the delivery-eligibility,
candidate-selection, delivery-instance, frequency-cap, and event services. Kept
in its own module so those services can share primitives WITHOUT importing each
other (no import cycle). Nothing here reads campaign/creative state or delivers;
it only provides time, ids, a privacy-safe subject reference, and the delivery
token HMAC.

Privacy (spec §9): a viewer is represented everywhere downstream by
``subject_ref`` — a salted SHA-256 hash of the canonical user id, truncated. The
raw user id is NEVER persisted in any delivery/impression/click row, yet the hash
is stable per viewer so a per-viewer frequency cap still works. The salt comes
from ``BUSINESS_OS_AD_SUBJECT_SALT`` (a fixed dev default is used when unset so
tests are deterministic; production must set its own).

Token (spec §3): ``impression_token`` is an HMAC over the delivery id keyed by a
server secret. It proves the client received THIS delivery. It carries no
authority on its own — the bound creative/version/placement/destination are
always read back from the stored delivery row, so a client can never substitute a
different creative while reusing a delivery id.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services import db


# --- config (safe defaults; overridable via env) ----------------------------
SUBJECT_SALT_ENV = "BUSINESS_OS_AD_SUBJECT_SALT"
TOKEN_SECRET_ENV = "BUSINESS_OS_AD_TOKEN_SECRET"
_DEFAULT_SUBJECT_SALT = "pulsesoc-ad-subject-v1"
_DEFAULT_TOKEN_SECRET = "pulsesoc-ad-token-v1"

# A delivery opportunity is short-lived: it authorizes ONE display. Default TTL is
# conservative; an expired instance rejects impressions/clicks.
DELIVERY_TTL_SECONDS = int(os.environ.get("BUSINESS_OS_AD_DELIVERY_TTL", "1800") or "1800")

# Frequency cap: max impressions per viewer per campaign within the rolling
# window. Conservative MVP defaults; safe-overridable via env.
FREQ_CAP_MAX = int(os.environ.get("BUSINESS_OS_AD_FREQ_CAP", "3") or "3")
FREQ_CAP_WINDOW_SECONDS = int(
    os.environ.get("BUSINESS_OS_AD_FREQ_WINDOW", "86400") or "86400")

# Per-viewer delivery-request rate limit (basic abuse control, spec §8): max
# delivery instances created for one subject within the short rolling window.
REQUEST_RATE_MAX = int(os.environ.get("BUSINESS_OS_AD_REQ_RATE_MAX", "60") or "60")
REQUEST_RATE_WINDOW_SECONDS = int(
    os.environ.get("BUSINESS_OS_AD_REQ_RATE_WINDOW", "60") or "60")

# Placement -> creative types compatible with it. Strict allowlist; Feed carries
# static/video creatives, Reels carries vertical reels video only.
PLACEMENTS_SUPPORTED = ("feed", "reels")
PLACEMENT_CREATIVE_COMPAT = {
    "feed": {"image", "video"},
    "reels": {"reels_video"},
}


def _subject_salt() -> str:
    return (os.environ.get(SUBJECT_SALT_ENV) or _DEFAULT_SUBJECT_SALT)


def _token_secret() -> bytes:
    return (os.environ.get(TOKEN_SECRET_ENV) or _DEFAULT_TOKEN_SECRET).encode("utf-8")


# --- time / ids -------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 (optionally Z-suffixed) timestamp to aware UTC, or None."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    iso_s = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso_s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def sid(value: Any) -> str:
    return str(value)


# --- privacy-safe subject reference -----------------------------------------
def subject_ref(user_id: Any) -> str:
    """Stable, privacy-safe reference for a viewer. Salted SHA-256 of the canonical
    user id, hex-truncated. Never reversible to the raw id in storage, but stable
    per viewer so the per-viewer frequency cap works."""
    raw = f"{_subject_salt()}:{sid(user_id)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# --- delivery token ---------------------------------------------------------
def make_impression_token(delivery_id: str) -> str:
    return hmac.new(_token_secret(), delivery_id.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_impression_token(delivery_id: str, token: Any) -> bool:
    if not token or not isinstance(token, str):
        return False
    expected = make_impression_token(delivery_id)
    return hmac.compare_digest(expected, token.strip())


def expires_at_from(decision_dt: datetime, ttl_seconds: Optional[int] = None) -> str:
    ttl = DELIVERY_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    return iso(decision_dt + timedelta(seconds=ttl))


# --- db helpers -------------------------------------------------------------
def row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def begin(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")


def commit(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        conn.execute("COMMIT")
    else:
        conn.commit()


def rollback(conn) -> None:
    try:
        if db.ENGINE_NAME == "sqlite":
            conn.execute("ROLLBACK")
        else:
            conn.rollback()
    except Exception:
        pass
