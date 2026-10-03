"""Who is allowed to open a direct message with whom.

PulseSoc ships a DM-privacy control in two places, and until this module
existed it was wired to nothing:

* **Account Command Center** (web ``/pulse/account`` and the mobile
  ``AccountCenterScreen``) writes ``user_settings.message_requests``, one of
  ``everyone`` / ``followers`` / ``none``.
* **Native privacy settings** (``PrivacySettingsScreen.tsx``) writes
  ``user_settings.pulse_native_preferences``, a JSON blob whose
  ``privacy.allowDirectMessages`` is one of ``everyone`` / ``followers`` /
  ``nobody``.

``profile_viewer_permissions._can_message`` used to read
``users.message_privacy`` / ``users.dm_privacy``. Neither column has ever
existed — not in ``init_db()``, not in production — so the lookup always fell
through to the ``everyone`` default and the preference could not deny anything.
Two shipped settings screens, no effect.

Two deliberate decisions are encoded here.

**The stricter of the two stores wins.** They are independent writers with no
shared ledger, so they can disagree. A user who set "nobody" on one screen and
never touched the other has expressed a restriction; resolving to the laxer
value would silently discard it. Privacy controls resolve toward the user's
most restrictive expressed intent.

**Absence means "everyone", not "deny".** This is the opposite of the
default-deny rule that governs *field exposure*, and the difference is the
point. For a field, absence means nobody decided to publish it, so withholding
is the safe answer. For a messaging gate, absence means the account never
expressed a restriction, and inventing one would silently break a working
product for every user who has no row. A missing table, unparseable JSON, or an
unrecognised vocabulary word therefore reads as "no restriction" — this module
degrades toward the behaviour that shipped, never toward a silent outage.

The preference governs *opening* a conversation, not continuing one. Tightening
the setting stops new strangers from reaching you; it does not retroactively
sever threads you already consented to. That matches how the control reads in
both UIs ("Message requests") and avoids a settings toggle quietly destroying
access to live conversation history.
"""

from __future__ import annotations

import json

# Canonical vocabulary. Both UIs are three-valued; the aliases cover the
# spellings each one uses plus the ones the old dead code accepted, so a value
# written by any writer resolves rather than silently reading as unrecognised.
EVERYONE = "everyone"
FOLLOWERS = "followers"
NOBODY = "nobody"

_ALIASES = {
    "everyone": EVERYONE,
    "all": EVERYONE,
    "public": EVERYONE,
    "anyone": EVERYONE,
    "followers": FOLLOWERS,
    "following": FOLLOWERS,
    "friends": FOLLOWERS,
    "friends_only": FOLLOWERS,
    "nobody": NOBODY,
    "none": NOBODY,
    "no_one": NOBODY,
    "off": NOBODY,
    "disabled": NOBODY,
}

# Most permissive first. Used to pick the stricter of two disagreeing stores.
_RANK = {EVERYONE: 0, FOLLOWERS: 1, NOBODY: 2}


def normalize(value):
    """Map a stored value onto the canonical vocabulary.

    Returns ``None`` for anything unrecognised so callers can tell "this store
    said everyone" apart from "this store said nothing I understand". The
    difference matters: an unknown word must not be allowed to out-rank a real
    restriction set on the other screen.
    """
    text = str(value or "").strip().lower()
    return _ALIASES.get(text)


def strictest(*values):
    """Return the most restrictive recognised value, or ``everyone``.

    ``None`` entries are ignored rather than treated as permissive, so an
    unreadable store cannot widen a restriction set elsewhere.
    """
    known = [v for v in values if v in _RANK]
    if not known:
        return EVERYONE
    return max(known, key=lambda v: _RANK[v])


def resolve_preference(cur, user_id):
    """The effective DM preference for ``user_id``.

    Reads both stores and returns the stricter. Never raises: a missing
    ``user_settings`` table reads as "no restriction" for the reason in the
    module docstring.
    """
    user_id = _int(user_id)
    if not user_id:
        return EVERYONE
    rows = _settings_rows(cur, user_id)
    return strictest(
        normalize(rows.get("message_requests")),
        _native_preference(rows.get("pulse_native_preferences")),
    )


def may_message(cur, target_user_id, sender_user_id, follows=None, friends=None):
    """May ``sender_user_id`` open a DM with ``target_user_id``?

    ``follows`` / ``friends`` are accepted when the caller has already resolved
    the relationship, purely to avoid repeating those queries; they are
    computed here when omitted.

    This answers the preference question only. Blocks, account status and
    profile visibility are each owned by the caller that already enforces
    them — duplicating those checks here would create a second, drifting copy
    of rules that are correct where they live.
    """
    target_user_id = _int(target_user_id)
    sender_user_id = _int(sender_user_id)
    if not target_user_id or not sender_user_id:
        return False
    if target_user_id == sender_user_id:
        # Note-to-self threads are a product decision owned by the caller, not
        # a privacy question. Nobody's DM preference applies to themselves.
        return True

    preference = resolve_preference(cur, target_user_id)
    if preference == EVERYONE:
        return True
    if preference == NOBODY:
        return False

    if follows is None:
        follows = _follows(cur, sender_user_id, target_user_id)
    if friends is None:
        friends = _friends(cur, sender_user_id, target_user_id)
    return bool(follows or friends)


def _native_preference(blob):
    """Pull ``privacy.allowDirectMessages`` out of the native settings JSON."""
    if not blob:
        return None
    try:
        data = json.loads(blob) if isinstance(blob, (str, bytes)) else blob
        privacy = (data or {}).get("privacy") or {}
        return normalize(privacy.get("allowDirectMessages"))
    except Exception:
        # Unparseable JSON is a corrupt store, not an expressed restriction.
        return None


def _settings_rows(cur, user_id):
    """Both settings keys in one read, as a plain dict."""
    try:
        cur.execute(
            "SELECT setting_key, setting_value FROM user_settings "
            "WHERE user_id=? AND setting_key IN ('message_requests', 'pulse_native_preferences')",
            (user_id,),
        )
        rows = cur.fetchall() or []
    except Exception:
        return {}
    out = {}
    for row in rows:
        try:
            key, value = row["setting_key"], row["setting_value"]
        except (TypeError, KeyError, IndexError):
            try:
                key, value = row[0], row[1]
            except Exception:
                continue
        out[str(key)] = value
    return out


def _follows(cur, sender_user_id, target_user_id):
    return _exists(
        cur,
        "SELECT 1 FROM pulse_follows WHERE follower_user_id=? AND followed_user_id=? LIMIT 1",
        (sender_user_id, target_user_id),
    )


def _friends(cur, sender_user_id, target_user_id):
    """Accepted friendship, checked against both tables the app writes to.

    Mirrors ``profile_viewer_permissions._friends``: ``pulse_friendships`` and
    ``pulse_friends`` coexist in this schema, so a single-table check would
    misreport friendship for accounts written through the other one.
    """
    if _exists(
        cur,
        "SELECT 1 FROM pulse_friendships WHERE user_id=? AND friend_user_id=? LIMIT 1",
        (sender_user_id, target_user_id),
    ):
        return True
    return _exists(
        cur,
        "SELECT 1 FROM pulse_friends WHERE user_id=? AND friend_user_id=? "
        "AND COALESCE(status,'active')='active' LIMIT 1",
        (sender_user_id, target_user_id),
    )


def _exists(cur, sql, params):
    try:
        cur.execute(sql, params)
        return bool(cur.fetchone())
    except Exception:
        return False


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
