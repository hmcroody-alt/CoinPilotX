"""HTTP surface for the PulseSoc native settings platform.

Everything the native Settings screens read or write goes through here. The
client contract is `mobile-native/src/settings/api.ts`; the preference shape it
validates against is `mobile-native/src/settings/schema.ts`. Those two files and
this one are the whole of the settings transport, and the normalizer below is a
deliberate mirror of the client's `normalizePreferences` — see "Why normalize
twice" below.

Design notes
------------

*Storage.* Preferences live in the existing `user_settings` key-value table
under a single row (`pulse_native_preferences`) holding a JSON document, plus a
sibling row holding the revision counter. A JSON document rather than a row per
leaf because the client already treats preferences as one atomic object with a
single revision: it patches by group, rolls back by group, and compares whole
documents for equality. Spreading ~60 leaves across 60 rows would buy nothing
and would make the read a fan-out.

*Concurrency.* Writes are last-write-wins per group, guarded by a monotonic
`revision`. A PATCH carrying a stale revision is still applied — but only to the
groups it names, which is the point of sending a partial patch. Two devices
editing Appearance and Notifications respectively both win; two devices editing
Appearance race and the later write is authoritative. The alternative (409 on
stale revision) would make a phone that was offline for an hour unable to save
a theme change without a merge UI, which is a worse outcome than losing a
redundant write.

*Why normalize twice.* The client normalizes because it must survive a hostile
or outdated server response. The server normalizes because it must survive a
hostile or outdated client — a stale app build, or a crafted request. Neither
is redundant: they defend opposite directions. This is also what makes
"every permission is enforced by the backend" true rather than aspirational —
`privacy_snapshot()` below is the function other subsystems call to ask what a
user has actually allowed, and it reads the same stored document.

*Failure semantics.* Mirrors what the client expects: reads that produce a list
(blocked, muted, sessions) return a real error on failure rather than an empty
list, because an empty list is a positive claim ("no other device is signed in")
and making that claim when the database was unreachable is the wrong failure
mode on a security surface.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

LOGGER = logging.getLogger(__name__)

settings_blueprint = Blueprint("pulse_mobile_settings", __name__)

API_PREFIX = "/api/pulse/mobile/settings"

PREFERENCES_KEY = "pulse_native_preferences"
REVISION_KEY = "pulse_native_preferences_revision"

# A relationship list is a UI surface, not a bulk export. These caps keep one
# request from paging in an entire block list built by a script.
MAX_RELATIONSHIP_ROWS = 500
MAX_SESSION_ROWS = 100

# How long a deletion request stays cancellable. Signing back in inside this
# window cancels it, which is the behaviour the native screen promises the user.
DELETION_GRACE_DAYS = 30


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(payload, status: int = 200):
    response = jsonify(payload)
    # Settings are per-user and change on write; a cached copy shown to the
    # wrong account, or a stale copy shown after a save, are both worse than a
    # round trip.
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400):
    return _json({"ok": False, "message": message}, status)


def _require_user():
    """Resolve the caller, or produce the 401 the client treats as permanent."""
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("SETTINGS_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return None, _error("Login required.", 401)
    return user, None


def _with_db(handler):
    """Run handler(cur, conn) inside a committed transaction."""
    bot = _bot()
    conn = bot.db()
    try:
        try:
            import sqlite3

            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        result = handler(cur, conn)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _row(value) -> dict:
    return dict(value) if value else {}


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def ensure_settings_schema(cur) -> None:
    """Tables this blueprint owns.

    `user_settings` and `blocked_users` already exist and are shared with the
    web app, so they are created by `bot.init_db` rather than here; calling
    `CREATE TABLE IF NOT EXISTS` for them anyway keeps this module usable
    against a database that has only ever served the native app.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            setting_key TEXT,
            setting_value TEXT,
            updated_at TEXT,
            UNIQUE(user_id, setting_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER,
            blocked_user_id INTEGER,
            reason TEXT,
            created_at TEXT,
            UNIQUE(blocker_user_id, blocked_user_id)
        )
        """
    )
    # Muting had no table: the web app only ever implemented blocking. Muting is
    # deliberately a separate relation rather than a column on `blocked_users` —
    # they are independent (you can mute someone you have not blocked, and
    # blocking is not a superset of muting for content ranking purposes).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_muted_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muter_user_id INTEGER NOT NULL,
            muted_user_id INTEGER NOT NULL,
            scope TEXT DEFAULT 'all',
            created_at TEXT,
            UNIQUE(muter_user_id, muted_user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_account_data_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            request_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reference TEXT,
            source TEXT,
            requested_at TEXT,
            scheduled_for TEXT,
            completed_at TEXT,
            cancelled_at TEXT
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_pulse_muted_users_muter ON pulse_muted_users(muter_user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pulse_account_data_requests_user "
        "ON pulse_account_data_requests(user_id, request_type, status)",
    ):
        try:
            cur.execute(statement)
        except Exception:
            # An index failing to build must not take the endpoint down; the
            # queries are correct without it, just slower.
            LOGGER.warning("SETTINGS_INDEX_SKIPPED statement=%s", statement.split(" ON ")[0])


# --------------------------------------------------------------------------
# Preference normalization
#
# Mirrors mobile-native/src/settings/schema.ts. Kept structurally parallel to it
# (same group order, same key order, same bounds) so a reviewer can diff the two
# by eye. `normalize_preferences` is total: any input, including None, a list,
# or a document written by a future app version, yields a complete document.
# --------------------------------------------------------------------------

NOTIFICATION_CATEGORIES = (
    "likes",
    "comments",
    "mentions",
    "follows",
    "messages",
    "calls",
    "live",
    "reels",
    "groups",
    "marketplace",
    "security",
    "product",
)

AUDIENCES = ("everyone", "followers", "nobody")
PROFILE_VISIBILITIES = ("public", "followers", "private")
THEMES = ("system", "light", "dark")
TIME_FORMATS = ("12h", "24h")
DOWNLOAD_POLICIES = ("always", "wifi", "never")
MEDIA_QUALITIES = ("auto", "data_saver", "high")

FONT_SCALE_MIN = 0.85
FONT_SCALE_MAX = 1.4
FONT_SCALE_STEP = 0.05
CACHE_LIMIT_MIN_MB = 128
CACHE_LIMIT_MAX_MB = 8192


def _default_categories() -> dict:
    base = {category: {"push": True, "email": False, "inApp": True} for category in NOTIFICATION_CATEGORIES}
    base["security"] = {"push": True, "email": True, "inApp": True}
    base["product"] = {"push": False, "email": True, "inApp": False}
    return base


def default_preferences() -> dict:
    return {
        "appearance": {
            "theme": "system",
            "fontScale": 1.0,
            "reduceTransparency": False,
            "compactDensity": False,
        },
        "accessibility": {
            "reduceMotion": False,
            "boldText": False,
            "highContrast": False,
            "captionsEnabled": True,
            "hapticFeedback": True,
            "screenReaderHints": True,
        },
        "language": {
            "appLanguage": "en",
            "contentLanguages": ["en"],
            "autoTranslate": False,
            "region": "auto",
            "timeFormat": "12h",
        },
        "notifications": {
            "pushEnabled": True,
            "emailEnabled": True,
            "smsEnabled": False,
            "sound": True,
            "vibration": True,
            "previewText": True,
            "quietHoursEnabled": False,
            "quietHoursStart": "22:00",
            "quietHoursEnd": "07:00",
            "categories": _default_categories(),
        },
        "privacy": {
            "accountVisibility": "public",
            "lastSeen": "followers",
            "onlineStatus": True,
            "readReceipts": True,
            "storyAudience": "followers",
            "liveAudience": "everyone",
            "allowTagging": "everyone",
            "allowMentions": "everyone",
            "allowDirectMessages": "everyone",
            "searchableByEmail": True,
            "searchableByPhone": False,
        },
        "security": {
            "twoFactorEnabled": False,
            "biometricUnlock": False,
            "loginAlerts": True,
            "requirePasswordForSensitiveChanges": True,
        },
        "storage": {
            "autoDownloadPhotos": "wifi",
            "autoDownloadVideos": "wifi",
            "autoDownloadAudio": "wifi",
            "mediaQuality": "auto",
            "cacheLimitMb": 1024,
            "autoClearCache": False,
        },
        "data": {
            "personalizedAds": True,
            "shareAnalytics": True,
            "shareCrashReports": True,
            "activityStatusSharing": True,
        },
        "developer": {
            "enabled": False,
            "showPerfOverlay": False,
            "verboseApiLogging": False,
        },
    }


PREFERENCE_GROUPS = tuple(default_preferences().keys())

_TRUE_TOKENS = {True, 1, "1", "true", "on", "yes"}
_FALSE_TOKENS = {False, 0, "0", "false", "off", "no"}


def _bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    key = value.strip().lower() if isinstance(value, str) else value
    # `1 == True` in Python, so bools are handled above before set membership.
    if key in _TRUE_TOKENS:
        return True
    if key in _FALSE_TOKENS:
        return False
    return fallback


def _one_of(value, allowed, fallback: str) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    for option in allowed:
        if option.lower() == text:
            return option
    return fallback


def _clamp(value, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return min(maximum, max(minimum, parsed))


def _quantize_font_scale(value: float) -> float:
    clamped = min(FONT_SCALE_MAX, max(FONT_SCALE_MIN, value))
    steps = round((clamped - FONT_SCALE_MIN) / FONT_SCALE_STEP)
    return round(FONT_SCALE_MIN + steps * FONT_SCALE_STEP, 2)


def _time_of_day(value, fallback: str) -> str:
    text = str(value or "").strip()
    if ":" not in text:
        return fallback
    hours_text, _, minutes_text = text.partition(":")
    if not hours_text.isdigit() or not minutes_text.isdigit() or len(minutes_text) != 2:
        return fallback
    hours, minutes = int(hours_text), int(minutes_text)
    if hours > 23 or minutes > 59:
        return fallback
    return f"{hours:02d}:{minutes:02d}"


def _language_tag(value, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 35:
        return fallback
    parts = text.split("-")
    primary = parts[0]
    if not (2 <= len(primary) <= 3) or not primary.isalpha():
        return fallback
    for part in parts[1:]:
        if not (2 <= len(part) <= 8) or not part.isalnum():
            return fallback
    return text.lower()


def _language_tags(value, fallback: list) -> list:
    if not isinstance(value, list):
        return list(fallback)
    seen, tags = set(), []
    for entry in value[:20]:
        tag = _language_tag(entry, "")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags or list(fallback)


def _channels(value, fallback: dict) -> dict:
    source = value if isinstance(value, dict) else {}
    return {
        "push": _bool(source.get("push"), fallback["push"]),
        "email": _bool(source.get("email"), fallback["email"]),
        "inApp": _bool(source.get("inApp"), fallback["inApp"]),
    }


def normalize_preferences(payload, base: dict | None = None) -> dict:
    """Total normalizer: arbitrary input in, complete valid document out."""
    base = base or default_preferences()
    raw = payload if isinstance(payload, dict) else {}

    def group(name: str) -> dict:
        value = raw.get(name)
        return value if isinstance(value, dict) else {}

    appearance = group("appearance")
    accessibility = group("accessibility")
    language = group("language")
    notifications = group("notifications")
    privacy = group("privacy")
    security = group("security")
    storage = group("storage")
    data = group("data")
    developer = group("developer")

    raw_categories = notifications.get("categories")
    raw_categories = raw_categories if isinstance(raw_categories, dict) else {}

    region = str(language.get("region") or "").strip()

    return {
        "appearance": {
            "theme": _one_of(appearance.get("theme"), THEMES, base["appearance"]["theme"]),
            "fontScale": _quantize_font_scale(
                _clamp(appearance.get("fontScale"), FONT_SCALE_MIN, FONT_SCALE_MAX, base["appearance"]["fontScale"])
            ),
            "reduceTransparency": _bool(appearance.get("reduceTransparency"), base["appearance"]["reduceTransparency"]),
            "compactDensity": _bool(appearance.get("compactDensity"), base["appearance"]["compactDensity"]),
        },
        "accessibility": {
            key: _bool(accessibility.get(key), base["accessibility"][key])
            for key in ("reduceMotion", "boldText", "highContrast", "captionsEnabled", "hapticFeedback", "screenReaderHints")
        },
        "language": {
            "appLanguage": _language_tag(language.get("appLanguage"), base["language"]["appLanguage"]),
            "contentLanguages": _language_tags(language.get("contentLanguages"), base["language"]["contentLanguages"]),
            "autoTranslate": _bool(language.get("autoTranslate"), base["language"]["autoTranslate"]),
            "region": (region or base["language"]["region"])[:64],
            "timeFormat": _one_of(language.get("timeFormat"), TIME_FORMATS, base["language"]["timeFormat"]),
        },
        "notifications": {
            "pushEnabled": _bool(notifications.get("pushEnabled"), base["notifications"]["pushEnabled"]),
            "emailEnabled": _bool(notifications.get("emailEnabled"), base["notifications"]["emailEnabled"]),
            "smsEnabled": _bool(notifications.get("smsEnabled"), base["notifications"]["smsEnabled"]),
            "sound": _bool(notifications.get("sound"), base["notifications"]["sound"]),
            "vibration": _bool(notifications.get("vibration"), base["notifications"]["vibration"]),
            "previewText": _bool(notifications.get("previewText"), base["notifications"]["previewText"]),
            "quietHoursEnabled": _bool(notifications.get("quietHoursEnabled"), base["notifications"]["quietHoursEnabled"]),
            "quietHoursStart": _time_of_day(notifications.get("quietHoursStart"), base["notifications"]["quietHoursStart"]),
            "quietHoursEnd": _time_of_day(notifications.get("quietHoursEnd"), base["notifications"]["quietHoursEnd"]),
            "categories": {
                category: _channels(raw_categories.get(category), base["notifications"]["categories"][category])
                for category in NOTIFICATION_CATEGORIES
            },
        },
        "privacy": {
            "accountVisibility": _one_of(
                privacy.get("accountVisibility"), PROFILE_VISIBILITIES, base["privacy"]["accountVisibility"]
            ),
            "lastSeen": _one_of(privacy.get("lastSeen"), AUDIENCES, base["privacy"]["lastSeen"]),
            "onlineStatus": _bool(privacy.get("onlineStatus"), base["privacy"]["onlineStatus"]),
            "readReceipts": _bool(privacy.get("readReceipts"), base["privacy"]["readReceipts"]),
            "storyAudience": _one_of(privacy.get("storyAudience"), AUDIENCES, base["privacy"]["storyAudience"]),
            "liveAudience": _one_of(privacy.get("liveAudience"), AUDIENCES, base["privacy"]["liveAudience"]),
            "allowTagging": _one_of(privacy.get("allowTagging"), AUDIENCES, base["privacy"]["allowTagging"]),
            "allowMentions": _one_of(privacy.get("allowMentions"), AUDIENCES, base["privacy"]["allowMentions"]),
            "allowDirectMessages": _one_of(
                privacy.get("allowDirectMessages"), AUDIENCES, base["privacy"]["allowDirectMessages"]
            ),
            "searchableByEmail": _bool(privacy.get("searchableByEmail"), base["privacy"]["searchableByEmail"]),
            "searchableByPhone": _bool(privacy.get("searchableByPhone"), base["privacy"]["searchableByPhone"]),
        },
        "security": {
            key: _bool(security.get(key), base["security"][key])
            for key in ("twoFactorEnabled", "biometricUnlock", "loginAlerts", "requirePasswordForSensitiveChanges")
        },
        "storage": {
            "autoDownloadPhotos": _one_of(
                storage.get("autoDownloadPhotos"), DOWNLOAD_POLICIES, base["storage"]["autoDownloadPhotos"]
            ),
            "autoDownloadVideos": _one_of(
                storage.get("autoDownloadVideos"), DOWNLOAD_POLICIES, base["storage"]["autoDownloadVideos"]
            ),
            "autoDownloadAudio": _one_of(
                storage.get("autoDownloadAudio"), DOWNLOAD_POLICIES, base["storage"]["autoDownloadAudio"]
            ),
            "mediaQuality": _one_of(storage.get("mediaQuality"), MEDIA_QUALITIES, base["storage"]["mediaQuality"]),
            "cacheLimitMb": int(
                round(_clamp(storage.get("cacheLimitMb"), CACHE_LIMIT_MIN_MB, CACHE_LIMIT_MAX_MB, base["storage"]["cacheLimitMb"]))
            ),
            "autoClearCache": _bool(storage.get("autoClearCache"), base["storage"]["autoClearCache"]),
        },
        "data": {
            key: _bool(data.get(key), base["data"][key])
            for key in ("personalizedAds", "shareAnalytics", "shareCrashReports", "activityStatusSharing")
        },
        "developer": {
            key: _bool(developer.get(key), base["developer"][key])
            for key in ("enabled", "showPerfOverlay", "verboseApiLogging")
        },
    }


def merge_preferences(stored: dict, patch) -> dict:
    """Apply a partial patch group-wise, then normalize the result.

    Only groups named in the patch are touched, and within a group only the keys
    present are overridden — so a client that knows about fewer keys than the
    server (an older build) cannot silently reset the ones it has never heard of
    by round-tripping its own smaller document back.
    """
    merged = {group: dict(values) for group, values in stored.items()}
    raw = patch if isinstance(patch, dict) else {}
    for group in PREFERENCE_GROUPS:
        incoming = raw.get(group)
        if not isinstance(incoming, dict):
            continue
        target = dict(merged.get(group) or {})
        for key, value in incoming.items():
            if key == "categories" and group == "notifications":
                categories = dict(target.get("categories") or {})
                if isinstance(value, dict):
                    for category, channels in value.items():
                        if category in NOTIFICATION_CATEGORIES and isinstance(channels, dict):
                            categories[category] = {**(categories.get(category) or {}), **channels}
                target["categories"] = categories
                continue
            target[key] = value
        merged[group] = target
    return normalize_preferences(merged)


# --------------------------------------------------------------------------
# Preference storage
# --------------------------------------------------------------------------

def _read_setting(cur, user_id: int, key: str):
    cur.execute(
        "SELECT setting_value FROM user_settings WHERE user_id=? AND setting_key=? LIMIT 1",
        (int(user_id), key),
    )
    row = _row(cur.fetchone())
    return row.get("setting_value")


def _write_setting(cur, user_id: int, key: str, value: str) -> None:
    # UPDATE-then-INSERT rather than a dialect-specific upsert: this codebase
    # runs on both SQLite and Postgres and the two spell ON CONFLICT
    # differently enough that the portable form is worth the extra statement.
    cur.execute(
        "UPDATE user_settings SET setting_value=?, updated_at=? WHERE user_id=? AND setting_key=?",
        (value, _now(), int(user_id), key),
    )
    if not cur.rowcount:
        cur.execute(
            "INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at) VALUES (?,?,?,?)",
            (int(user_id), key, value, _now()),
        )


def _read_updated_at(cur, user_id: int):
    cur.execute(
        "SELECT updated_at FROM user_settings WHERE user_id=? AND setting_key=? LIMIT 1",
        (int(user_id), PREFERENCES_KEY),
    )
    return _row(cur.fetchone()).get("updated_at")


def load_preferences(cur, user_id: int) -> tuple[dict, int, str | None]:
    """Stored document, revision, and last-write timestamp. Never raises on bad data."""
    ensure_settings_schema(cur)
    stored = _read_setting(cur, user_id, PREFERENCES_KEY)
    try:
        parsed = json.loads(stored) if stored else {}
    except (TypeError, ValueError):
        # A corrupt document is not a reason to refuse to render Settings. The
        # next write overwrites it with something valid.
        LOGGER.warning("SETTINGS_DOCUMENT_UNPARSEABLE user_id=%s", user_id)
        parsed = {}
    try:
        revision = int(_read_setting(cur, user_id, REVISION_KEY) or 0)
    except (TypeError, ValueError):
        revision = 0
    return normalize_preferences(parsed), max(0, revision), _read_updated_at(cur, user_id)


def save_preferences(cur, user_id: int, preferences: dict, revision: int) -> int:
    next_revision = max(1, int(revision) + 1)
    _write_setting(cur, user_id, PREFERENCES_KEY, json.dumps(preferences, separators=(",", ":"), sort_keys=True))
    _write_setting(cur, user_id, REVISION_KEY, str(next_revision))
    return next_revision


def privacy_snapshot(cur, user_id: int) -> dict:
    """What this user has actually allowed.

    The entry point for the rest of the backend. Any subsystem deciding whether
    to expose last-seen, deliver a DM, show a story, or surface a profile in
    search calls this rather than reading `user_settings` directly, so the
    enforcement point and the storage format stay one thing.
    """
    preferences, _, _ = load_preferences(cur, user_id)
    return preferences["privacy"]


def notification_snapshot(cur, user_id: int) -> dict:
    """What this user has agreed to be notified about, for the fan-out path."""
    preferences, _, _ = load_preferences(cur, user_id)
    return preferences["notifications"]


def _side_effects(cur, user_id: int, preferences: dict) -> None:
    """Project the preferences that other tables also own.

    Two preferences are not only ours: `users.preferred_language` is read by the
    web app and the email templates, and `users.profile_visibility` gates the
    public profile page. Writing them here keeps a change made in the native app
    from being invisible everywhere else — which is the difference between a
    setting that is saved and a setting that is applied.
    """
    try:
        cur.execute(
            "UPDATE users SET preferred_language=?, profile_visibility=? WHERE user_id=?",
            (
                preferences["language"]["appLanguage"],
                preferences["privacy"]["accountVisibility"],
                int(user_id),
            ),
        )
    except Exception:
        # A missing column on an old database must not fail the save; the
        # authoritative copy is the document we just wrote.
        LOGGER.warning("SETTINGS_PROJECTION_SKIPPED user_id=%s", user_id)


def _envelope(preferences: dict, revision: int, updated_at) -> dict:
    return {
        "ok": True,
        "preferences": preferences,
        "revision": revision,
        "updated_at": updated_at,
    }


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------

@settings_blueprint.get(API_PREFIX)
def get_settings():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])

    try:
        preferences, revision, updated_at = _with_db(lambda cur, conn: load_preferences(cur, user_id))
    except Exception as exc:
        LOGGER.exception("SETTINGS_READ_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("Could not load your settings.", 500)
    return _json(_envelope(preferences, revision, updated_at))


@settings_blueprint.patch(API_PREFIX)
def patch_settings():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    payload = request.get_json(silent=True) or {}
    patch = payload.get("preferences")
    if not isinstance(patch, dict) or not patch:
        # A 400 here is deliberate: the client treats 4xx as permanent and will
        # not retry, which is right — an empty patch will never become valid.
        return _error("No preference changes were supplied.", 400)
    unknown = [group for group in patch if group not in PREFERENCE_GROUPS]
    if unknown:
        return _error(f"Unknown preference group: {unknown[0]}.", 400)

    def run(cur, conn):
        stored, revision, _ = load_preferences(cur, user_id)
        merged = merge_preferences(stored, patch)
        next_revision = save_preferences(cur, user_id, merged, revision)
        _side_effects(cur, user_id, merged)
        return merged, next_revision, _read_updated_at(cur, user_id)

    try:
        preferences, revision, updated_at = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_WRITE_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("Could not save your settings.", 500)
    return _json(_envelope(preferences, revision, updated_at))


# --------------------------------------------------------------------------
# Relationship lists
# --------------------------------------------------------------------------

def _target_user_id(payload) -> int:
    try:
        value = int((payload or {}).get("user_id") or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _relationship_rows(cur, sql: str, params) -> list:
    cur.execute(sql, params)
    users = []
    for raw in cur.fetchall() or []:
        row = _row(raw)
        user_id = int(row.get("user_id") or 0)
        if user_id <= 0:
            continue
        username = str(row.get("username") or "").strip()
        users.append(
            {
                "id": user_id,
                "username": username,
                "display_name": str(row.get("display_name") or username or f"User {user_id}").strip(),
                "avatar_url": row.get("avatar_url") or None,
                "created_at": row.get("created_at"),
            }
        )
    return users


BLOCKED_SQL = """
    SELECT u.user_id AS user_id, u.username AS username, u.display_name AS display_name,
           u.avatar_url AS avatar_url, b.created_at AS created_at
    FROM blocked_users b
    JOIN users u ON u.user_id = b.blocked_user_id
    WHERE b.blocker_user_id = ?
    ORDER BY b.created_at DESC, b.id DESC
    LIMIT ?
