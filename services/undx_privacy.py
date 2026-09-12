"""UNDX — how sensitive a request is, and how sensitive a provider may receive.

One ladder, not three. Before this module the repository held three overlapping
vocabularies for the same idea:

* ``services/private_office/model.py`` — PUBLIC .. RESTRICTED, rank-ordered,
  real code, already carrying a *ceiling* semantic for fact retrieval.
* ``docs/UNDX_PROVIDER_DATA_POLICY.md`` — SYNTHETIC, PLATFORM_PUBLIC,
  PLATFORM_PRIVATE, ACCOUNT, PRIVATE_OFFICE, SECRET. Prose only; no code read it.
* the provider matrix in that same document, enforced by nothing.

Three names for one question is how a control ends up applied to the vocabulary
the author happened to remember. So the five shared rungs are **imported** from
the Private Office, which is their canonical owner under
``PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md`` §14 ("feature code may read through the
owner's public API"). This module is a consumer of that ladder, not a second
definition of it. If the Office adds a rung, this module inherits it, and
``test_the_shared_rungs_are_the_private_office_ones`` fails if the two ever drift.

Two rungs exist here that the Office does not model, because the Office
classifies *facts it stores* and this module classifies *text leaving the
building*:

* ``SYNTHETIC`` sits below PUBLIC. Health-check probes and benchmark fixtures
  contain no real content at all. It is not a privacy level so much as the
  absence of one, and it is the rung that makes "this provider may receive
  nothing but test traffic" expressible.
* ``SECRET`` sits above RESTRICTED. Credentials and service keys. No provider
  receives it, ever, and no ceiling can be configured to admit it.

## Fail closed

§2 of the brief requires rejection, not deprioritisation, and the two places
this module can be wrong are not symmetrical. Under-classifying sensitive text
sends it somewhere it should not go and nobody finds out. Over-classifying
refuses a request and someone complains within the hour. So every ambiguity
resolves upward:

* an unrecognised class name ranks as ``SECRET`` — rejected everywhere. Not
  ``PUBLIC``, which is what a ``dict.get(name, 0)`` would quietly do.
* a provider with no declared ceiling accepts ``SYNTHETIC`` only, so a provider
  added later is useless until someone classifies it deliberately.
* ``None`` means the caller did not say, which is different from an unknown
  name, and resolves to ``DEFAULT_REQUEST_PRIVACY`` below.

## Why the ceiling depends on the model, not just the provider

"Provider privacy ceiling" is the obvious framing and it is not quite right
here. Meta serves two tiers from one credential and one base URL, and
``META_MUSE_MODEL`` chooses between them:

* ``muse-spark-1.3`` — Meta's console states prompts and completions are not
  used to train Meta models.
* ``muse-spark-1.3-contributor`` — the console states inputs and outputs *are*
  used to train and improve Meta's AI models.

A ceiling keyed on ``"meta"`` alone would be a control that one environment
variable silently invalidates: flip the variable and the same ceiling now blesses
sending user content into a training corpus, while every log line still says
"meta". So ``provider_ceiling()`` takes the resolved model and applies a
per-model override where the vendor's own terms differ by model. The router
passes what it is actually about to send.
"""

from __future__ import annotations

import os

# The five shared rungs come from their owner. Importing the module costs ~1.5ms
# and pulls in nothing but constants - `private_office/model.py` imports only
# `__future__`, and the package `__init__` deliberately does not import
# submodules eagerly.
from services.private_office.model import (
    SENSITIVITIES as _OFFICE_SENSITIVITIES,
    SENSITIVITY_PUBLIC,
    SENSITIVITY_INTERNAL,
    SENSITIVITY_CONFIDENTIAL,
    SENSITIVITY_HIGHLY_SENSITIVE,
    SENSITIVITY_RESTRICTED,
)

#: No real content: connectivity probes, benchmark fixtures, golden-corpus
#: prompts. Below PUBLIC because published content is still *content*.
PRIVACY_SYNTHETIC = "SYNTHETIC"

#: Credentials, tokens, internal service keys. Above every configurable ceiling.
PRIVACY_SECRET = "SECRET"

