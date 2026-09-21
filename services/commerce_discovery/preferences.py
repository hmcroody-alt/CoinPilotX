"""What this viewer has consented to, and what they have told us to stop.

Two sources, one answer. :func:`viewer_policy` resolves the stored settings
document *and* the suppression table into a single frozen object that the engine
consults once per request. The engine never reads either source directly — one
resolution point means the consent rules cannot be enforced in one place and
forgotten in another, which is the usual way a "turn this off" switch ends up
governing three of the four surfaces that honour it.

Order of authority, strongest first
-----------------------------------

1. **``marketplaceRecommendations: false``** — the master switch. Organic
   discovery stops on every social surface. Marketplace itself keeps working:
   the user switched off *being recommended to*, not *shopping*.
2. **An unexpired snooze** — same effect, with an end date.
3. **A suppression** — narrower: this product, this seller, or this surface.
4. **``frequency``** — shapes how much, never whether.
5. **``personalizedRecommendations: false``** — shapes *how* it is ranked. The
   engine still serves, but on quality and freshness only, and every card
   reports ``reason="popular"`` because no personal claim is true any more.

Failure direction
-----------------

Every read here fails **closed**: an unreadable settings row, a suppression
query that raises, a preferences document that will not parse — all resolve to
"show nothing". That is the opposite of the fail-safe rule elsewhere in this
package, and the asymmetry is the point. A discovery fault must never break the
feed (so the *engine* degrades to serving zero placements), and it must never
override a user's refusal (so *consent* degrades to the refusal). Both rules
point the same way: when in doubt, no commerce.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config, subject

LOGGER = logging.getLogger(__name__)

#: Multiplier applied to per-surface placement budgets, by user frequency
#: choice. "low" is a genuine reduction rather than a token one, and "more" is
#: capped well below double — a user asking for more commerce has not asked for
#: their feed to become a catalogue.
FREQUENCY_MULTIPLIER = {"low": 0.4, "balanced": 1.0, "more": 1.6}

#: How strongly a soft "see fewer like this" biases ranking, per matching
#: signal. Accumulates: three "see fewer" on one category is a strong hint,
#: one is a nudge.
SEE_FEWER_WEIGHT = 0.34


@dataclass(frozen=True)
class ViewerPolicy:
    """Everything the engine is allowed to know about this viewer's wishes."""

    subject_ref: str
    #: False when discovery is off entirely, for any reason.
    allowed: bool
    #: Why it is off, as a stable code, for admin observability. "" when on.
    blocked_reason: str = ""
    personalized: bool = True
    frequency: str = "balanced"
    suppressed_listings: frozenset = field(default_factory=frozenset)
    suppressed_sellers: frozenset = field(default_factory=frozenset)
    suppressed_surfaces: frozenset = field(default_factory=frozenset)
    #: ``{category_or_seller_token: strength}`` from "see fewer like this".
    soft_signals: dict = field(default_factory=dict)

    def allows_surface(self, surface: str) -> bool:
        return self.allowed and surface not in self.suppressed_surfaces

    def budget_multiplier(self) -> float:
        return FREQUENCY_MULTIPLIER.get(self.frequency, 1.0)

    def hidden_strength(self, listing: dict) -> float:
        """Soft-signal weight against one listing, in ``[0, 1]``.

        Hard suppressions are not represented — those removed the candidate
        before scoring. This is only the "see fewer" bias.
        """
        if not self.soft_signals:
            return 0.0
        total = 0.0
        for key in (
            str(listing.get("category") or "").strip().lower(),
            str(listing.get("subcategory") or "").strip().lower(),
            f"seller:{listing.get('seller_user_id')}",
        ):
            if key and key in self.soft_signals:
                total += float(self.soft_signals[key])
        return min(1.0, total)


#: The policy returned whenever anything at all went wrong. Deny, with a code.
def _denied(ref: str, reason: str) -> ViewerPolicy:
    return ViewerPolicy(subject_ref=ref, allowed=False, blocked_reason=reason)


def _commerce_group(preferences: Any) -> dict:
    """The ``commerce`` group from a settings document, or ``{}``.

    ``{}`` means "this document predates the group", which the caller treats as
    the defaults — not as a refusal. An older app build that has never written
    the group must not read as a user who switched discovery off.
    """
    if not isinstance(preferences, dict):
        return {}
    group = preferences.get("commerce")
    return group if isinstance(group, dict) else {}


