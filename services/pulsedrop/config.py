"""Every PulseDrop tunable, in one place, resolved at call time.

## Why nothing here is a module constant

Railway hands a container its environment at boot and never again, so an
``os.getenv`` read at import time is frozen until the next deploy. A kill switch
with that property is not a kill switch — by the time the redeploy lands the
thing you were trying to stop has published. So each setting is a function, and
each function consults the ``pulsedrop_settings`` table before falling back to
the environment. Flipping a row stops the next tick; no deploy, no restart.

The environment still sets the defaults, because a fresh database must behave
sensibly before anyone has written a settings row, and because
``.env.example`` is the contract the protection suite checks. The table is an
override layer, not a replacement.

## Three switches, not one

``enabled()`` is the master. ``signals_enabled()`` and ``reels_enabled()`` gate
the two surfaces independently, because they fail differently: a bad Signal is a
bad sentence, a bad Reel is a bad sentence rendered into a video file that cost
CPU and now sits in object storage. Being able to stop Reels while Signals keep
running is the difference between pausing a feature and pausing the account.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

#: How long a settings row is trusted before it is re-read. Short enough that an
#: operator flipping the kill switch sees it take effect within one tick, long
#: enough that a 5-second worker loop is not querying settings 12 times a minute.
_CACHE_SECONDS = 20.0

_lock = threading.Lock()
_cache: dict[str, str] = {}
_cache_at = 0.0


def _load_overrides() -> dict[str, str]:
    """Settings rows, memoised briefly. Never raises: a database that cannot
    answer means "no overrides", which degrades to the environment defaults
    rather than to an unconfigured PulseDrop."""
    global _cache_at
    now = time.monotonic()
    with _lock:
        if _cache_at and now - _cache_at < _CACHE_SECONDS:
            return dict(_cache)
    rows: dict[str, str] = {}
    try:
        from services import db as db_service

        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT setting_key, setting_value FROM pulsedrop_settings")
            for row in cur.fetchall() or []:
                item = dict(row)
                key = str(item.get("setting_key") or "").strip()
                if key:
                    rows[key] = str(item.get("setting_value") or "")
        finally:
            conn.close()
    except Exception:
        # A missing table is the normal state before the first tick calls
        # ensure_schema(); anything else is a database problem that the caller
        # will hit again on its own query. Either way PulseDrop runs on defaults.
        log.debug("pulsedrop_settings_unavailable", exc_info=True)
        return dict(_cache)
    with _lock:
        _cache.clear()
        _cache.update(rows)
        _cache_at = now
    return dict(rows)


def invalidate_cache() -> None:
    """Drop the memoised settings so the next read hits the table.

    Called after a write so an operator who flips a switch and reloads the page
    is not told it is still set the old way for the next 20 seconds.
    """
    global _cache_at
    with _lock:
        _cache_at = 0.0


def _raw(key: str, default: str) -> str:
    override = _load_overrides().get(key)
    if override not in (None, ""):
        return str(override)
    return os.getenv(key, default)


def _flag(key: str, default: str) -> bool:
    return str(_raw(key, default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: str, *, low: int, high: int) -> int:
    try:
        value = int(float(str(_raw(key, default)).strip()))
    except (TypeError, ValueError):
        value = int(default)
    return max(low, min(value, high))


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

FLAG = "flag"
INT = "int"


class Setting(NamedTuple):
    """One tunable: how to read it, what it defaults to, and its safe range.

    The accessors below resolve through this table rather than repeating a
    default and a pair of bounds in their own bodies. That was not worth doing
    while the environment was the only way to set anything -- a typo in a
    ``.env`` is an operator's problem and the clamp catches it. It became worth
    doing the moment an admin form could write these rows: a form that validated
    against one copy of the bounds while the accessor clamped against another
    would accept 500, store 500, display 500, and run 200, with nothing
    anywhere reporting a disagreement.
    """

    kind: str
    default: str
    low: int = 0
    high: int = 0
    label: str = ""
    group: str = ""
    help: str = ""


#: Declaration order is display order on the ops page; ``group`` is the heading.
SETTINGS: dict[str, Setting] = {
    "PULSEDROP_ENABLED": Setting(
        FLAG, "false", label="PulseDrop enabled", group="Kill switches",
        help="Master switch. Off means the tick returns without reading the catalog.",
    ),
    "PULSEDROP_SIGNALS_ENABLED": Setting(
        FLAG, "true", label="Publish Signals", group="Kill switches",
        help="Independent of Reels, so one surface can be paused without the account.",
    ),
    "PULSEDROP_REELS_ENABLED": Setting(
        FLAG, "true", label="Publish Reels", group="Kill switches",
        help="Also stops rendering. A bad Reel costs CPU and an object in storage.",
    ),
    "PULSEDROP_EVALUATION_INTERVAL_SECONDS": Setting(
        INT, "7200", 60, 86_400, "Evaluation interval (s)", "Cadence",
        "How often the curator considers publishing. Evaluating is not publishing.",
    ),
    "PULSEDROP_MIN_PUBLISH_INTERVAL_SECONDS": Setting(
        INT, "5400", 0, 86_400, "Minimum gap between posts (s)", "Cadence",
        "Floor between two publications of any kind, so a backlog is not a burst.",
    ),
    "PULSEDROP_REEL_MIN_INTERVAL_SECONDS": Setting(
        INT, "21600", 0, 604_800, "Minimum gap between Reels (s)", "Cadence",
        "Reels are the expensive, most intrusive format, so they get a longer "
        "floor than the one above rather than sharing it.",
    ),
    "PULSEDROP_DAILY_PUBLICATION_CAP": Setting(
        INT, "8", 0, 200, "Publications per day", "Cadence",
        "A ceiling, not a target. Zero stops publishing while still evaluating, "
        "so the run log keeps answering whether the pipeline works.",
    ),
    "PULSEDROP_DAILY_REEL_CAP": Setting(
        INT, "3", 0, 100, "Reels per day", "Cadence",
        "Counted inside the cap above, not in addition to it.",
    ),
    "PULSEDROP_PRODUCT_COOLDOWN_HOURS": Setting(
        INT, "336", 0, 8_760, "Product cooldown (h)", "Fairness",
        "How long before the same listing may be published again. Two weeks, "
        "because a feed that recycles one product is the thing readers notice.",
    ),
    "PULSEDROP_SELLER_COOLDOWN_HOURS": Setting(
        INT, "24", 0, 8_760, "Seller cooldown (h)", "Fairness",
        "Stops one merchant holding the account for a day. Shorter than the "
        "share cap below is deliberate: this bounds bursts, that bounds totals.",
    ),
    "PULSEDROP_CATEGORY_COOLDOWN_HOURS": Setting(
        INT, "12", 0, 8_760, "Category cooldown (h)", "Fairness",
        "Variety across the day. Relaxed rather than removed when the catalog "
        "is too thin to satisfy it — an empty feed is not more diverse.",
    ),
    "PULSEDROP_REEL_PRODUCT_COOLDOWN_HOURS": Setting(
        INT, "720", 0, 8_760, "Reel product cooldown (h)", "Fairness",
        "Longer than the Signal cooldown, because a repeated Reel costs a "
        "second render of footage the viewer has already scrolled past.",
    ),
    "PULSEDROP_REEL_SELLER_COOLDOWN_HOURS": Setting(
        INT, "72", 0, 8_760, "Reel seller cooldown (h)", "Fairness",
        "The Reel surface is small enough that one merchant appearing twice in "
        "a week reads as promotion rather than discovery.",
    ),
    "PULSEDROP_CROSS_FORMAT_COOLDOWN_HOURS": Setting(
        INT, "48", 0, 8_760, "Signal before Reel (h)", "Fairness",
        "Zero permits both formats in one tick, which is the outcome the "
        "distribution engine exists to avoid making automatic.",
    ),
    "PULSEDROP_PAIR_EVERY_POST": Setting(
        FLAG, "false", label="Pair every Signal with a Reel", group="Fairness",
        help="Off, a Reel is earned: seller-shot footage on a top-scoring "
             "product. On, any product worth publishing at all goes to both "
             "surfaces, composed from stills when there is no video. A volume "
             "decision, not a quality one -- it puts two posts in a tick "
             "without widening the catalog, so read the daily cap as covering "
             "half as many products. The cooldowns still apply: this decides "
             "what a publishable product earns, not who is publishable.",
    ),
    "PULSEDROP_MAX_SELLER_SHARE_PERCENT": Setting(
        INT, "40", 1, 100, "Max one seller's share (%)", "Fairness",
        "Only bites once the catalog can support it; today it is one seller.",
    ),
    "PULSEDROP_SELLER_SHARE_WINDOW": Setting(
        INT, "10", 2, 500, "Publications in the share window", "Fairness",
        "How many recent publications the share cap above is measured over. A "
        "short window makes the cap strict; a long one makes it forgiving.",
    ),
    "PULSEDROP_CANDIDATE_LIMIT": Setting(
        INT, "200", 1, 2_000, "Candidates per tick", "Execution",
        "How much catalog one tick reads before ranking. Raising it costs query "
        "time on every tick and only matters once the catalog is larger.",
    ),
    "PULSEDROP_LEASE_SECONDS": Setting(
        INT, "300", 30, 3_600, "Curator lease (s)", "Execution",
        "Must exceed the worst-case tick, or a stolen lease means two publishers.",
    ),
    "PULSEDROP_REEL_TARGET_SECONDS": Setting(
        INT, "8", 3, 30, "Composed Reel length (s)", "Reel composition",
        "Length of a Reel built from product images. Long enough for the price "
        "reveal to land, short enough not to outstay a product shot.",
    ),
    "PULSEDROP_REEL_MAX_SOURCE_SECONDS": Setting(
        INT, "90", 5, 600, "Longest seller video reused (s)", "Reel composition",
        "Beyond this the composer falls back to images rather than truncating "
        "someone else's footage at an arbitrary point.",
    ),
    "PULSEDROP_REEL_RENDER_MAX_ATTEMPTS": Setting(
        INT, "3", 1, 10, "Render attempts", "Reel composition",
        "A render that has used them all is parked as failed rather than "
        "retried forever; the run log counts it and the listing moves on.",
    ),
    "PULSEDROP_REEL_RENDER_TIMEOUT_SECONDS": Setting(
        INT, "180", 30, 900, "Render timeout (s)", "Reel composition",
        "Also how soon the curator comes back to a tick that is waiting on an "
        "encode, so raising it delays the publication as well as the give-up.",
    ),
    "PULSEDROP_REEL_MIN_IMAGES": Setting(
        INT, "1", 1, 10, "Fewest images to compose", "Reel composition",
        "Below this the listing is not composed at all. One still image plus a "
        "slow push-in is a legitimate Reel; zero is a blank screen.",
    ),
    "PULSEDROP_REEL_MAX_IMAGES": Setting(
        INT, "4", 1, 10, "Most images to compose", "Reel composition",
        "Past this the cuts come faster than the eye reads them and the result "
        "looks like a slideshow, which is the thing the composer avoids.",
    ),
    "PULSEDROP_REEL_AUDIO_ENABLED": Setting(
        FLAG, "false", label="Music under Reels", group="Reel composition",
        help="Off unless a track has been cleared below. Music is attached at "
             "playback, never encoded in, so this switch also silences Reels "
             "already published.",
    ),
    "PULSEDROP_REEL_AUDIO_VOLUME_PERCENT": Setting(
        INT, "60", 0, 100, "Music volume (%)", "Reel composition",
        "A bed, not a soundtrack. The video carries no audio of its own, so "
        "this is the whole level a member hears.",
    ),
}


def resolve(key: str):
    """The running value of one setting, by key. Raises on an unknown key."""
    spec = SETTINGS[key]
    if spec.kind == FLAG:
        return _flag(key, spec.default)
    return _int(key, spec.default, low=spec.low, high=spec.high)




# ---------------------------------------------------------------------------
# Kill switches
# ---------------------------------------------------------------------------


def enabled() -> bool:
    """Master switch. False means the tick returns without reading the catalog."""
    return resolve("PULSEDROP_ENABLED")


def signals_enabled() -> bool:
    return resolve("PULSEDROP_SIGNALS_ENABLED")


def reels_enabled() -> bool:
    return resolve("PULSEDROP_REELS_ENABLED")


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


def evaluation_interval_seconds() -> int:
    """How often the curator wakes up and considers publishing.

    Evaluation is not publication. A tick that finds nothing worth posting is a
    successful tick, and the interval floor exists so a misconfigured value
    cannot turn the worker's 5-second loop into a catalog scan every 5 seconds.
    """
    return resolve("PULSEDROP_EVALUATION_INTERVAL_SECONDS")


def min_publish_interval_seconds() -> int:
    """Floor between two PulseDrop publications of any kind.

    Separate from the evaluation interval on purpose: a backlog of newly
    approved listings must not become a burst just because several ticks in a
    row each found a worthy candidate.
    """
    return resolve("PULSEDROP_MIN_PUBLISH_INTERVAL_SECONDS")


def reel_min_interval_seconds() -> int:
    return resolve("PULSEDROP_REEL_MIN_INTERVAL_SECONDS")


def daily_publication_cap() -> int:
    return resolve("PULSEDROP_DAILY_PUBLICATION_CAP")


def daily_reel_cap() -> int:
    return resolve("PULSEDROP_DAILY_REEL_CAP")


# ---------------------------------------------------------------------------
# Fairness and anti-repetition
# ---------------------------------------------------------------------------


def product_cooldown_hours() -> int:
    return resolve("PULSEDROP_PRODUCT_COOLDOWN_HOURS")


def seller_cooldown_hours() -> int:
    return resolve("PULSEDROP_SELLER_COOLDOWN_HOURS")


def category_cooldown_hours() -> int:
    return resolve("PULSEDROP_CATEGORY_COOLDOWN_HOURS")


def reel_product_cooldown_hours() -> int:
    return resolve("PULSEDROP_REEL_PRODUCT_COOLDOWN_HOURS")


def reel_seller_cooldown_hours() -> int:
    return resolve("PULSEDROP_REEL_SELLER_COOLDOWN_HOURS")


def cross_format_cooldown_hours() -> int:
    """How long after a product's Signal before the same product may get a Reel.

    Zero is meaningful and is not the default: it permits SIGNAL_AND_REEL in one
    tick, which is the "mechanically both" outcome the distribution engine
    exists to avoid making automatic.
    """
    return resolve("PULSEDROP_CROSS_FORMAT_COOLDOWN_HOURS")


def pair_every_post() -> bool:
    """Whether a publishable product should get both formats rather than one.

    This is the operator overriding the editorial default described in
    ``distribution``: normally the Reel is the expensive format and has to be
    earned, so ``_earns_both`` wants seller-shot footage on a top-scoring
    product. Turning this on says the account wants reach more than it wants
    that restraint.

    It widens what a *publishable* product earns and nothing else. Every
    cooldown, cap and pacing floor is evaluated exactly as before, so a Reel
    that fairness would have blocked stays blocked and the tick publishes the
    Signal alone -- with a reason naming the blocker, because "pairing is on
    and this post has no Reel" is a question the run log should be able to
    answer.
    """
    return resolve("PULSEDROP_PAIR_EVERY_POST")


def candidate_limit() -> int:
    """Rows pulled from the catalog per tick, before ranking.

    A ceiling rather than a target. The eligible catalog is small today (tens of
    listings); this stops the query from becoming a table scan if it is not.
    """
    return resolve("PULSEDROP_CANDIDATE_LIMIT")


def max_per_seller_share_percent() -> int:
    """Ceiling on one seller's share of recent publications.

    Deliberately not enforced as equality. When the eligible catalog is a single
    seller — which is production's actual state — an equality rule would publish
    nothing forever, so this only bites once the catalog can support it. See
    ``diversity.seller_share_blocked``.
    """
    return resolve("PULSEDROP_MAX_SELLER_SHARE_PERCENT")


def seller_share_window() -> int:
    """Recent publications considered when measuring a seller's share."""
    return resolve("PULSEDROP_SELLER_SHARE_WINDOW")