"""

MUTED_SQL = """
    SELECT u.user_id AS user_id, u.username AS username, u.display_name AS display_name,
           u.avatar_url AS avatar_url, m.created_at AS created_at
    FROM pulse_muted_users m
    JOIN users u ON u.user_id = m.muted_user_id
    WHERE m.muter_user_id = ?
    ORDER BY m.created_at DESC, m.id DESC
    LIMIT ?
"""


def _list_relationship(sql: str, label: str):
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])

    def run(cur, conn):
        ensure_settings_schema(cur)
        return _relationship_rows(cur, sql, (user_id, MAX_RELATIONSHIP_ROWS))

    try:
        users = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_%s_READ_FAILED user_id=%s error=%s", label.upper(), user_id, exc.__class__.__name__)
        # Deliberately an error rather than `{"users": []}`: an empty list is a
        # claim the user will act on, and we cannot make it truthfully here.
        return _error(f"Could not load your {label} list.", 500)
    return _json({"ok": True, "users": users, "count": len(users)})


@settings_blueprint.get(f"{API_PREFIX}/blocked")
def list_blocked():
    return _list_relationship(BLOCKED_SQL, "blocked")


@settings_blueprint.get(f"{API_PREFIX}/muted")
def list_muted():
    return _list_relationship(MUTED_SQL, "muted")


def _mutate_relationship(table: str, owner_column: str, target_column: str, add: bool, label: str):
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    target_id = _target_user_id(request.get_json(silent=True) or {})
    if not target_id:
        return _error("A user is required.", 400)
    if target_id == user_id:
        return _error(f"You cannot {label} yourself.", 400)

    def run(cur, conn):
        ensure_settings_schema(cur)
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_id,))
        if not cur.fetchone():
            return "missing"
        if not add:
            cur.execute(
                f"DELETE FROM {table} WHERE {owner_column}=? AND {target_column}=?",
                (user_id, target_id),
            )
            return "removed"
        cur.execute(
            f"SELECT id FROM {table} WHERE {owner_column}=? AND {target_column}=? LIMIT 1",
            (user_id, target_id),
        )
        if cur.fetchone():
            # Idempotent: blocking someone already blocked is a success, not a
            # conflict. The client fires this from a toggle that may retry.
            return "exists"
        columns = f"{owner_column}, {target_column}, created_at"
        cur.execute(f"INSERT INTO {table} ({columns}) VALUES (?,?,?)", (user_id, target_id, _now()))
        return "added"

    try:
        outcome = _with_db(run)
    except Exception as exc:
        LOGGER.exception(
            "SETTINGS_%s_WRITE_FAILED user_id=%s target=%s error=%s", label.upper(), user_id, target_id, exc.__class__.__name__
        )
        return _error(f"Could not update your {label} list.", 500)
    if outcome == "missing":
        return _error("That account no longer exists.", 404)
    return _json({"ok": True, "user_id": target_id, "state": outcome})


@settings_blueprint.post(f"{API_PREFIX}/blocked")
def add_blocked():
    return _mutate_relationship("blocked_users", "blocker_user_id", "blocked_user_id", True, "block")


@settings_blueprint.delete(f"{API_PREFIX}/blocked")
def remove_blocked():
    return _mutate_relationship("blocked_users", "blocker_user_id", "blocked_user_id", False, "block")


@settings_blueprint.post(f"{API_PREFIX}/muted")
def add_muted():
    return _mutate_relationship("pulse_muted_users", "muter_user_id", "muted_user_id", True, "mute")


@settings_blueprint.delete(f"{API_PREFIX}/muted")
def remove_muted():
    return _mutate_relationship("pulse_muted_users", "muter_user_id", "muted_user_id", False, "mute")


def is_blocked(cur, viewer_id: int, target_id: int) -> bool:
    """Whether either direction of a block exists. For enforcement elsewhere."""
    cur.execute(
        """
        SELECT 1 FROM blocked_users
        WHERE (blocker_user_id=? AND blocked_user_id=?) OR (blocker_user_id=? AND blocked_user_id=?)
        LIMIT 1
        """,
        (int(viewer_id), int(target_id), int(target_id), int(viewer_id)),
    )
    return bool(cur.fetchone())


def is_muted(cur, muter_id: int, target_id: int) -> bool:
    cur.execute(
        "SELECT 1 FROM pulse_muted_users WHERE muter_user_id=? AND muted_user_id=? LIMIT 1",
        (int(muter_id), int(target_id)),
    )
    return bool(cur.fetchone())


# --------------------------------------------------------------------------
# Sessions and devices
# --------------------------------------------------------------------------

def _current_session_hash():
    """Hash of the access token this request arrived with, if any.

    Used only to mark one row `current` so the UI can label it and refuse to
    revoke it silently. A web caller has no bearer token, so nothing is marked
    current — which is correct: the web session is not in this table.
    """
    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        return _bot().mobile_token_hash(token)
    except Exception:
        return None


def _session_row(row: dict, current_hash) -> dict:
    session_id = str(row.get("id") or "")
    platform = str(row.get("platform") or "").strip()
    label = str(row.get("device_label") or "").strip()
    return {
        "id": session_id,
        "device_name": label or (platform.title() if platform else "Unknown device"),
        "platform": platform,
        # Country rather than a precise location: it is what the auth layer
        # already records, and a city-level guess from an IP is often wrong in a
        # way that alarms people about sessions that are actually theirs.
        "location": (row.get("country") or "").strip() or None,
        # `ip_hash` is stored, never the address. Returning the hash would be
        # noise to the user and a fingerprint to anyone who reads the response,
        # so the field is reported as absent rather than filled with something
        # that only looks like an answer.
        "ip_address": None,
        "last_active_at": row.get("last_seen_at") or row.get("created_at"),
        "current": bool(current_hash and row.get("access_token_hash") == current_hash),
    }


@settings_blueprint.get(f"{API_PREFIX}/sessions")
def list_sessions():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    current_hash = _current_session_hash()

    def run(cur, conn):
        _bot().ensure_mobile_security_session_schema(cur)
        cur.execute(
            """
            SELECT id, device_label, platform, country, last_seen_at, created_at, access_token_hash
            FROM mobile_security_sessions
            WHERE user_id=? AND status='active' AND COALESCE(revoked_at,'')=''
            ORDER BY COALESCE(last_seen_at, created_at) DESC
            LIMIT ?
            """,
            (user_id, MAX_SESSION_ROWS),
        )
        return [_session_row(_row(raw), current_hash) for raw in cur.fetchall() or []]

    try:
        sessions = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_SESSIONS_READ_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("Could not load your active sessions.", 500)
    return _json({"ok": True, "sessions": sessions, "count": len(sessions)})


@settings_blueprint.post(f"{API_PREFIX}/sessions/revoke")
def revoke_session():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    payload = request.get_json(silent=True) or {}
    raw_id = str(payload.get("session_id") or "").strip()
    revoke_all = bool(payload.get("all"))
    if not revoke_all and not raw_id:
        return _error("A session is required.", 400)

    def run(cur, conn):
        _bot().ensure_mobile_security_session_schema(cur)
        now = _now()
        if revoke_all:
            current_hash = _current_session_hash()
            # "Sign out everywhere else" keeps the caller signed in. Signing the
            # caller out too would be a different, more surprising action, and
            # the client has an explicit sign-out for that.
            if current_hash:
                cur.execute(
                    """
                    UPDATE mobile_security_sessions
                    SET status='revoked', revoked_at=?, revoked_reason='user_revoked_all'
                    WHERE user_id=? AND status IN ('active','rotated') AND COALESCE(access_token_hash,'') <> ?
                    """,
                    (now, user_id, current_hash),
                )
            else:
                cur.execute(
                    """
                    UPDATE mobile_security_sessions
                    SET status='revoked', revoked_at=?, revoked_reason='user_revoked_all'
                    WHERE user_id=? AND status IN ('active','rotated')
                    """,
                    (now, user_id),
                )
            return {"revoked": max(0, int(cur.rowcount or 0))}

        # Scoped by user_id as well as id: without it, knowing a session id from
        # any account would be enough to sign that account out.
        cur.execute(
            """
            UPDATE mobile_security_sessions
            SET status='revoked', revoked_at=?, revoked_reason='user_revoked'
            WHERE id=? AND user_id=? AND status IN ('active','rotated')
            """,
            (now, raw_id, user_id),
        )
        return {"revoked": max(0, int(cur.rowcount or 0))}

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_SESSION_REVOKE_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("Could not sign that device out.", 500)
    if not revoke_all and not result["revoked"]:
        # Already gone, or never this user's. Both are 404 rather than 500: the
        # request was well-formed, the target simply is not there to revoke.
        return _error("That session is no longer active.", 404)
    result["ok"] = True
    result["message"] = "Signed out." if not revoke_all else f"Signed {result['revoked']} other device(s) out."
    return _json(result)


# --------------------------------------------------------------------------
# Data export and account deletion
# --------------------------------------------------------------------------

def _open_request(cur, user_id: int, request_type: str):
    cur.execute(
        """
        SELECT id, reference, status, requested_at, scheduled_for
        FROM pulse_account_data_requests
        WHERE user_id=? AND request_type=? AND status='pending'
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), request_type),
    )
    return _row(cur.fetchone())