def viewer_policy(cur, user_id: Any, *, load_preferences=None) -> ViewerPolicy:
    """Resolve settings + suppressions into one decision object.

    ``load_preferences`` is injected so this module does not import the settings
    route pack at module scope — that pack imports ``bot``, and a ranking unit
    test should not boot the monolith to find out whether a user said no.
    """
    ref = subject.subject_ref(user_id)

    if not config.enabled():
        return _denied(ref, "engine_disabled")

    try:
        if load_preferences is None:
            from services.pulse_settings_routes import load_preferences as _load

            load_preferences = _load
        preferences, _, _ = load_preferences(cur, int(user_id))
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_PREFERENCES_UNREADABLE", exc_info=True)
        return _denied(ref, "preferences_unreadable")

    group = _commerce_group(preferences)
    if group.get("marketplaceRecommendations") is False:
        return _denied(ref, "user_disabled")

    snooze_until = group.get("snoozeUntil") or ""
    if snooze_until and not subject.is_expired(snooze_until):
        return _denied(ref, "snoozed")

    personalized = group.get("personalizedRecommendations", True) is not False
    if not config.personalization_enabled():
        personalized = False

    try:
        listings, sellers, surfaces, soft = _load_suppressions(cur, ref)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_SUPPRESSIONS_UNREADABLE", exc_info=True)
        # Fails closed: a viewer who told us to stop showing a seller must not
        # see that seller again merely because one query failed.
        return _denied(ref, "suppressions_unreadable")

    return ViewerPolicy(
        subject_ref=ref,
        allowed=True,
        personalized=personalized,
        frequency=str(group.get("frequency") or "balanced"),
        suppressed_listings=listings,
        suppressed_sellers=sellers,
        suppressed_surfaces=surfaces,
        soft_signals=soft,
    )


def _load_suppressions(cur, ref: str):
    """Current, unexpired suppressions for one viewer.

    Expired rows are filtered in SQL rather than swept: a snooze that lapsed is
    simply not selected, so there is no background job whose failure would leave
    a user permanently opted out of a feature they asked to pause for a month.
    """
    now = subject.now_iso()
    cur.execute(
        "SELECT scope, ref, source_action FROM commerce_discovery_suppressions "
        "WHERE subject_ref=? AND (expires_at IS NULL OR expires_at>?)",
        (ref, now),
    )
    listings: set[int] = set()
    sellers: set[int] = set()
    surfaces: set[str] = set()
    soft: dict[str, float] = {}

    for row in cur.fetchall() or []:
        record = dict(row) if not isinstance(row, dict) else row
        scope = str(record.get("scope") or "").strip().lower()
        value = str(record.get("ref") or "").strip()
        action = str(record.get("source_action") or "").strip().lower()
        if not value:
            continue
        # "see fewer" is a bias, not a ban, so it is accumulated into the soft
        # signals instead of removing the candidate. Storing it in the same
        # table keeps one place to look for "what has this user told us".
        if action == "see_fewer":
            soft[value.lower()] = min(1.0, soft.get(value.lower(), 0.0) + SEE_FEWER_WEIGHT)
            continue
        if scope == "product":
            try:
                listings.add(int(value))
            except (TypeError, ValueError):
                continue
        elif scope == "seller":
            try:
                sellers.add(int(value))
            except (TypeError, ValueError):
                continue
        elif scope == "surface":
            surfaces.add(value.lower())
        elif scope == "all":
            surfaces.update(("feed", "reels", "messenger"))

    return frozenset(listings), frozenset(sellers), frozenset(surfaces), soft


def record_suppression(
    cur,
    ref: str,
    *,
    scope: str,
    value: Any,
    action: str,
    ttl_seconds: Optional[int] = None,
) -> None:
    """Upsert one suppression rule. Portable UPDATE-then-INSERT.

    ``ttl_seconds=None`` stores a permanent rule. "Hide this seller" is
    permanent by design — an expiring version would resurface a merchant the
    user explicitly rejected, which is the single most trust-damaging thing this
    engine could do, and the user has no way to know it was ever going to.
    """
    now = subject.now_iso()
    expires = subject.expiry_iso(ttl_seconds) if ttl_seconds else None
    cur.execute(
        "UPDATE commerce_discovery_suppressions "
        "SET expires_at=?, source_action=?, updated_at=? "
        "WHERE subject_ref=? AND scope=? AND ref=?",
        (expires, action, now, ref, scope, str(value)),
    )
    if not cur.rowcount:
        cur.execute(
            "INSERT INTO commerce_discovery_suppressions "
            "(suppression_id, subject_ref, scope, ref, source_action, expires_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                subject.new_id("cd_sup"), ref, scope, str(value),
                action, expires, now, now,
            ),
        )
