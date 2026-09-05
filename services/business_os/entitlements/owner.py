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
* It does not open the Private Office. It confers MEMBERSHIP up to
  PRIVATE_OFFICE — the answer to "did this member pay for the room" — and the
  Office door has a second, independent lock that asks a different question.
  See :func:`confers` and ``private_office_routes._office_lock_gate``.

Membership floor
----------------
The floor is PRIVATE_OFFICE, the top of the ladder. It was PREMIUM for exactly
one commit, and that was wrong for a reason worth recording: the owner tapped
Private Office and was told "Membership required — renew membership". Being
asked to buy the product back is not a cosmetic defect, it is the guarantee
failing in the one place it is most visible.

Raising the floor to the top rung is the honest expression of the rule. The
alternative considered was a fifth ``OWNER`` tier ranked above PRIVATE_OFFICE;
it was rejected because every consumer that switches on a known tier — the
feature matrix, the client tier unions, the availability map — would have to
learn a rung that grants nothing PRIVATE_OFFICE does not already grant. A floor
at the existing top rung reaches the same access with no new vocabulary, and
``source`` still names the authority truthfully as ``owner_lifetime``, so no
surface mistakes this for a purchased grant.

What the floor does NOT do is enable anything unbuilt. ``feature_matrix``
resolves implementation BEFORE entitlement, so ``human_concierge``,
``private_briefings``, ``private_shield`` and the rest report NOT_IMPLEMENTED to
the owner exactly as they do to everyone else. A tier is not a construction
crew.
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

#: The membership rung owner lifetime guarantees. Named here, in the module that
#: owns the rule, and imported by the resolver — rather than the resolver
#: choosing a rung for the owner — so "how high does owner reach" has one answer
#: and changing it is one edit in the place a reader looks for it.
#:
#: The literal is duplicated from ``private_office.tiers.TIER_PRIVATE_OFFICE``
#: rather than imported, because that module imports this one and a module-level
#: import back would close the ring at startup.
#: ``test_premium_owner_lifetime.test_the_floor_constant_names_a_real_rung``
#: asserts the two are equal, so the duplication cannot drift silently — a
#: rename that missed this line would otherwise drop the owner to FREE, because
#: ``tiers.rank`` fails closed on a name it does not recognise.
FLOOR_TIER = "PRIVATE_OFFICE"

#: The umbrella membership keys at or below the floor — every rung of the ladder
#: that has a key at all (FREE has none; every authenticated user is already
#: there). Everything *inside* a tier is a capability governed by the feature
#: matrix and, for the Office, by the second lock. Granting membership is not the
#: same as opening a door.
MEMBERSHIP_KEYS = frozenset({
    "premium.access",
    "private.access",
    "private_office.access",
})


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

    The four membership rungs (:data:`MEMBERSHIP_KEYS`) plus the capabilities a
    Premium plan confers — and nothing else.

    The boundary is still the load-bearing part, it has just moved. ``explain``
    is called with every entitlement key in the product, so an unscoped
    short-circuit here would answer True for keys that have nothing to do with
    membership at all. What this function now says is precise: the owner holds
    every MEMBERSHIP tier permanently, and holds Premium's capabilities as a
    consequence of holding Premium.

    What it still does not say is that the owner may read anything. Membership
    and access are different questions, and the Private Office is where the
    difference is visible: ``private_office.access`` answers "this member has the
    room", and the Office's second lock — a passcode bound to this session and
    this device, evaluated after and independently of the tier — answers "the
    person holding the phone just proved they are that member". Owner lifetime
    settles the first question forever and has no opinion on the second. An owner
    who has not unlocked gets 423 LOCKED, which is the correct refusal, not the
    403 "renew your membership" that this change exists to eliminate.

    Capability scope is read from the two modules that already define it rather
    than restated here, so a capability added to a Premium plan is conferred
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

    # Membership first, and answered from a local constant that needs no import.
    # This is the half of the rule that is a GUARANTEE, so it must not be able to
    # fail for an environmental reason: an owner asked to renew their membership
    # because a module was mid-reload is the exact defect being fixed, and it
    # would be no less wrong for being intermittent.
    if name in MEMBERSHIP_KEYS:
        return True

    scope: set = set()
    try:
        from services.business_os.entitlements import premium as _premium
        scope.add(_premium.PREMIUM_ACCESS)
        scope.update(_premium.PREMIUM_CAPABILITIES)
    except Exception:  # noqa: BLE001
        _log.exception("premium key scope unavailable")
        # Fail closed on CAPABILITY scope. Membership was already settled above;
        # what is unknown here is whether an arbitrary key is a Premium benefit
        # or something else entirely, and guessing in that state is how a
        # capability gets conferred by accident.
        return False
    try:
        from services.business_os.entitlements import facade as _facade
        scope.update(_facade._LEGACY_READERS.keys())
    except Exception:  # noqa: BLE001
        # The advertised set is still known and still correct, so answer from
        # it rather than dropping the owner to nothing.
        _log.exception("facade key scope unavailable")
    return name in scope
