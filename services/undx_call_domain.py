"""Which subsystem a routed AI call came from, and nothing about what it may do.

`undx_router.classify_request` already infers a category from the text of a message.
That inference is useful and it is also a guess: it reads "wallet" and concludes
security, which is right often enough to order providers by and never right enough
to decide anything. This module carries the other kind of knowledge — the kind the
caller holds for certain. `scam_shield` does not need to infer that it is scam
shield.

The two are deliberately separate. A declared domain is a fact about provenance; an
inferred category is a hypothesis about content. Collapsing them would mean either
trusting a guess as if it were declared, or asking callers to re-declare what the
text already shows.

Naming: this is not `undx_domain_reasoning`, which reads evidence records inside a
feature domain (account standing, music licensing), nor `private_office.DOMAINS`,
which enumerates fact categories. Three vocabularies called "domain" is already one
too many, so this one says `call_domain` everywhere and never abbreviates to
`domain` in a public name.

## The one rule this module exists to keep

Routing may use domain. **Permissions may not.**

A domain is attacker-influenced in exactly one place that matters: `TELEGRAM`
carries text an arbitrary stranger sent to a bot. If a domain could widen what a
call is permitted to do, then the value that says "this came from a stranger" would
be the value that decides how much the stranger is trusted. That is not a rule worth
writing in a comment and hoping, so it is enforced by construction instead:

* This module does not import `undx_privacy`, and cannot name a privacy class.
* It exposes no function returning an allow/deny, a ceiling, or a class.
* The only behavioural output is a *preference* — an ordering hint over provider
  names that the router is free to ignore, and which cannot add a provider the
  privacy ceilings did not already admit.

`tests/test_undx_call_domain.py::PermissionsMayNotUseDomainTest` asserts each of
those three properties against the source rather than against this docstring, on
the same principle as `test_undx_canary.py`'s refusal to let the canary module name
a provider: the cheapest way to keep a vocabulary out of a decision is to keep it
out of the file.

Two of the three are checked against the AST and one — the privacy class names —
against the raw text, which is stricter and costs this file nothing, because the
rule can be explained in the general without ever writing a class name down. The
distinction matters in the other direction for provider names: this file *does*
need to name one, as the anti-example in `_PREFERENCE` below, so that check looks
at string literals only. `scripts/undx_call_domain_mutation_check.py` breaks each
rule in turn and fails if the suite stays green — including one mutation that must
be *allowed*, to prove the checks are not just banning words.
"""

from __future__ import annotations

#: The subsystem that originated a routed call.
#:
#: Flat on purpose. A hierarchy (`MESSAGING.TELEGRAM`) invites the question of
#: whether a parent's preference is inherited, and the honest answer for routing is
#: that TELEGRAM has nothing in common with in-app messaging except the word: one is
#: a stranger's text arriving over a third-party bot API, the other is a member
#: talking to another member.
CALL_DOMAIN_GENERAL = "GENERAL"
CALL_DOMAIN_SECURITY = "SECURITY"
CALL_DOMAIN_MESSAGING = "MESSAGING"
CALL_DOMAIN_SCAM_SHIELD = "SCAM_SHIELD"
CALL_DOMAIN_TELEGRAM = "TELEGRAM"
CALL_DOMAIN_PRIVATE_OFFICE = "PRIVATE_OFFICE"
CALL_DOMAIN_COMMERCE = "COMMERCE"
CALL_DOMAIN_RESEARCH = "RESEARCH"

CALL_DOMAINS: tuple[str, ...] = (
    CALL_DOMAIN_GENERAL,
    CALL_DOMAIN_SECURITY,
    CALL_DOMAIN_MESSAGING,
    CALL_DOMAIN_SCAM_SHIELD,
    CALL_DOMAIN_TELEGRAM,
    CALL_DOMAIN_PRIVATE_OFFICE,
    CALL_DOMAIN_COMMERCE,
    CALL_DOMAIN_RESEARCH,
)

#: Unordered on purpose: there is no ladder here.
#:
#: Privacy classes rank, and that ranking is load-bearing — `rank(x) > rank(ceiling)`
#: is how a request gets refused. Domains do not rank, and giving them an ordering
#: would invite exactly the comparison that the rule above forbids. A set, not a
#: tuple index, is the type that refuses to be compared.
_KNOWN: frozenset[str] = frozenset(CALL_DOMAINS)