# ---------------------------------------------------------------------------
# Distributed execution
# ---------------------------------------------------------------------------


def lease_seconds() -> int:
    """How long one instance owns the curator before another may take over.

    Must exceed the worst-case tick: a tick that outlives its lease can publish
    concurrently with the instance that stole it. Rendering does not run under
    this lease for exactly that reason — it is a queued job with its own claim.
    """
    return resolve("PULSEDROP_LEASE_SECONDS")


# ---------------------------------------------------------------------------
# Reel composition
# ---------------------------------------------------------------------------


def reel_target_seconds() -> int:
    return resolve("PULSEDROP_REEL_TARGET_SECONDS")


def reel_max_source_seconds() -> int:
    """Longest seller video PulseDrop will republish as a Reel.

    A seller's 4-minute unboxing is a legitimate product video and a bad Reel.
    Beyond this the composer falls back to the image path rather than truncating
    someone else's footage at an arbitrary point.
    """
    return resolve("PULSEDROP_REEL_MAX_SOURCE_SECONDS")


def reel_render_max_attempts() -> int:
    return resolve("PULSEDROP_REEL_RENDER_MAX_ATTEMPTS")


def reel_render_timeout_seconds() -> int:
    return resolve("PULSEDROP_REEL_RENDER_TIMEOUT_SECONDS")


