"""The @pulsedrop account: a real ``users`` row, provisioned idempotently.

## Why a real account and not a second MEMBER_000

PulseSoc already has one official automated account, ``pulsesoc_insight``, and
it is not a user: it lives at ``user_id=0`` with only an ``arena_profiles`` row,
and ``pulse_native_profile_payload`` returns a hand-written literal dict for it
before it ever reaches the database. That shape works for an account nobody is
expected to follow — and it shows, because the same function hides the follower
and following counts for automated accounts, since for MEMBER_000 there is no
``pulse_follows`` row that could point at ``user_id=0`` in the first place.

PulseDrop has to be followable, mentionable, reportable, blockable, and has to
appear in search. Every one of those is a foreign key to ``users.user_id``.
Copying the MEMBER_000 shape would mean reimplementing each of them against a
special case, which is how a "system account" becomes a parallel social graph.
So PulseDrop is an ordinary row in ``users`` and the ordinary code paths work on
it unmodified. What makes it official is a badge, a disclosure, and the fact
that nothing can log into it.

## Why it has no credentials

``login_enabled=0`` and a null ``password_hash`` are the security design, not a
detail of it. The publisher does not authenticate as PulseDrop — it writes rows
as ``PULSEDROP_USER_ID`` from inside the worker process, which is already
trusted by the database. There is therefore no PulseDrop password, no PulseDrop
token, and nothing in the environment to leak. An attacker who wants to post as
PulseDrop needs the database, at which point the account is not the weak link.

## Why the brand assets are absolute URLs

``pulse_feed_engine._brand_media_url`` exists because React Native's ``Image``
will not load a site-relative ``/static/...`` URI — it has no origin to resolve
it against, so the avatar silently renders as nothing. That function is reused
rather than reimplemented so the two system accounts cannot disagree about what
the app's base URL is.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

log = logging.getLogger(__name__)

USERNAME = "pulsedrop"
DISPLAY_NAME = "PulseDrop"
SYSTEM_LABEL = "Official PulseSoc Commerce Account"
BIO = "Fresh finds, trending products & new drops from across PulseSoc."

#: Automation disclosure. Shown on the profile and carried on every payload.
#: An automated account that does not say so is the thing app review rejects,
#: and it is also simply untrue by omission.
AUTOMATION_DISCLOSURE = (
    "This account is operated automatically by PulseSoc. It is not a human user."
)
TRANSPARENCY_DISCLOSURE = (
    "PulseDrop highlights listings from independent PulseSoc sellers. "
    "PulseSoc is not the seller of these products."
)

#: Brand artwork, dated the way every other asset in ``static/brand`` is so a
#: replacement is a new file rather than a mutation of a cached URL.
AVATAR_PATH = "/static/brand/pulsedrop-avatar-20260926.png"
COVER_PATH = "/static/brand/pulsedrop-cover-20260926.png"

#: Append-only, never rewritten. ``pulse_feed_engine`` learned this the hard
#: way: once a profile row stores an avatar URL, the stored value outranks the
#: constant forever, so replacing the artwork requires knowing which URLs this
#: module minted in the past in order to be allowed to overwrite them. Deleting
#: an entry here re-freezes every profile still holding it.
LEGACY_AVATAR_PATHS: tuple[str, ...] = ()

#: The cover is a designed banner with a centred wordmark, not a photograph.
#: Cropping it to fill a hero cuts the wordmark, so the client is told to fit it
#: instead. Declared here rather than matched on filename in the app, which is
#: how the Insight cover is handled today and is why that treatment cannot be
#: extended to a second account without shipping a new build.
COVER_FIT = "contain"
COVER_ASPECT_RATIO = 2000 / 750

_lock = threading.Lock()
_cached_user_id = 0


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def avatar_url() -> str:
    from services import pulse_feed_engine

    return pulse_feed_engine._brand_media_url(AVATAR_PATH)


def cover_url() -> str:
    from services import pulse_feed_engine

    return pulse_feed_engine._brand_media_url(COVER_PATH)


def is_brand_avatar(value: str) -> bool:
    """True when ``value`` is an avatar this module minted, past or present."""
    candidate = str(value or "").split("?", 1)[0]
    if not candidate:
        return True
    return any(candidate.endswith(path) for path in (AVATAR_PATH, *LEGACY_AVATAR_PATHS))


def profile_overlay() -> dict:
    """The fields that make PulseDrop's profile honest about what it is.

    Merged onto the ordinary profile payload rather than replacing it, so the
    real follower count, real post count and real ``viewer_follows`` continue to
    come from the real tables.
    """
    from services.pulse_ai.content_policy import AUTOMATED_ACCOUNT_TYPE

    return {
        # Imported, not spelled again. ``pulse_feed_engine`` branches on this
        # exact string to decide a post is from an automated account; a second
        # literal is a second thing to keep in step with the first.
        "account_type": AUTOMATED_ACCOUNT_TYPE,
        "automated": True,
        "official_system_account": True,
        "system_account_label": SYSTEM_LABEL,
        "automation_disclosure": AUTOMATION_DISCLOSURE,
        "transparency_disclosure": TRANSPARENCY_DISCLOSURE,
        "brand_cover_fit": COVER_FIT,
        "brand_cover_aspect_ratio": COVER_ASPECT_RATIO,
        # Stated because the *other* automated account's answer is False, and
        # the app hid the follower and following counts for anything automated
        # on the strength of it. That was right for MEMBER_000, which cannot
        # have followers, and wrong here: being followable is the entire reason
        # this account is a real ``users`` row. The flag moves the decision from
        # "is it a bot" to "does it have a social graph", which is the question
        # actually being asked.
        "has_social_graph": True,
    }


def account_user_id(cur=None) -> int:
    """PulseDrop's ``users.user_id``, or 0 when it has not been provisioned.

    Memoised per process because it is read on the feed hydration path. The
    cache only ever caches a *found* id: caching "absent" would mean a process
    that started before provisioning never notices it happened.
    """
    global _cached_user_id
    if _cached_user_id:
        return _cached_user_id
    owns_connection = cur is None
    conn = None
    try:
        if owns_connection:
            from services import db as db_service

            conn = db_service.connect()
            cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE username=? LIMIT 1", (USERNAME,))
        row = dict(cur.fetchone() or {})
        found = int(row.get("user_id") or 0)
    except Exception:
        log.warning("pulsedrop_account_lookup_failed", exc_info=True)
        return 0
    finally:
        if owns_connection and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if found:
        with _lock:
            _cached_user_id = found
    return found


def is_pulsedrop(user_id, cur=None) -> bool:
    try:
        candidate = int(user_id or 0)
    except (TypeError, ValueError):
        return False
    return bool(candidate) and candidate == account_user_id(cur)


def ensure_account() -> int:
    """Create or reconcile @pulsedrop. Returns its user id, or 0 on failure.

    Idempotent by lookup-then-insert on a unique-ish handle rather than by a
    fixed id: ``users.user_id`` is a SERIAL on PostgreSQL, so pinning PulseDrop
    to a chosen integer would either collide with a real member or desynchronise
    the sequence.

    Reconciliation is deliberately narrow. It refreshes the brand artwork, the
    disclosure-bearing fields and the account's inability to log in — the things
    this module owns and that must not drift. It does not touch the display name
    or bio on an existing row, because an operator who edited those made a
    decision and a worker that overwrites it every 5 seconds is a bug.
    """
    global _cached_user_id
    from services import db as db_service

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        now = _now()
        cur.execute("SELECT * FROM users WHERE username=? LIMIT 1", (USERNAME,))
        existing = dict(cur.fetchone() or {})
        if existing:
            user_id = int(existing.get("user_id") or 0)
            stored_avatar = str(existing.get("avatar_url") or "")
            next_avatar = avatar_url() if is_brand_avatar(stored_avatar) else stored_avatar
            cur.execute(
                """
                UPDATE users
                SET avatar_url=?, cover_url=?, banner_url=?, account_status='active',
                    hidden_from_discovery=0, access_enabled=1, login_enabled=0,
                    profile_visibility='public', password_hash=NULL
                WHERE user_id=?
                """,
                (next_avatar, cover_url(), cover_url(), user_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users
                    (username, display_name, full_name, bio, avatar_url, cover_url, banner_url,
                     account_status, hidden_from_discovery, access_enabled, login_enabled,
                     profile_visibility, onboarding_complete, preferred_language, signup_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, 1, 0, 'public', 1, 'en', ?)
                """,
                (USERNAME, DISPLAY_NAME, DISPLAY_NAME, BIO, avatar_url(), cover_url(), cover_url(), now),
            )
            cur.execute("SELECT user_id FROM users WHERE username=? LIMIT 1", (USERNAME,))
            user_id = int(dict(cur.fetchone() or {}).get("user_id") or 0)
        if not user_id:
            conn.rollback()
            log.error("pulsedrop_account_provision_no_id")
            return 0
        _ensure_arena_profile(cur, user_id, now)
        _ensure_verified_badge(cur, user_id, now)
        conn.commit()
        with _lock:
            _cached_user_id = user_id
        return user_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        log.exception("pulsedrop_account_provision_failed")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_arena_profile(cur, user_id: int, now: str) -> None:
    """The row that gives PulseDrop its ``public_player_id``.

    Every PulseSoc identity is addressed by ``public_player_id``, not by user
    id, so without this row PulseDrop's own posts would attribute to a generated
    ``PulseSoc-nnnnnn`` handle.
    """
    cur.execute(
        "SELECT id, avatar_url FROM arena_profiles WHERE user_id=? OR public_player_id=? LIMIT 1",
        (user_id, USERNAME),
    )
    row = dict(cur.fetchone() or {})
    if row:
        stored = str(row.get("avatar_url") or "")
        next_avatar = avatar_url() if is_brand_avatar(stored) else stored
        cur.execute(
            """
            UPDATE arena_profiles
            SET user_id=?, username=?, public_player_id=?, display_name=?, avatar_url=?, rank=?, updated_at=?
            WHERE id=?
            """,
            (user_id, USERNAME, USERNAME, DISPLAY_NAME, next_avatar, SYSTEM_LABEL, now, row.get("id")),
        )
        return
    cur.execute(
        """
        INSERT INTO arena_profiles
            (user_id, username, public_player_id, display_name, avatar_url, rank, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, USERNAME, USERNAME, DISPLAY_NAME, avatar_url(), SYSTEM_LABEL, now, now),
    )


def _ensure_verified_badge(cur, user_id: int, now: str) -> None:
    """The checkmark, granted as a badge row like every other verification.

    ``pulse_native_profile_payload`` sets ``verified_badge`` from
    ``"verified" in badge_keys`` and from nothing else — notably not from
    Premium, which used to light it up and was fixed precisely because a bought
    checkmark is a lie about identity. Granting the real badge is therefore the
    only way to make PulseDrop verified, which is the correct amount of work.
    """
    cur.execute(
        "SELECT 1 FROM pulse_user_badges WHERE user_id=? AND badge_key='verified' LIMIT 1",
        (user_id,),
    )
    if cur.fetchone():
        return
    cur.execute(
        """
        INSERT INTO pulse_user_badges (user_id, badge_key, granted_by, created_at)
        VALUES (?, 'verified', 0, ?)
        ON CONFLICT (user_id, badge_key) DO NOTHING
        """,
        (user_id, now),
    )


def reset_cache() -> None:
    """Forget the memoised user id. For tests that rebuild the database."""
    global _cached_user_id
    with _lock:
        _cached_user_id = 0