@settings_blueprint.post(f"{API_PREFIX}/data-export")
def request_data_export():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    source = str((request.get_json(silent=True) or {}).get("source") or "native_settings")[:60]

    def run(cur, conn):
        ensure_settings_schema(cur)
        existing = _open_request(cur, user_id, "export")
        if existing:
            # Re-requesting while one is in flight returns the in-flight one
            # rather than queueing a second copy of the same archive.
            return existing, False
        reference = f"exp_{secrets.token_hex(8)}"
        cur.execute(
            """
            INSERT INTO pulse_account_data_requests (user_id, request_type, status, reference, source, requested_at)
            VALUES (?,?,?,?,?,?)
            """,
            (user_id, "export", "pending", reference, source, _now()),
        )
        return {"reference": reference, "requested_at": _now()}, True

    try:
        record, created = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_EXPORT_REQUEST_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("We couldn't request your export. Please try again.", 500)

    email = str(user.get("email") or "").strip()
    destination = f" to {email}" if email else " to the address on your account"
    message = (
        f"Export requested. We'll email a download link{destination} when it's ready."
        if created
        else f"An export is already being prepared. We'll email a download link{destination} when it's ready."
    )
    return _json({"ok": True, "message": message, "reference": record.get("reference"), "status": "pending"})


@settings_blueprint.post(f"{API_PREFIX}/delete-account")
def request_account_deletion():
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    payload = request.get_json(silent=True) or {}
    # The native screen makes the user type this exactly; verifying it server-side
    # too means a mis-wired client cannot schedule a deletion by accident.
    if str(payload.get("confirmation") or "").strip() != "DELETE":
        return _error("Type DELETE to confirm.", 400)
    source = str(payload.get("source") or "native_settings")[:60]

    def run(cur, conn):
        ensure_settings_schema(cur)
        existing = _open_request(cur, user_id, "deletion")
        if existing:
            return existing, False
        scheduled_for = (datetime.now(timezone.utc) + timedelta(days=DELETION_GRACE_DAYS)).isoformat(timespec="seconds")
        reference = f"del_{secrets.token_hex(8)}"
        cur.execute(
            """
            INSERT INTO pulse_account_data_requests
                (user_id, request_type, status, reference, source, requested_at, scheduled_for)
            VALUES (?,?,?,?,?,?,?)
            """,
            (user_id, "deletion", "pending", reference, source, _now(), scheduled_for),
        )
        return {"reference": reference, "scheduled_for": scheduled_for}, True

    try:
        record, created = _with_db(run)
    except Exception as exc:
        LOGGER.exception("SETTINGS_DELETION_REQUEST_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _error("We couldn't submit your deletion request. Nothing has been deleted.", 500)

    scheduled = str(record.get("scheduled_for") or "")[:10]
    message = (
        f"Your account is scheduled for deletion on {scheduled}. "
        "Sign out to finish — signing back in before then will cancel it."
        if created
        else f"Your account is already scheduled for deletion on {scheduled}. Signing back in before then will cancel it."
    )
    return _json(
        {
            "ok": True,
            "message": message,
            "reference": record.get("reference"),
            "scheduled_for": record.get("scheduled_for"),
            "status": "pending",
        }
    )


def cancel_pending_deletion(cur, user_id: int) -> int:
    """Cancel a scheduled deletion. Called from the sign-in path.

    Without this the grace period is a promise the product does not keep: the
    native screen tells the user that signing back in cancels the request, and
    the only place that can be true is wherever a successful login is recorded.
    """
    try:
        ensure_settings_schema(cur)
        cur.execute(
            """
            UPDATE pulse_account_data_requests
            SET status='cancelled', cancelled_at=?
            WHERE user_id=? AND request_type='deletion' AND status='pending'
            """,
            (_now(), int(user_id)),
        )
        return max(0, int(cur.rowcount or 0))
    except Exception:
        LOGGER.warning("SETTINGS_DELETION_CANCEL_FAILED user_id=%s", user_id)
        return 0


def register(app) -> None:
    app.register_blueprint(settings_blueprint)
