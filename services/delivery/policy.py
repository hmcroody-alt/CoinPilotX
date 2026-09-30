"""What PulseSoc has *declared* about its own side of a delivery promise.

The gap this fills
------------------
``estimate.arrival_window`` needs four things nobody in this package produces: a
handling allowance, a buffer, an optional dispatch cutoff, and a ``now`` that is
timezone-aware *in the origin warehouse's zone*. It refuses rather than invent
any of them, quoting the repo's own CJ research — "preserve unknown processing
time", "show an estimate only when required components exist" — and
``reports/cj-discovery/CJ_DROPSHIPPING_FORENSIC_REPORT.md`` is explicit that CJ's
catalogue ``dispatch``/``deliveryCycle`` fields "are not a universal processing
SLA". So the supplier cannot supply them. Somebody has to declare them, and this
is where.

Why the absence of a default *is* the rollout gate
--------------------------------------------------
:func:`handling` and :func:`buffer_days` return ``None`` until an operator sets a
variable, and ``None`` composes to ``handling_time_undeclared`` /
``buffer_policy_undeclared`` — a refusal, not a window. That means the engine
ships **inert**: every code path runs, every cache key is exercised, every
supplier call is made and measured, and no buyer is shown a date until someone
decides what PulseSoc is willing to promise. That is §101-103's shadow mode
achieved by the shape of the contract rather than by a flag, and it is a stronger
guarantee than a flag, because there is no value of the flag that produces a
made-up number.

The distinction being drawn is ``suppliers.pricing``'s, applied to time. A
default *margin* is a policy a platform may choose; a default *freight cost* is a
claim about what a supplier charges. A handling **allowance** is likewise a
policy choice — but it has to be chosen, and an unset environment variable is
nobody choosing.

Why this module parses its own ranges
-------------------------------------
``suppliers.normalize.transit_days`` already parses ``"2-5"`` into a typed range
and is deliberately not reused. It raises a lower bound of 0 to 1, which is
correct for freight — a parcel cannot cross a border in zero days — and wrong
here: ``estimate._advance`` documents that "zero handling means the item ships
the same day it is ordered", and a warehouse that picks same-day is a real and
declarable policy. Borrowing that parser would silently add a day to every
promise on the platform, in the direction that produces a buyer who was told
Thursday.

The reverse reuse is also refused: nothing here is offered back to the provider
boundary, because a 0 lower bound *there* would be the fabrication that parser
exists to prevent.

Why the timezone is a fact and the rest are switches
----------------------------------------------------
``suppliers.policy`` draws the line and this module follows it: what varies per
deployment is a switch, and what is true about the world is a constant. The
handling allowance, the buffer, the cutoff hour and the declared closures are
commitments this deployment makes, so they are variables. Which timezone a
warehouse country sits in is not, so :data:`ORIGIN_ZONES` is a constant.

Two things are deliberately *not* configurable. ``unspecified_basis`` stays
``estimate.BASIS_BUSINESS``: it is the rounding-direction decision that module
already made and defended, and a switch would exist only to let someone pick the
unsafe direction. And there is no "default origin country" — see below.

The unmapped origin rounds the date later, on purpose
-----------------------------------------------------
``now`` decides what "today" is for dispatch, and getting the zone wrong moves
the whole window by a day. The direction matters more than the magnitude: a
warehouse in Shenzhen at 02:00 on the 2nd is 18:00 on the 1st in UTC, so quoting
in UTC starts handling a day *early* and closes the window a day early — the
direction this domain must never round.

So an origin with no entry in :data:`ORIGIN_ZONES` is quoted in the most
advanced zone the map declares, not in UTC. Its local date is by construction no
earlier than any declared zone's, so an unknown warehouse can only ever be
quoted *later* than the truth. This is derived from the map at call time rather
than written down, so adding a warehouse with a larger offset moves the fallback
with it, and DST is handled because the offsets are read from the zones
themselves and not from a table of numbers.

``zoneinfo`` needs system tzdata, no ``tzdata`` package is installed, and two
live modules in this repo already fall back to UTC when a zone will not load.
UTC is the unsafe direction here, so this module falls back to a fixed
:data:`_FALLBACK_OFFSET_HOURS` instead — which is exact rather than approximate
for the origin that dominates this supplier, because China observes no daylight
saving. :func:`declared` reports it, so a container with no tzdata is visible as
a deployment defect rather than as a fleet of estimates that are quietly a day
optimistic.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from . import estimate, routing

#: The handling allowance, as ``min-max`` or a single number of business days.
#: Unset means no estimate is produced at all; see the module docstring.
ENV_HANDLING = "PULSE_DELIVERY_HANDLING_DAYS"

#: Whole days added to the far end of every window. ``0`` is a decision and is
#: accepted; unset is nobody having decided, and yields no estimate.
ENV_BUFFER = "PULSE_DELIVERY_BUFFER_DAYS"

#: Local hour at the origin after which an order starts handling the next day.
#: Unset models no cutoff, which is the *optimistic* reading, and is why it is
#: reported by :func:`declared`.
ENV_CUTOFF = "PULSE_DELIVERY_DISPATCH_CUTOFF_HOUR"

#: ISO dates the origin is closed. Unset models none — which is not the claim
#: that there are none. See :func:`closures`.
ENV_CLOSURES = "PULSE_DELIVERY_ORIGIN_CLOSURES"

#: Transit days past which a route is not offered at all.
ENV_CEILING = "PULSE_DELIVERY_TRANSIT_CEILING_DAYS"

#: Which route a destination's options resolve to. One of ``routing.POLICIES``.
ENV_ROUTE_POLICY = "PULSE_DELIVERY_ROUTE_POLICY"

#: Where CJ's warehouse countries actually are. A fact about geography, so a
#: constant rather than a switch — and IANA names rather than offsets, so daylight
#: saving is the library's problem and not a table that goes stale twice a year.
#:
#: China is one official zone despite its width, which is why ``CN`` is a single
#: entry and not a judgement call. ``US`` is not: it spans five, and CJ's US
#: warehouses are on the west coast, so the west-coast zone is the one that makes
#: "today" latest and is therefore the safe reading of an ambiguous country.
ORIGIN_ZONES = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "US": "America/Los_Angeles",
    "DE": "Europe/Berlin",
    "GB": "Europe/London",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "CZ": "Europe/Prague",
    "TH": "Asia/Bangkok",
    "ID": "Asia/Jakarta",
    "AU": "Australia/Sydney",
    "CA": "America/Vancouver",
    "JP": "Asia/Tokyo",
    "AE": "Asia/Dubai",
}

#: Used only when no zone in :data:`ORIGIN_ZONES` can be loaded at all, i.e. when
#: the container has no tzdata.
#:
#: It is the most advanced offset any declared origin can reach, and that is the
#: whole specification — not "the offset of the origin we ship from most". The
#: tempting value here is UTC+8, because it is exact year-round for China (which
#: observes no daylight saving) and China is where most of this supplier's stock
#: sits. It is the wrong value: ``Australia/Sydney`` is in the map at UTC+10, and
#: UTC+11 in southern summer, so a UTC+8 fallback would place an AU origin's
#: "today" up to three hours *earlier* than it really is — the one direction this
#: module must never round. Being three hours ahead of China instead costs at most
#: a dispatch date one day late, which is the safe error.
#:
#: ``test_the_fixed_fallback_offset_is_at_least_every_declared_zones`` holds this
#: against the map, so declaring a zone further east fails rather than silently
#: making the fallback unsafe again.
_FALLBACK_OFFSET_HOURS = 11

#: A handling allowance longer than this is a configuration mistake, not a
#: policy. Deliberately far looser than any real allowance: the point is to catch
#: a variable holding a year or a phone number, not to second-guess an operator
#: who has a slow warehouse.
MAX_HANDLING_DAYS = 60

#: Same reasoning for the buffer. A buffer this long is not padding, it is a
#: different promise.
MAX_BUFFER_DAYS = 30

#: ``min-max`` or a bare number. Unsigned on both sides, because the separator is
#: a range separator and never a minus sign — the same trap ``normalize`` records
#: for transit strings, where reusing a signed money pattern read ``"7-20"`` as 7
#: followed by negative 20.
#:
#: ``re.ASCII`` matters: without it ``\d`` is Unicode-aware and matches digits from
#: every script, so ``"٢"`` parses and ``int()`` then returns a perfectly correct
#: 2. The value would not be wrong — the situation would be. A deployment variable
#: holding non-ASCII digits is a paste from a localized spreadsheet or a mangled
#: encoding, not a decision, and reading it anyway means the one chance to tell the
#: operator their configuration is not what they think it is goes past in silence.
_RANGE = re.compile(r"^(\d{1,3})(?:\s*(?:-|–|—|to)\s*(\d{1,3}))?$",
                    re.IGNORECASE | re.ASCII)

#: A closure date's shape. Checked separately from parsing it — see ``closures``.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)


class PolicyInvalid(Exception):
    """A declared value that cannot be read as what it claims to be.

    Raised rather than ignored. An unset variable and an unreadable one are
    different operator situations: the first is "we have not decided yet" and
    composes to a refusal on its own, while the second is a decision that was
    made and then typed wrong. Treating a typo as absence would show no estimate
    to a deployment that believes it configured one, and the variable would sit
    there looking correct.
    """


def handling() -> Optional[Dict[str, Any]]:
    """The declared handling allowance, in the shape ``arrival_window`` wants.

    ``None`` when undeclared, which composes to ``handling_time_undeclared``.

    The basis is stated as business days rather than left unspecified, because
    that is what it is: ``estimate.arrival_window`` advances handling in business
    days regardless of what this says, so stating anything else would produce a
    record whose ``components`` disagreed with its own arithmetic.
    """
    raw = _raw(ENV_HANDLING)
    if raw is None:
        return None
    low, high = _range(raw, ENV_HANDLING, ceiling=MAX_HANDLING_DAYS)
    return {"min_days": low, "max_days": high, "basis": estimate.BASIS_BUSINESS}


def buffer_days() -> Optional[int]:
    """Whole days of padding on the far end, or ``None`` when undeclared.

    ``0`` is returned as ``0`` and not folded into ``None``. A deployment that has
    decided to add no padding has decided something, and the difference between
    that and having decided nothing is the difference between an estimate and a
    refusal.
    """
    raw = _raw(ENV_BUFFER)
    if raw is None:
        return None
    low, high = _range(raw, ENV_BUFFER, ceiling=MAX_BUFFER_DAYS)
    if low != high:
        raise PolicyInvalid(f"{ENV_BUFFER} is a single number of days, not a range")
    return low


def dispatch_cutoff_hour() -> Optional[int]:
    """The local hour at the origin after which handling starts tomorrow.

    ``None`` models no cutoff, and that is the optimistic reading rather than the
    neutral one: an order placed at 23:50 is then assumed to start handling the
    same day. It is still the default, because a cutoff nobody declared is a
    warehouse schedule this platform does not know — but :func:`declared` reports
    its absence so it is a visible gap and not an invisible day.
    """
    raw = _raw(ENV_CUTOFF)
    if raw is None:
        return None
    try:
        hour = int(raw)
    except ValueError:
        raise PolicyInvalid(f"{ENV_CUTOFF} must be an hour of the day, got {raw!r}")
    if not 0 <= hour <= 23:
        raise PolicyInvalid(f"{ENV_CUTOFF} must be 0..23, got {hour}")
    return hour


def closures() -> Tuple[str, ...]:
    """Declared dates the origin does not pick, ready for ``holidays=``.

    Empty is the default and is *not* the claim that the warehouse never closes.
    ``estimate._holiday_set`` names the closure that matters on this supplier:
    CJ ships from China, and Chinese New Year moves real delivery dates by weeks.
    Nothing here invents those dates, because a fabricated calendar is the same
    defect as a fabricated duration, and the count travels in the estimate's
    ``components`` so an accuracy review can see which windows were computed
    blind.

    The lever an operator has meanwhile is the declared buffer and allowance,
    both of which are variables. That is the intended response to a known
    closure, and it is a decision with a name on it.
    """
    raw = _raw(ENV_CLOSURES)
    if raw is None:
        return ()
    found = []
    for part in raw.replace(";", ",").split(","):
        candidate = part.strip()
        if not candidate:
            continue
        # Two checks, and both are load-bearing. The shape check is here because
        # neither available parser enforces it: `strptime("%Y-%m-%d")` accepts an
        # unpadded `2026-2-17`, and `date.fromisoformat` accepts a bare `20260217`
        # on 3.11+ but not on every version this has to run under. Either one would
        # let a string through that `estimate._holiday_set` then rejects — one layer
        # down, after the supplier call, as an EstimateRejected attributed to the
        # estimator rather than to the variable that is actually wrong.
        #
        # The parse is `date.fromisoformat` specifically, because that is the
        # function `_holiday_set` uses. Anything this accepts, the consumer accepts.
        # It is also the only one of the two that rejects `2026-02-30`, which is
        # shaped exactly like a date and is not one.
        if not _ISO_DATE.match(candidate):
            raise PolicyInvalid(
                f"{ENV_CLOSURES} holds {candidate!r}, which is not an ISO date "
                "(YYYY-MM-DD, zero-padded)")
        try:
            date.fromisoformat(candidate)
        except ValueError:
            raise PolicyInvalid(
                f"{ENV_CLOSURES} holds {candidate!r}, which is not a real date")
        found.append(candidate)
    # Sorted and de-duplicated so the same declaration always produces the same
    # tuple. This value reaches `components.holidays_modelled`, and a count that
    # moved with the order someone typed would make two identical deployments
    # look different to an accuracy review.
    return tuple(sorted(set(found)))


def ceiling_days() -> Optional[int]:
    """Transit days past which a route is not offered. ``None`` means no ceiling.

    Unlike handling and buffer, absence here is genuinely neutral: no ceiling
    means every route the supplier offers is eligible, which is the supplier's own
    answer rather than a substituted one. ``routing`` records the value either
    way, so "no ceiling" is auditable as a decision rather than inferred from a
    missing field.
    """
    raw = _raw(ENV_CEILING)
    if raw is None:
        return None
    try:
        days = int(raw)
    except ValueError:
        raise PolicyInvalid(f"{ENV_CEILING} must be a whole number of days, got {raw!r}")
    if days < 1:
        raise PolicyInvalid(f"{ENV_CEILING} must be at least 1 day, got {days}")
    return days


def route_policy() -> str:
    """Which route wins when a destination offers several.

    Defaults to ``routing``'s own default rather than to a string repeated here,
    so there is one answer to "what does this platform pick" and not two that can
    drift apart.
    """
    raw = _raw(ENV_ROUTE_POLICY)
    if raw is None:
        return routing.POLICY_CHEAPEST_ACCEPTABLE
    named = raw.strip().upper()
    if named not in routing.POLICIES:
        raise PolicyInvalid(
            f"{ENV_ROUTE_POLICY} must be one of {', '.join(routing.POLICIES)}, "
            f"got {raw!r}")
    return named


def unspecified_basis() -> str:
    """How to read a supplier that gave a number and no day basis.

    A constant, not a switch. ``estimate`` chose business days because it is the
    later of the two readings and this domain rounds later; a variable here would
    exist only so that a deployment could choose the earlier one.
    """
    return estimate.BASIS_BUSINESS


def now_at(origin: Any, *, clock=None) -> datetime:
    """The current moment in the origin warehouse's zone.

    ``arrival_window`` requires ``now`` to be timezone-aware and documents that
    the zone is the origin's, "because it is the warehouse that has to pick the
    item". An unmapped or missing origin is quoted in the most advanced declared
    zone rather than in UTC — see the module docstring for why that direction is
    the safe one.

    ``clock`` supplies the absolute instant and defaults to now in UTC. It exists
    so a test can pin the instant without also pinning the zone arithmetic, which
    is the part worth testing.
    """
    instant = clock() if clock is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise PolicyInvalid("clock must return a timezone-aware moment")
    return instant.astimezone(zone_for(origin))


def zone_for(origin: Any) -> Any:
    """The tzinfo for a warehouse country, never ``None`` and never UTC.

    Falls through twice, and both fall-throughs move the local date later rather
    than earlier: an unrecognised country to the most advanced declared zone, and
    a zone database that will not load to a fixed offset.
    """
    code = origin.strip().upper() if isinstance(origin, str) else ""
    named = ORIGIN_ZONES.get(code)
    if named is not None:
        loaded = _zone(named)
        if loaded is not None:
            return loaded
    return _most_advanced_zone()


def declared() -> Dict[str, Any]:
    """What is configured, for a health surface — never for a buyer.

    The same distinction ``destination.edge_configured`` draws. "No estimate
    because nobody declared a handling allowance" and "no estimate because the
    supplier could not price this route" are both silence to a buyer and
    completely different work for an operator, and conflating them is how a
    deployment concludes its supplier is down.

    Reports rather than raises when a value is unreadable, because a health
    surface that 500s when the thing it monitors is misconfigured is the one
    moment it needed to render. ``errors`` names the variables to fix.
    """
    report: Dict[str, Any] = {"errors": []}
    for key, reader in (("handling", handling), ("buffer_days", buffer_days),
                        ("dispatch_cutoff_hour", dispatch_cutoff_hour),
                        ("closures", closures), ("ceiling_days", ceiling_days),
                        ("route_policy", route_policy)):
        try:
            report[key] = reader()
        except PolicyInvalid as exc:
            report[key] = None
            report["errors"].append(str(exc))
    report["timezones_loaded"] = _zone(ORIGIN_ZONES["CN"]) is not None
    # The one derived field: an operator's actual question is not "which of six
    # variables is set" but "can this deployment show a date at all", and that is
    # exactly the pair `arrival_window` refuses on.
    report["can_estimate"] = (report["handling"] is not None
                              and report["buffer_days"] is not None
                              and not report["errors"])
    return report


def _raw(name: str) -> Optional[str]:
    """The variable, or ``None`` when it is unset or blank.

    Blank is absence, not a value. Railway sets every key listed in the service's
    variables whether or not it has content, so an empty string is the ordinary
    shape of "declared in the dashboard and never filled in" — and treating it as
    a value would make ``PULSE_DELIVERY_BUFFER_DAYS=`` an unreadable-policy error
    on a deployment that simply has not decided yet.
    """
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip() or None


def _range(raw: str, name: str, *, ceiling: int) -> Tuple[int, int]:
    """``"2-4"`` or ``"3"`` into a pair of whole days, zero allowed.

    Zero is the whole reason this is not ``normalize.transit_days``; see the
    module docstring. Reversed bounds are sorted rather than refused: ``"4-2"`` is
    an unambiguous typo whose meaning nobody could mistake, and refusing it would
    turn a transposition into a deployment with no estimates.
    """
    match = _RANGE.match(raw)
    if match is None:
        raise PolicyInvalid(
            f"{name} must be a number of days or a `min-max` range, got {raw!r}")
    low = int(match.group(1))
    high = low if match.group(2) is None else int(match.group(2))
    if low > high:
        low, high = high, low
    if high > ceiling:
        raise PolicyInvalid(f"{name} is {high} days, past the {ceiling}-day "
                            f"sanity ceiling for this setting")
    return low, high


def _zone(name: str) -> Any:
    """A tzinfo for an IANA name, or ``None`` when the database cannot supply one.

    Imported lazily and caught broadly. ``zoneinfo`` raises
    ``ZoneInfoNotFoundError`` for a missing database, but the failure mode being
    defended against is a container with no tzdata at all, and the point is to
    reach the fixed-offset fallback rather than to classify the reason.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None


def _most_advanced_zone() -> Any:
    """The declared zone whose local date is furthest ahead, right now.

    Computed from current offsets rather than from a written-down winner, so DST
    is respected and a new warehouse entry is picked up without editing this
    function. Falls back to a fixed offset when no zone loads at all, which is
    the no-tzdata case.
    """
    instant = datetime.now(timezone.utc)
    best, best_offset = None, None
    for name in ORIGIN_ZONES.values():
        loaded = _zone(name)
        if loaded is None:
            continue
        offset = instant.astimezone(loaded).utcoffset()
        if offset is not None and (best_offset is None or offset > best_offset):
            best, best_offset = loaded, offset
    if best is None:
        return timezone(timedelta(hours=_FALLBACK_OFFSET_HOURS))
    return best
