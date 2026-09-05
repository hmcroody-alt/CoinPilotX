"""Owner lifetime Premium — one definition, consumed by every Premium decider.

The rule
--------
The owner account holds Premium permanently. Not "a subscription that keeps
getting renewed", not "a status word somebody remembered to set" — a standing
entitlement with no period end, which no clock, provider event, or store refund
can take away.

Why this module exists rather than a branch in the resolver
-----------------------------------------------------------
Premium is decided in three places that deliberately do NOT call each other:

* ``premium.resolve``   — membership, and the bridge the tier ladder reads.
* ``facade.explain``    — per-capability gates, which read the *raw* legacy
  reader on purpose so shadow mode compares two independent answers.
* ``private_office.tiers.resolve_tier`` — the ladder the mobile client renders.

Three independent readers is the design, not an accident: collapsing them would
destroy the parity check the entitlement migration depends on. But it means a
rule stated in one of them is a rule the other two do not know. That is exactly
how the owner ended up Premium in the badge engine (``has_active_premium``
already short-circuits on :func:`premium_identity_engine.is_owner`) and Free at
the gates — same account, two answers.

So the rule lives here, in one function with no policy of its own, and the three
deciders each ask it. Adding a fourth decider means adding one call, not
re-deriving who the owner is.

What this module deliberately does NOT do
-----------------------------------------
* It does not decide owner IDENTITY. That is
  ``premium_identity_engine.is_owner`` — an allowlist of immutable user ids read
  from ``PULSESOC_OWNER_USER_IDS``. That helper carries the scar of a real
  incident (it used to match on display name, so any user could rename
  themselves into permanent Premium and every owner bypass), and re-deriving
  owner identity here would reopen exactly that door.
* It does not outrank an account hold. A suspended owner is a suspended
  account; see :func:`applies` for how that is enforced without inventing a new
  revocation.
* It does not confer PRIVATE or PRIVATE_OFFICE. See :func:`confers`.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("business_os.entitlements.owner")

# --- vocabulary -------------------------------------------------------------
#: The reason string. Part of the closed enum in ``premium.REASONS``; safe to
#: log and to ship to a client. It names the ACTUAL basis for access, which is
#: the whole point: reporting owner access as ACTIVE_SUBSCRIPTION would put a
#: renewal date on a membership that has none, and ACTIVE_TRIAL would put a
#: countdown on one that never ends.
REASON_OWNER_LIFETIME = "OWNER_LIFETIME"

#: The membership mode. Travels to the client as ``membership.mode`` and selects
#: the Premium Center layout, so the screen never has to know who the owner is.
MODE_OWNER_LIFETIME = "owner_lifetime"

#: Decision provenance. Kept distinct from ``service._VALID_SOURCES`` — that set
#: describes how a GRANT ROW was created, and owner lifetime writes no row. A
#: standing rule is not a purchase and must not be recorded as one.
SOURCE_OWNER_LIFETIME = "owner_lifetime"


def is_owner_account(user_id: Any) -> bool:
    """Is ``user_id`` the owner, per the one server-owned owner identity?

    Fails CLOSED. Every other failure path in this package fails open, because
    there the risk is stripping Premium from a paying member. Here the asymmetry
    reverses: an owner who loses Premium for the duration of one broken request
    gets it back on the next one, while a bare ``except: return True`` would hand
    permanent, unrevokable Premium to every account on the platform the first
    time an import went wrong. The recoverable failure is the correct one.
    """
    try:
        from services import premium_identity_engine as _pie
        return bool(_pie.is_owner({"user_id": int(user_id)}))
    except Exception:  # noqa: BLE001
        _log.exception("owner identity unavailable for user=%s", user_id)
        return False


def applies(user_id: Any, hold: Any = None) -> bool:
    """Does owner lifetime decide this request?

    ``hold`` is the resolved ``facade.account_hold`` dict, or None when the
    caller has not evaluated one.

    An account hold suppresses owner lifetime. Note what that means precisely:
    the owner rule stops APPLYING, so the caller falls through to its normal,
    entirely unchanged resolution path. It does not mean owner lifetime returns
    a denial of its own.

    That distinction is the difference between honouring the brief and shipping
    a regression. "Owner lifetime must not bypass account holds" is satisfied by
    standing aside. Returning False-as-denial would be a *new* revocation: an
    owner whose legacy row says Premium is Premium today, and a change that
    exists to guarantee them access must not be the thing that takes it away
    while their account is under review.
    """
    if hold is not None and hold.get("on_hold"):
        return False
    return is_owner_account(user_id)


def confers(key: Any) -> bool:
    """Does owner lifetime grant ``key``?

    Exactly the Premium key set — the umbrella ``premium.access`` plus the
    capabilities a Premium plan confers — and nothing else.

    The boundary is the load-bearing part. ``facade.explain`` is called with
    every entitlement key in the product, including ``private.access`` and
    ``private_office.access``. A short-circuit that answered True for any key
    would hand the owner the Private Office silently, through a change whose
    commit message says "premium". Private tiers are granted by a real grant row
    or not at all, and this function is what keeps that true.

    Scope is read from the two modules that already define it rather than
    restated here, so a capability added to a Premium plan is conferred
    automatically and a key that is not a Premium benefit stays out:

    * ``premium.PREMIUM_ACCESS`` + ``premium.PREMIUM_CAPABILITIES`` — the
      umbrella and the *advertised* benefits.
    * ``facade._LEGACY_READERS`` — every key the facade will answer for a member
      purely because they are Premium. This second source is not redundant. It
      carries ``premium.identity.effects`` and
      ``premium.crypto.portfolio_intelligence``, which are deliberately absent
      from the advertised tuple (one is unadvertised, one is an alias), and
      omitting them would deny the owner two capabilities every paying member
      holds — re-creating the split this change exists to close, one layer down.

    Both are imported lazily: ``premium`` imports the facade and the facade
    imports this module, so a module-level import would close that ring at
    startup.
    """
    name = str(key or "")
    if not name:
        return False
    scope: set = set()
    try:
        from services.business_os.entitlements import premium as _premium
        scope.add(_premium.PREMIUM_ACCESS)
        scope.update(_premium.PREMIUM_CAPABILITIES)
    except Exception:  # noqa: BLE001
        _log.exception("premium key scope unavailable")
        # Fail closed on SCOPE. An unknown scope means we cannot tell a Premium
        # capability from the Private Office, and guessing in that state is how
        # a tier boundary gets crossed by accident.
        return False
    try:
        from services.business_os.entitlements import facade as _facade
        scope.update(_facade._LEGACY_READERS.keys())
    except Exception:  # noqa: BLE001
        # The advertised set is still known and still correct, so answer from
        # it rather than dropping the owner to nothing.
        _log.exception("facade key scope unavailable")
    return name in scope
