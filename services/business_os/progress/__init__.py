"""Progress OS — the Founding Member Challenge and what comes after it.

A referral program that pays only for real members. A signup is not a
referral; a person who signs up, fills in a real profile, posts on two
separate days and stays in good standing is. The distinction is the whole
product: it is what separates a community from a farm of empty accounts.

Module map
----------
``campaign``      Versioned campaign config. Targets, intervals, reward
                  amounts and milestones live here, not in code branches, so
                  a v2 can change the rules without rewriting the engine or
                  retroactively rescoring people who finished under v1.
``schema``        Additive tables. The UNIQUE constraints are the security
                  model — see the module docstring.
``qualification`` The state machine from attribution to QUALIFIED, including
                  the two-day posting evidence.
``milestones``    Milestone awards and the repeatable $30 reward cycles.
                  Records what was *earned*; the canonical rewards engine
                  owns what gets *paid*.
``missions``      Retention journeys for after the challenge, and for the
                  referred person's own beginning.
``progress_api``  Framework-agnostic controllers returning ``(status, body)``.

Design commitments
------------------
*Canonical-first.* No second ledger, no second attribution system, no second
achievement store. Attribution stays in ``referral_conversions``; cash is
granted through ``services.business_os.rewards``; Live eligibility flows
through the existing privilege engine rather than a parallel boolean.

*Server authority.* The client never computes a qualified count, a reward, a
milestone or Live eligibility. Every one of those is derived server-side from
records the client cannot write.

*Privacy by construction.* Progress is private to the profile owner. No
handler accepts a target-user parameter, and referrals are addressed by an
HMAC token that binds the referrer — a token simply does not resolve for
anybody else.

*Signals are not guilt.* Shared IPs, families, roommates, offices, schools
and CGNAT are normal. Risk signals can route a qualification to human review;
they can never award, and they never silently disqualify on their own.
"""

from .schema import ensure_schema  # noqa: F401
from .campaign import get as get_campaign  # noqa: F401