def reel_min_images() -> int:
    """Fewest product images the composition path will accept.

    One image is enough: a single slow push-in reads as deliberate. The setting
    exists so the bar can be raised without a code change if it turns out that
    one-image Reels read as filler.
    """
    return resolve("PULSEDROP_REEL_MIN_IMAGES")


def reel_max_images() -> int:
    return resolve("PULSEDROP_REEL_MAX_IMAGES")


def reel_audio_enabled() -> bool:
    """Whether a cleared bed may be attached to a Reel.

    Independent of ``reels_enabled`` rather than folded into it, because the two
    answer different questions and fail differently: turning Reels off stops
    publication, while turning this off silences music on Reels already
    published — the track is resolved per request, so withdrawing consent here
    reaches the whole back catalogue on the next scroll.
    """
    return resolve("PULSEDROP_REEL_AUDIO_ENABLED")


def reel_audio_volume() -> float:
    """Bed level, 0.0–1.0. Stored as a percentage so the form stays integers."""
    return max(0.0, min(resolve("PULSEDROP_REEL_AUDIO_VOLUME_PERCENT") / 100.0, 1.0))


def snapshot() -> dict[str, Any]:
    """Every resolved setting, for the ops surface and worker heartbeats."""
    return {
        "enabled": enabled(),
        "signals_enabled": signals_enabled(),
        "reels_enabled": reels_enabled(),
        "evaluation_interval_seconds": evaluation_interval_seconds(),
        "min_publish_interval_seconds": min_publish_interval_seconds(),
        "reel_min_interval_seconds": reel_min_interval_seconds(),
        "daily_publication_cap": daily_publication_cap(),
        "daily_reel_cap": daily_reel_cap(),
        "product_cooldown_hours": product_cooldown_hours(),
        "seller_cooldown_hours": seller_cooldown_hours(),
        "category_cooldown_hours": category_cooldown_hours(),
        "reel_product_cooldown_hours": reel_product_cooldown_hours(),
        "reel_seller_cooldown_hours": reel_seller_cooldown_hours(),
        "cross_format_cooldown_hours": cross_format_cooldown_hours(),
        "pair_every_post": pair_every_post(),
        "candidate_limit": candidate_limit(),
        "max_seller_share_percent": max_per_seller_share_percent(),
        "seller_share_window": seller_share_window(),
        "lease_seconds": lease_seconds(),
        "reel_target_seconds": reel_target_seconds(),
        "reel_max_source_seconds": reel_max_source_seconds(),
        "reel_render_max_attempts": reel_render_max_attempts(),
        "reel_render_timeout_seconds": reel_render_timeout_seconds(),
        "reel_min_images": reel_min_images(),
        "reel_max_images": reel_max_images(),
        "reel_audio_enabled": reel_audio_enabled(),
        "reel_audio_volume": reel_audio_volume(),
    }