#: Low to high. The middle five are the Private Office ladder, in its order.
PRIVACY_CLASSES: tuple[str, ...] = (
    PRIVACY_SYNTHETIC,
    *_OFFICE_SENSITIVITIES,
    PRIVACY_SECRET,
)

PRIVACY_RANK: dict[str, int] = {name: index for index, name in enumerate(PRIVACY_CLASSES)}

#: The rank an unrecognised name gets. Deliberately the top of the ladder: a
#: typo in a class name must refuse the request, not silently declassify it.
UNKNOWN_CLASS_RANK = PRIVACY_RANK[PRIVACY_SECRET]

#: Other vocabularies mapped onto the ladder, so a caller using the name from
#: the data-policy document or from the mission brief lands on the right rung
#: instead of falling through to the unknown-class rejection.
#:
#: These are aliases, not rungs. `PRIVACY_CLASSES` stays seven long, because a
#: ladder with two names for one height is a ladder nobody can reason about.
PRIVACY_ALIASES: dict[str, str] = {
    # docs/UNDX_PROVIDER_DATA_POLICY.md
    "PLATFORM_PUBLIC": SENSITIVITY_PUBLIC,
    "PLATFORM_PRIVATE": SENSITIVITY_CONFIDENTIAL,
    "ACCOUNT": SENSITIVITY_HIGHLY_SENSITIVE,
    "PRIVATE_OFFICE": SENSITIVITY_RESTRICTED,
    # Mission brief wording. USER_PRIVATE and BUSINESS_PRIVATE are the same
    # height: both are "content the author did not publish". The distinction
    # between them is about *whose* data it is, which is an isolation question
    # owned elsewhere, not a disclosure-damage question owned here.
    "USER_PRIVATE": SENSITIVITY_CONFIDENTIAL,
    "BUSINESS_PRIVATE": SENSITIVITY_CONFIDENTIAL,
}


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def normalise(privacy_class: str | None) -> str:
    """Resolve a caller's class name to a rung, or to the caller-said-nothing default.

    ``None`` and ``""`` mean "the caller did not classify this", which is a
    different failure from "the caller classified it as something I do not
    recognise". The first is the normal case for existing call sites and gets
    `DEFAULT_REQUEST_PRIVACY`; the second is a bug and is left alone so that
    `rank()` can reject it.
    """
    if privacy_class is None or not str(privacy_class).strip():
        return default_request_privacy()
    name = str(privacy_class).strip().upper()
    return PRIVACY_ALIASES.get(name, name)


def rank(privacy_class: str | None) -> int:
    """Height on the ladder. An unrecognised name ranks at the top, not the bottom."""
    return PRIVACY_RANK.get(normalise(privacy_class), UNKNOWN_CLASS_RANK)


def is_known(privacy_class: str | None) -> bool:
    return normalise(privacy_class) in PRIVACY_RANK


#: What a request is assumed to carry when its caller does not say.
#:
#: CONFIDENTIAL, not PUBLIC and not INTERNAL. The router cannot see what it is
#: forwarding: `undx_capability_planner.plan_agentic_intent` sends the user's own
#: message verbatim to be classified, and a user's message to their assistant is
#: private by default. Assuming the less sensitive of two plausible readings is
#: precisely the failure this module exists to close, so the default is the more
#: sensitive one and callers that genuinely hold public or synthetic text say so.
#:
#: It costs less than it looks: INTERNAL and CONFIDENTIAL admit the same set of
#: providers under the ceilings below, so this choice is conservative without
#: being the thing that narrows routing.
_DEFAULT_REQUEST_PRIVACY = SENSITIVITY_CONFIDENTIAL


def default_request_privacy() -> str:
    """The assumed class for an unclassified request.

    Overridable by `UNDX_DEFAULT_REQUEST_PRIVACY`, but only *upward*. Letting an
    environment variable lower the assumed sensitivity of unclassified traffic
    would make this whole module one `railway variables --set` away from off,
    which is the property the brief calls fail-closed.
    """
    configured = _env("UNDX_DEFAULT_REQUEST_PRIVACY", _DEFAULT_REQUEST_PRIVACY).upper()
    configured = PRIVACY_ALIASES.get(configured, configured)
    if configured not in PRIVACY_RANK:
        return _DEFAULT_REQUEST_PRIVACY
    if PRIVACY_RANK[configured] < PRIVACY_RANK[_DEFAULT_REQUEST_PRIVACY]:
        return _DEFAULT_REQUEST_PRIVACY
    return configured