#: What a call is assumed to be when its caller does not say.
#:
#: `GENERAL`, and the reasoning runs opposite to `undx_privacy`'s. There, silence
#: defaults to the *more* sensitive reading, because the cost of guessing wrong is
#: disclosure. Here the only consequence of being wrong is a provider ordering, so
#: the default is the one that expresses no preference at all rather than the one
#: that sounds safest. Defaulting an unlabelled call to `SECURITY` would quietly
#: reorder providers for every caller that had not been migrated yet.
DEFAULT_CALL_DOMAIN = CALL_DOMAIN_GENERAL


def normalise(call_domain: str | None) -> str:
    """Resolve a caller's domain name, or the caller-said-nothing default.

    Mirrors `undx_privacy.normalise` in shape so the two read the same at a call
    site: `None` and `""` mean "not declared" and get the default, while a name
    that is merely unrecognised is returned as written so `is_known` can report it.
    Normalising a typo into the default would make the typo invisible, and the
    whole reason `is_known` exists is that invisible configuration mistakes are the
    expensive kind.
    """
    if call_domain is None or not str(call_domain).strip():
        return DEFAULT_CALL_DOMAIN
    return str(call_domain).strip().upper().replace("-", "_").replace(" ", "_")


def is_known(call_domain: str | None) -> bool:
    """Whether the name is one of `CALL_DOMAINS`.

    Exposed for the same reason `undx_privacy.is_known` is: a misspelt domain and a
    deliberate `GENERAL` produce identical routing, and only one of them is a bug.
    Readiness surfaces should publish this rather than let the two look alike.
    """
    return normalise(call_domain) in _KNOWN


#: Provider ordering hints, by domain.
#:
#: Every entry is a *preference*, not a requirement, and the router intersects it
#: with the providers a request's privacy class already admits. So an entry here can
#: reorder a set; it can never enlarge one. That is the property that keeps this
#: table on the routing side of the rule at the top of the file.
#:
#: **Empty, deliberately, and it should stay empty until a benchmark fills it.**
#:
#: The mission's objective is consolidation, not model promotion, and it forbids
#: promoting a provider without benchmark, safety, cost and latency evidence behind
#: it. An ordering hint is a promotion: writing `SCAM_SHIELD: ("openai", ...)` here
#: would route every scam classification to one vendor on the strength of an
#: intuition, and it would do it in a table that looks like configuration rather
#: than like a decision anybody reviewed.
#:
#: So the mechanism exists and carries nothing yet. Two hypotheses are worth
#: measuring in Phase 15-16, recorded here as candidates and explicitly not as
#: entries:
#:
#: 1. RESEARCH may belong with a live-retrieval provider, for the reason
#:    `classify_request` checks `current_web` before every other rule — a stale
#:    answer to a question about now is well-formed, confident, and detectable only
#:    by a reader who already knew.
#: 2. SECURITY and SCAM_SHIELD parse their answers instead of showing them, so they
#:    may do better on providers with a real structured-output mode.
#:
#: Hypothesis 2 is a trap worth naming: the need is a *capability* (structured
#: output), not a *preference*, and capabilities belong on the request where they
#: can be required and verified. Routing scam_shield to a JSON-capable vendor by
#: domain would satisfy the need today and lose it silently the first time the
#: table was reordered. `route_structured_request` should be told what the caller
#: needs; it should not have to infer it from where the call came from.
#:
#: Until then `routing_preference` returns `()` for everything, which means every
#: routing decision is still made by content classification and privacy ceilings —
#: the same two inputs as before this module existed. Nothing routes differently
#: because of a domain, and the cost ledger gains an attribution it did not have.
_PREFERENCE: dict[str, tuple[str, ...]] = {}


def routing_preference(call_domain: str | None) -> tuple[str, ...]:
    """Provider names this domain would rather try first, possibly none.

    An empty tuple is the normal answer and means "no opinion — decide from the
    content", which is what an unrecognised domain also gets. That collapse is
    intentional: a typo should lose its preference, not acquire someone else's, and
    it must not fail the call outright because a routing hint is not a permission
    and a bad hint is not worth an outage.
    """
    return _PREFERENCE.get(normalise(call_domain), ())


def readiness() -> dict[str, object]:
    """What an operator can check without reading this file.

    `declared_preferences` is deliberately a count of domains with an opinion
    rather than the table itself: the table is source, and a readiness surface that
    echoes source tells an operator nothing they could not read faster.
    """
    return {
        "call_domains": list(CALL_DOMAINS),
        "default_call_domain": DEFAULT_CALL_DOMAIN,
        "declared_preferences": len(_PREFERENCE),
    }