# ---------------------------------------------------------------------------
# The write path
# ---------------------------------------------------------------------------


def coerce(key: str, value: Any) -> str:
    """The string that will actually be stored for ``value``, or raise.

    Coercion happens *before* the write rather than being left to the accessor's
    clamp, so that the row and the running value can never disagree. An operator
    who types 5 into a field whose floor is 60 is told the setting is 60 and the
    table says 60; the alternative is a row reading 5, a page reading 5 back, and
    a curator quietly running at 60 forever.

    :raises KeyError: the key is not a PulseDrop setting. The admin form posts
        arbitrary strings, and the settings table is read by a background worker,
        so an unknown key is refused rather than stored as inert junk that looks
        like configuration to the next person reading the table.
    """
    spec = SETTINGS[key]
    text = str("" if value is None else value).strip()
    if spec.kind == FLAG:
        return "true" if text.lower() in {"1", "true", "yes", "on"} else "false"
    try:
        number = int(float(text))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} takes a number, not {text!r}") from exc
    return str(max(spec.low, min(number, spec.high)))


def overrides() -> dict[str, str]:
    """The rows currently set, uncached. For the ops page only.

    Uncached because the page's job is to show what is *stored*, and a 20-second
    memo would show an operator their own write as not having happened.
    """
    invalidate_cache()
    return {key: value for key, value in _load_overrides().items() if key in SETTINGS}