#: The most sensitive class each provider may receive.
#:
#: Every entry traces to `docs/UNDX_PROVIDER_DATA_POLICY.md`, which records what
#: was actually established about each vendor rather than what is plausible. The
#: split is not a quality ranking:
#:
#:   CONFIDENTIAL  the vendor's API terms state our data is not trained on, read
#:                 from the vendor's own console or documentation.
#:   PUBLIC        that has not been independently established. "Not verified"
#:                 is not an accusation, but it is not a basis for sending
#:                 somebody's unpublished content either.
#:
#: Nothing here reaches HIGHLY_SENSITIVE or RESTRICTED. PRIVATE_OFFICE data sits
#: behind a second lock precisely because the first lock was judged insufficient
#: for it, and "we read the vendor's terms and they seemed fine" is the first
#: lock applied twice. Admitting a provider to those rungs is a deliberate
#: decision with the retention question actually answered, and the router is not
#: where that decision gets made.
PROVIDER_CEILINGS: dict[str, str] = {
    "openai": SENSITIVITY_CONFIDENTIAL,
    "claude": SENSITIVITY_CONFIDENTIAL,
    "meta": SENSITIVITY_CONFIDENTIAL,
    # Perplexity resolves a query by searching the live web at request time, so
    # the prompt becomes a search engine query. That caps it at PUBLIC on
    # mechanism, independent of any retention policy it might publish: a DM
    # summarised into a Perplexity prompt has been typed into a search box.
    "perplexity": SENSITIVITY_PUBLIC,
    "gemini": SENSITIVITY_PUBLIC,
    "deepseek": SENSITIVITY_PUBLIC,
    "groq": SENSITIVITY_PUBLIC,
}

#: Ceilings that belong to a *model*, overriding its provider's entry.
#:
#: Meta's Contributor tier is the case this exists for. Its console states
#: plainly that inputs and outputs are used to train and improve Meta's models,
#: which is a different vendor relationship from the Standard tier reached
#: through the same credential and the same base URL. Keyed on the model ID the
#: router is about to send, so flipping `META_MUSE_MODEL` moves the ceiling with
#: it rather than leaving a stale blessing behind.
MODEL_CEILINGS: dict[str, str] = {
    "muse-spark-1.3-contributor": PRIVACY_SYNTHETIC,
}

#: A provider nobody has classified. SYNTHETIC, so that adding a provider to
#: `undx_router.PROVIDERS` without adding it here makes it reachable by health
#: checks and by nothing else. The alternative - defaulting to a usable ceiling -
#: means the next provider arrives pre-approved for user content.
UNDECLARED_PROVIDER_CEILING = PRIVACY_SYNTHETIC


def provider_ceiling(provider: str, model: str | None = None) -> str:
    """The most sensitive class this provider may receive, for this model.

    `model` is what the router is about to put on the wire, not what is
    configured as a default, so an override that changes tiers changes the
    ceiling in the same breath.
    """
    if model:
        override = MODEL_CEILINGS.get(str(model).strip().lower())
        if override:
            return override
    return PROVIDER_CEILINGS.get(str(provider).strip().lower(), UNDECLARED_PROVIDER_CEILING)


def provider_accepts(provider: str, privacy_class: str | None, model: str | None = None) -> bool:
    """May this provider receive text of this class?

    The whole control in one comparison. SECRET fails here for every provider
    because no ceiling in `PROVIDER_CEILINGS` or `MODEL_CEILINGS` is SECRET and
    none may be - see `test_no_ceiling_can_admit_secret`.
    """
    return rank(privacy_class) <= rank(provider_ceiling(provider, model))


def refusal_reason(provider: str, privacy_class: str | None, model: str | None = None) -> str:
    """Why a provider was refused, in the words an operator needs.

    Names both heights. "privacy_refused" alone sends the reader to this module
    to work out which of the two sides was the surprise, and the answer is
    usually that the request was classified higher than the caller expected.
    """
    return (
        f"privacy_ceiling: {normalise(privacy_class)} exceeds "
        f"{provider_ceiling(provider, model)} for {provider}"
    )