def set_override(key: str, value: Any, *, updated_by: int = 0) -> str:
    """Store one setting. Returns the stored string. Raises on a bad key."""
    from services import db as db_service

    from . import schema

    stored = coerce(key, value)
    schema.ensure_schema()
    conn = db_service.connect()
    try:
        conn.execute(
            """
            INSERT INTO pulsedrop_settings (setting_key, setting_value, updated_by, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (setting_key) DO UPDATE
                SET setting_value=EXCLUDED.setting_value,
                    updated_by=EXCLUDED.updated_by,
                    updated_at=EXCLUDED.updated_at
            """,
            (key, stored, int(updated_by or 0), _stamp()),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # So the operator's next page load reflects the write rather than the memo.
    invalidate_cache()
    log.info("PULSEDROP_SETTING_WRITTEN key=%s value=%s by=%s", key, stored, updated_by)
    return stored


def clear_override(key: str, *, updated_by: int = 0) -> None:
    """Delete one setting row, returning that setting to its environment value.

    Distinct from writing the default: an operator who clears a row is saying
    "whatever the deployment says", and a deploy that later changes the
    environment should move it. Writing the default instead would pin it.
    """
    from services import db as db_service

    if key not in SETTINGS:
        raise KeyError(key)
    conn = db_service.connect()
    try:
        conn.execute("DELETE FROM pulsedrop_settings WHERE setting_key=?", (key,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    invalidate_cache()
    log.info("PULSEDROP_SETTING_CLEARED key=%s by=%s", key, updated_by)


def _stamp() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")
