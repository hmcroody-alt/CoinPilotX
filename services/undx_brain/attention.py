"""What a request is about, and — just as load-bearing — what it is not about.

Directive §6 asks for attention and salience routing: given "Why is my account acting
strange?", the system should open account health, sessions, devices, notifications,
recent settings changes and support tickets, and *should not* open Marketplace, music or
crypto unless evidence connects them. That second clause is the whole difficulty. Making
a system look at more things is easy; making it decline to look at things is what stops
an answer about a lost password from arriving with a summary of somebody's Reels.

This module is the front door of :mod:`services.undx_brain.workspace`. The workspace can
refuse a twenty-fifth entry, but something has to decide which six of eighty capabilities
were worth offering it in the first place, and that decision cannot be "all of them".

Five decisions here are load-bearing.

**There is no second catalogue.** Every product area, resource type, capability id and
phrasing this module scores against is read at import from
:mod:`services.undx_knowledge_map`, which in turn reads its operational fields from
:mod:`services.undx_capability_registry`. Attention owns no list of what PulseSoc
contains. A capability added to the registry becomes attendable with no edit here, and —
the part that matters more — a capability *removed* stops being attendable immediately
rather than lingering in a hand-kept copy that nobody remembers to prune.

**A request that matches nothing activates nothing.** There is no default area, no
fallback to the most popular one, no "when in doubt, show the feed". An empty
:class:`Focus` carrying a reason is the honest output, and the caller's correct response
to it is to ask the person what they mean. The convenient alternative — activating
something so the turn has material to work with — is precisely how a question nobody
understood gets answered confidently about the wrong subject.

**Every activation names the evidence that caused it.** §6's rule is conditional: those
areas must not activate *unless evidence connects them*. A conditional that cannot be
audited is a preference, so each activated area carries the :class:`Cue` list that lifted
it — which term, matched against which field of which capability record. "Why did you
look at my devices?" is answerable from the returned value, without a log.

**A symptom alone is a mood, not a direction.** "Strange", "weird", "broken", "hacked" say
that something is wrong; they do not say where. So the :class:`Concern` frames — the
mechanism that gets from "acting strange" to sessions and devices, which no vocabulary
match could reach — fire only when a symptom word appears *together with* an anchor word
naming the thing that is misbehaving. "My account is acting strange" opens the account
investigation. "That marketplace listing looks strange" does not, and Marketplace
activates on its own terms, from the map, as it should. The frames name resource types
that must exist in the map or this module fails at import: a frame contributes *which
question to ask*, never *what exists*.

**Ranking may drop a tail; it may not drop arbitrarily.** The workspace refuses rather
than evicts because the entry it would evict is arbitrary with respect to importance.
Here the opposite holds: salience *is* the ordering, and carrying the fourteenth-best
area would defeat the purpose. So this module does cut — but it reports the cut. Areas
that had a cue and lost are returned in :attr:`Focus.withheld` with
:attr:`Focus.crowded` set, so "it never considered my orders" and "it considered them and
ranked them ninth" are distinguishable.

What this module deliberately does not do:

* It does not read account state. It takes no owner and touches no database, because the
  question "what is this about?" is answerable before knowing whose account it is. If a
  future version needs an owner, that is the signal that it has started doing retrieval's
  job rather than routing's.
* It does not decide permission. An area activating means "look here", never "you may
  act here"; :mod:`services.undx_agent_policy` remains the only thing that decides that.
* It does not choose an action. Narrowing eighty capabilities to six is not selecting one
  of the six.
* It does not interpret the goal. "Find my Bitcoin alert" and "Fix my Bitcoin alert"
  activate the same area and are entirely different requests; telling them apart is goal
  understanding, and it is deliberately not here.
* It does not retrieve. :func:`services.undx_brain.knowledge.retrieve` reads the source
  corpus; this reads the product map. They answer different questions and are kept apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from services import undx_knowledge_map as _map

from . import config as brain_config
from . import workspace as _workspace
from .bounds import Refusal
from .knowledge import MAX_TERMS, STOP_WORDS
from .workspace import Slot, Workspace


__all__ = [
    "Cue", "Area", "Focus", "Concern", "CONCERNS", "RESOURCE_TYPES", "PRODUCT_AREAS",
    "MAX_AREAS", "MAX_CAPABILITIES", "MAX_REQUEST_CHARS", "MAX_TERMS", "STOP_WORDS",
    "attend", "place_into",
]


# ---------------------------------------------------------------------------------
# ceilings
# ---------------------------------------------------------------------------------

#: How many areas one focus may carry. Six is not a token budget — it is the number of
#: distinct subjects a single answer can address before it stops being an answer and
#: becomes a status page. Configuration may lower it; nothing may raise it.
MAX_AREAS = 6

#: How many capabilities one focus may name. Clamped at use time to the workspace's own
#: skill-slot ceiling rather than restated, so the two cannot drift: offering the
#: workspace more capabilities than it will accept would mean the surplus is dropped by
#: whichever one happened to arrive last.
MAX_CAPABILITIES = 6

#: How much of a request is read for routing. A request longer than this is still
#: attended — the excess is simply not tokenised, and the focus says so. Refusing to
#: route a long message would turn a person who explains themselves thoroughly into a
#: person the system cannot help.
MAX_REQUEST_CHARS = 2000

#: A term appearing in more than this fraction of all capability records separates
#: nothing, so it is dropped from the index at import. Without it, "list" and "summary"
#: — which are in a third of the descriptions — quietly become the strongest signal in
#: any request containing them.
_COMMON_TERM_SHARE = 0.25

#: An area scoring below this fraction of the strongest area is withheld. An area an
#: order of magnitude weaker than the best is not weak evidence; it is a coincidence of
#: vocabulary, and presenting it as attention is worse than presenting less. The same
#: reasoning, and deliberately the same shape, as ``knowledge._RELEVANCE_FLOOR``.
_SALIENCE_FLOOR = 0.2


# ---------------------------------------------------------------------------------
# scoring weights
# ---------------------------------------------------------------------------------
#
# The ordering between these *is* the routing policy, which is why they are named
# constants and not literals inside the loop.

#: The map lists, for each capability, the phrasings people actually use for it. A
#: request containing one of them whole is the strongest signal available anywhere here,
#: because it is not an inference — somebody wrote down that this sentence means this
#: capability.
_W_INTENT_PHRASE = 10

#: A term matching a segment of the capability id (``alerts`` in ``crypto.alerts.pause``).
#: Ids are chosen by the people who built the capability and are the most deliberate
#: vocabulary in the map.
_W_CAPABILITY = 6

#: A term matching the resource type (``session``, ``support_ticket``). This is the noun
#: the person is holding, so it beats the area heading above it.
_W_RESOURCE = 5

#: A term matching a word of the product area (``Saved content``). Areas are headings;
#: headings are broad by construction.
_W_AREA = 4

#: A term appearing inside an intent phrase without matching the whole phrase.
_W_INTENT_TERM = 3

#: A term appearing in the description prose. Incidental by default — descriptions are
#: written for humans and mention neighbouring concepts freely.
_W_DESCRIPTION = 1

#: Contributed by a concern frame that fired. Below an exact phrasing and above a bare
#: area word: a symptom plus an anchor is strong evidence about *where to look*, and
#: weaker evidence than somebody naming the thing outright.
_W_CONCERN = 7

#: Added per additional matching capability in the same area, capped by
#: ``_BREADTH_CAP``. A second and third match in one area is corroboration; the tenth is
#: just a large area, and without the cap Reels and Social relationships — the two areas
#: with ten records each — would outrank a precise single-capability hit on volume alone.
_W_BREADTH = 1.0
_BREADTH_CAP = 3


# ---------------------------------------------------------------------------------
# vocabulary from the map
# ---------------------------------------------------------------------------------

#: Every resource type the product map declares. Derived, never typed out: a concern
#: frame naming something absent here is a frame pointing at nothing, and it fails at
#: import rather than silently contributing zero at runtime.
RESOURCE_TYPES: frozenset[str] = frozenset(r.resource_type for r in _map.RECORDS)

#: Every product area, in the map's own spelling.
PRODUCT_AREAS: tuple[str, ...] = _map.PRODUCT_AREAS

#: Words, at least three characters, lowercase. Deliberately *not*
#: ``knowledge._TERM``: that one keeps ``/``, ``.`` and ``-`` because it tokenises file
#: paths, and a person asking about their account does not type paths. The stop list is
#: shared, because a word that separates nothing in the corpus separates nothing here
#: either, and two stop lists is two places to forget a word.
_WORD = re.compile(r"[a-z0-9]{3,}")

#: Splits identifiers and resource types into their parts: ``crypto.alerts.pause`` gives
#: ``crypto``, ``alerts``, ``pause``; ``support_ticket`` gives ``support``, ``ticket``.
_PART = re.compile(r"[a-z0-9]+")

#: Suffixes folded away so that what a person types meets what the map declares. The map
#: is written in singulars — ``session``, ``device``, ``notification``, ``follow_edge`` —
#: and people ask about their *sessions*, their *devices*, who they are *following*. Two
#: spellings of one noun failing to meet was a real miss here: "who is following me"
#: reached ``social.follow`` only through the prose in its description, which is not
#: enough to activate an area, so the request that the Social relationships area exists
#: to serve did not open it.
_SUFFIXES = ("s", "es", "ed", "ing")

#: Below this length, folding does more harm than good: ``ads`` must not become ``ad``
#: and lose its capability-id match.
_MIN_FOLDED = 4


def _variants(word: str) -> tuple[str, ...]:
    """A word and the forms it might be spelled in elsewhere, best form first.

    Every variant is *indexed* as well as queried, which is what makes this work without
    a real stemmer: the map writes ``crypto.alerts.pause`` and ``device``, people type
    "alert" and "devices", and neither side has to guess which way the other went. A
    single-form fold was the first attempt and it produced a silent miss — ``devices``
    folded to ``devic`` because the ``es`` rule fired before the ``s`` rule, so "what
    devices am I signed in on" did not reach ``security.device.list``, the one capability
    that answers it. Generating both and letting the index decide has no such ordering
    to get wrong.

    Deliberately crude — four suffixes, one pass, no dictionary. Every extra conflation
    is another way for a request about one thing to open the area for another, and the
    failure to avoid here is over-matching: a missed area is a narrower answer, a wrongly
    opened one is a confidently irrelevant one.
    """
    forms = [word]
    if word.endswith("ies") and len(word) - 3 >= _MIN_FOLDED - 1:
        forms.append(word[:-3] + "y")
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_FOLDED - 1:
            candidate = word[: -len(suffix)]
            if candidate not in forms:
                forms.append(candidate)
    return tuple(forms)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(str(text or "").lower()) if w not in STOP_WORDS]


def _parts(text: str) -> list[str]:
    return [p for p in _PART.findall(str(text or "").lower()) if len(p) >= 3 and p not in STOP_WORDS]


#: Fields that describe *what a capability is*, as opposed to what it does or what prose
#: happens to surround it. An area needs at least one cue from this set to activate — see
#: the note in :func:`attend`.
_STRUCTURAL_FIELDS = frozenset({"phrase", "capability", "resource", "area", "concern"})

#: ``folded term -> {capability_id: (weight, field)}``. One entry per (term, capability)
#: pair holding only the *strongest* field that produced it: a term appearing in both the
#: area heading and the description is not twice the evidence, it is the same evidence
#: noticed twice, and summing it would let verbose descriptions outrank precise ids.
_POSTINGS: dict[str, dict[str, tuple[int, str]]] = {}

#: ``(phrase, capability_id)`` for every phrasing the map records, longest first so the
#: most specific phrasing present in a request is the one credited.
_PHRASES: list[tuple[str, str]] = []


def _post(term: str, capability_id: str, weight: int, source_field: str) -> None:
    for form in _variants(term):
        bucket = _POSTINGS.setdefault(form, {})
        current = bucket.get(capability_id)
        if current is None or weight > current[0]:
            bucket[capability_id] = (weight, source_field)


def _build_index() -> None:
    for record in _map.RECORDS:
        cid = record.capability_id
        for term in _parts(cid):
            _post(term, cid, _W_CAPABILITY, "capability")
        for term in _parts(record.resource_type):
            _post(term, cid, _W_RESOURCE, "resource")
        for term in _words(record.product_area):
            _post(term, cid, _W_AREA, "area")
        for phrase in record.supported_intents:
            cleaned = " ".join(str(phrase or "").lower().split())
            if cleaned:
                _PHRASES.append((cleaned, cid))
            for term in _words(phrase):
                _post(term, cid, _W_INTENT_TERM, "intent")
        for term in _words(record.description):
            _post(term, cid, _W_DESCRIPTION, "description")

    # Drop the terms that are in everything. Done after the index is built rather than
    # by a hand-kept list, so it tracks the map: a word becomes uninformative because
    # enough records use it, not because somebody predicted it would.
    ceiling = max(1, int(len(_map.RECORDS) * _COMMON_TERM_SHARE))
    for term in [t for t, hits in _POSTINGS.items() if len(hits) > ceiling]:
        del _POSTINGS[term]

    _PHRASES.sort(key=lambda pair: (-len(pair[0]), pair[0]))


_build_index()

#: ``capability_id -> product_area``, so a posting resolves to an area without walking
#: the record list again.
_AREA_OF: dict[str, str] = {r.capability_id: r.product_area for r in _map.RECORDS}
_RESOURCE_OF: dict[str, str] = {r.capability_id: r.resource_type for r in _map.RECORDS}
_RECORD_OF: dict[str, Any] = {r.capability_id: r for r in _map.RECORDS}

#: ``resource_type -> capability ids``, used by the concern frames.
_BY_RESOURCE: dict[str, tuple[str, ...]] = {}
for _record in _map.RECORDS:
    _BY_RESOURCE.setdefault(_record.resource_type, ())
    _BY_RESOURCE[_record.resource_type] += (_record.capability_id,)
del _record


# ---------------------------------------------------------------------------------
# concerns
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Concern:
    """A symptom frame: what somebody says when they cannot name the subsystem.

    A concern is not a domain. It is a question shape — "something about X is wrong" —
    paired with the resource types that question opens. The resource types are the map's,
    validated at import; the pairing is the editorial content, and it is small on purpose.

    Both ``anchors`` and ``symptoms`` must appear for the frame to fire. Firing on a
    symptom alone would mean every sentence containing "weird" opened a security
    investigation, and firing on an anchor alone would mean every sentence containing
    "account" did.
    """

    name: str
    anchors: frozenset[str]
    symptoms: frozenset[str]
    resources: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        unknown = tuple(sorted(set(self.resources) - RESOURCE_TYPES))
        if unknown:
            # Import-time failure, deliberately, and for the reason the knowledge map
            # gives about its own malformed records: a frame pointing at a resource type
            # that no longer exists contributes nothing, silently, and the symptom of
            # that is a person asking why nothing was checked.
            raise ValueError(f"concern {self.name!r} names unknown resource types: {unknown}")
        if not self.anchors or not self.symptoms or not self.resources:
            raise ValueError(f"concern {self.name!r} must have anchors, symptoms and resources")

    def fires(self, terms: frozenset[str]) -> tuple[str, str]:
        """The (anchor, symptom) that fired this frame, or ``("", "")``."""
        anchor = next((t for t in sorted(self.anchors) if t in terms), "")
        symptom = next((t for t in sorted(self.symptoms) if t in terms), "")
        return (anchor, symptom) if anchor and symptom else ("", "")


#: The frames. Deliberately few. Every one of these earns its place by naming a route
#: from a sentence somebody actually says to a set of records no vocabulary match reaches,
#: and each new one is a chance to activate something that should have stayed quiet.
CONCERNS: tuple[Concern, ...] = (
    Concern(
        name="account_anomaly",
        anchors=frozenset({
            "account", "profile", "login", "logins", "signin", "password", "security",
            "sessions", "session", "devices", "device", "everything",
        }),
        symptoms=frozenset({
            "strange", "strangely", "weird", "weirdly", "odd", "oddly", "wrong",
            "broken", "hacked", "compromised", "suspicious", "unusual", "unexpected",
            "acting", "glitching", "glitchy", "misbehaving", "messed", "hijacked",
            "breached", "stolen", "unauthorised", "unauthorized",
        }),
        resources=(
            # account standing
            "account_health", "account_health_fact",
            # who is signed in, on what
            "session", "device", "device_session", "security_event",
            # what changed in the settings
            "setting", "setting_recommendation", "security_setting", "privacy_setting",
            # what arrived
            "notification", "notification_preference", "notification_group",
            # whether it is already known about
            "support_ticket",
        ),
        rationale=(
            "Somebody who can name the subsystem names it. Somebody who says their "
            "account is acting strange is reporting a symptom, and the honest response "
            "is to check the things that produce that symptom: who is signed in, on "
            "what, what changed in the settings, what the account's standing is, what "
            "arrived in the notifications, and whether support already knows.\n\n"
            "``activity_fact`` is deliberately absent even though a daily activity "
            "summary sounds relevant. It is the resource type behind ``search.activity`` "
            "as well as ``activity.daily_summary``, so including it opened Search on a "
            "request that has nothing to do with searching — a frame is only as precise "
            "as the resource types it names, and one shared type is enough to lose the "
            "precision the second half of §6 is asking for."
        ),
    ),
    Concern(
        name="unwanted_volume",
        anchors=frozenset({
            "notifications", "notification", "alerts", "emails", "email", "messages",
            "pings", "push",
        }),
        symptoms=frozenset({
            "many", "constant", "constantly", "keep", "keeps", "flooded", "spam",
            "spammed", "endless", "nonstop", "stop", "stopping", "annoying", "loud",
            "buzzing", "overwhelming",
        }),
        resources=(
            "notification", "notification_preference", "notification_group",
            "setting", "setting_recommendation",
        ),
        rationale=(
            "\"Why do I keep getting these\" is a question about settings, not about "
            "the notifications themselves, and the settings capability is the one a "
            "vocabulary match on \"notifications\" would never reach."
        ),
    ),
)


# ---------------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    """One reason something was activated.

    Kept as a value rather than a formatted string because §6's rule is conditional —
    those areas must not activate unless evidence connects them — and a condition that
    can only be read as prose cannot be tested.
    """

    term: str
    #: Which part of the map matched: ``phrase``, ``capability``, ``resource``, ``area``,
    #: ``intent``, ``description`` or ``concern``.
    source_field: str
    capability_id: str
    weight: float

    def __str__(self) -> str:
        return f"{self.term!r} matched {self.source_field} of {self.capability_id}"


@dataclass(frozen=True)
class Area:
    """One activated product area, and what inside it is actually reachable.

    ``capability_ids`` and ``unreachable`` are separate fields rather than one list with
    a flag because the difference decides what the caller can offer. An area that is
    relevant with nothing executable in it is a real and common state — the map has
    twenty-six records whose status is ``service_missing`` — and folding it in with the
    executable ones produces a plan that fails at dispatch instead of a sentence that
    says the feature is not wired up yet.
    """

    product_area: str
    score: float
    resource_types: tuple[str, ...]
    #: Executable capabilities being carried.
    capability_ids: tuple[str, ...]
    #: Executable, and cut by the capability ceiling rather than by the map. Kept apart
    #: from ``unreachable`` because merging them was a real defect in the first version
    #: of this module: a working capability dropped for budget was reported as one the
    #: product has not built, and :func:`place_into` wrote that sentence into the
    #: workspace, where it would have become an answer telling somebody a feature they
    #: use does not exist.
    deferred: tuple[str, ...] = ()
    #: Relevant, and not executable today — ``service_missing`` and its neighbours.
    unreachable: tuple[str, ...] = ()
    cues: tuple[Cue, ...] = field(default=(), repr=False)

    @property
    def reachable(self) -> bool:
        """Whether the *map* says something here can run, not whether budget allowed it."""
        return bool(self.capability_ids or self.deferred)


@dataclass(frozen=True)
class Focus:
    """Where to look for one request. Possibly nowhere, which is a real answer.

    ``__bool__`` is "something activated", not "attention succeeded". A focus that
    activated nothing because the request matched nothing is working correctly; the
    caller distinguishes the two through :attr:`ok`.
    """

    areas: tuple[Area, ...] = ()
    #: Areas that had a cue and did not survive the floor or the ceiling. Only areas with
    #: some evidence appear here — this is the tail that was considered and cut, not a
    #: list of the forty-four areas that were never in the running.
    withheld: tuple[str, ...] = ()
    #: The request's terms, after stop words. The request itself is deliberately not
    #: kept: routing needs the words that matched, and storing the sentence would make
    #: every focus a copy of whatever the person typed, in a value that is safe to log.
    terms: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    #: How many areas had any cue at all, before the floor and the ceiling.
    considered: int = 0
    #: Whether the ceiling, rather than the floor, did the cutting.
    crowded: bool = False
    ok: bool = True
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return bool(self.areas)

    def __len__(self) -> int:
        return len(self.areas)

    @property
    def area_names(self) -> tuple[str, ...]:
        return tuple(area.product_area for area in self.areas)

    def activated(self, product_area: str) -> bool:
        return product_area in self.area_names

    @property
    def capability_ids(self) -> tuple[str, ...]:
        """Executable capabilities across every activated area, best first."""
        return tuple(cid for area in self.areas for cid in area.capability_ids)

    @property
    def unreachable(self) -> tuple[str, ...]:
        return tuple(cid for area in self.areas for cid in area.unreachable)

    @property
    def deferred(self) -> tuple[str, ...]:
        """Executable, relevant, and beyond the capability ceiling."""
        return tuple(cid for area in self.areas for cid in area.deferred)

    def why(self, product_area: str) -> tuple[str, ...]:
        """The cues that activated an area, as readable sentences."""
        for area in self.areas:
            if area.product_area == product_area:
                return tuple(str(cue) for cue in area.cues)
        return ()

    def inspect(self) -> dict[str, Any]:
        """Shape, for logging. No request text and no capability descriptions."""
        return {
            "ok": self.ok,
            "reason": self.reason,
            "areas": list(self.area_names),
            "withheld": list(self.withheld),
            "considered": self.considered,
            "crowded": self.crowded,
            "concerns": list(self.concerns),
            "terms": len(self.terms),
            "capabilities": len(self.capability_ids),
            "deferred": len(self.deferred),
            "unreachable": len(self.unreachable),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------------


def attend(request: Any, *, env: Mapping[str, str] | None = None) -> Focus:
    """Decide what one request is about.

    Always returns a :class:`Focus`, never ``None`` and never an exception. Disabled,
    empty and unmatched all produce a focus that activated nothing and says why, because
    the caller's fallback for an exception here would be to carry on with no routing at
    all — which is the unbounded behaviour this exists to replace.
    """
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    notes = tuple(resolution.notes)

    if not bool(values.get("UNDX_BRAIN_ENABLED", False)):
        return Focus(ok=False, reason="the Brain layer is disabled", notes=notes)
    if not bool(values.get("UNDX_BRAIN_ATTENTION_ENABLED", False)):
        return Focus(
            ok=False,
            reason="salience routing is disabled; every call site selects context as it "
                   "does today",
            notes=notes,
        )

    text = str(request or "")
    extra: tuple[str, ...] = ()
    if len(text) > MAX_REQUEST_CHARS:
        extra = (f"only the first {MAX_REQUEST_CHARS} characters were routed on",)
        text = text[:MAX_REQUEST_CHARS]
    lowered = " ".join(text.lower().split())
    if not lowered:
        return Focus(ok=True, reason="the request was empty", notes=notes + extra)

    ordered_terms = []
    for word in _words(lowered):
        if word not in ordered_terms:
            ordered_terms.append(word)
        if len(ordered_terms) >= MAX_TERMS:
            break
    terms = tuple(ordered_terms)
    term_set = frozenset(terms)

    max_areas = _clamp(values.get("UNDX_ATTENTION_MAX_AREAS"), MAX_AREAS, 1, MAX_AREAS)
    skill_ceiling = _workspace.BY_SLOT[Slot.SKILL].limit
    max_capabilities = _clamp(
        values.get("UNDX_ATTENTION_MAX_CAPABILITIES"),
        MAX_CAPABILITIES, 1, min(MAX_CAPABILITIES, skill_ceiling),
    )

    # ---- gather cues -------------------------------------------------------------
    #
    # ``scores`` is per capability record; areas are formed from it afterwards. Scoring
    # records first is what lets a single precise hit beat a scattering across a large
    # area, which is the failure mode the breadth cap exists for.
    scores: dict[str, float] = {}
    cues: dict[str, list[Cue]] = {}

    def add(cid: str, weight: float, cue: Cue) -> None:
        scores[cid] = scores.get(cid, 0.0) + weight
        cues.setdefault(cid, []).append(cue)

    # Whole phrasings first. A phrase already counted is not counted again through its
    # words, so "show my support tickets" does not also collect an intent-term hit for
    # "support" and one for "tickets" on the same record.
    credited: set[tuple[str, str]] = set()
    for phrase, cid in _PHRASES:
        if phrase and phrase in lowered:
            key = (cid, "phrase")
            if key in credited:
                continue
            credited.add(key)
            add(cid, _W_INTENT_PHRASE, Cue(phrase, "phrase", cid, _W_INTENT_PHRASE))

    for term in terms:
        # Strongest posting per capability across the term's forms, so a word that
        # matches an id in one spelling and a description in another is credited once,
        # for the id.
        best: dict[str, tuple[int, str]] = {}
        for form in _variants(term):
            for cid, posting in _POSTINGS.get(form, {}).items():
                if cid not in best or posting[0] > best[cid][0]:
                    best[cid] = posting
        for cid, (weight, source_field) in best.items():
            if (cid, "phrase") in credited and source_field == "intent":
                continue
            add(cid, float(weight), Cue(term, source_field, cid, float(weight)))

    fired: list[str] = []
    for concern in CONCERNS:
        anchor, symptom = concern.fires(term_set)
        if not anchor:
            continue
        fired.append(concern.name)
        label = f"{anchor}+{symptom}"
        for resource in concern.resources:
            for cid in _BY_RESOURCE.get(resource, ()):
                add(cid, float(_W_CONCERN), Cue(label, "concern", cid, float(_W_CONCERN)))

    if not scores:
        return Focus(
            terms=terms, ok=True,
            reason="nothing in the product map matched this request",
            notes=notes + extra,
        )

    # ---- form areas --------------------------------------------------------------
    grouped: dict[str, list[str]] = {}
    for cid in scores:
        grouped.setdefault(_AREA_OF[cid], []).append(cid)

    candidates: list[Area] = []
    for area_name, ids in grouped.items():
        ranked = sorted(ids, key=lambda c: (-scores[c], c))
        best = scores[ranked[0]]
        breadth = _W_BREADTH * min(len(ranked) - 1, _BREADTH_CAP)
        executable = tuple(c for c in ranked if _RECORD_OF[c].is_executable)
        candidates.append(Area(
            product_area=area_name,
            score=round(best + breadth, 3),
            resource_types=tuple(sorted({_RESOURCE_OF[c] for c in ranked})),
            capability_ids=executable,
            unreachable=tuple(c for c in ranked if c not in set(executable)),
            cues=tuple(cue for c in ranked for cue in cues[c]),
        ))

    # An area with nothing executable ranks behind every area that has something,
    # whatever the scores say. This is the one place ordering is not pure salience, and
    # it is deliberate: attention selects what to carry into a bounded context, and an
    # area where nothing can be run contributes one sentence — "this is not wired up
    # yet" — which must never displace a capability that would have done the work. The
    # map makes this a live concern rather than a hypothetical: twenty-six of its
    # records are ``service_missing``, and several of them sit in areas named so
    # closely to the built ones ("Privacy settings" beside "Privacy") that they score
    # almost identically on the same words.
    candidates.sort(key=lambda a: (not a.reachable, -a.score, a.product_area))
    considered = len(candidates)

    # An area needs at least one *structural* cue — a phrasing, a capability id, a
    # resource type, an area name or a concern — to activate at all. Score alone is not
    # enough, and this is the rule that does most of the work in §6's second half.
    #
    # Both false positives found while building this were pure intent-and-description
    # matches. "Find my Bitcoin alert" opened Marketplace and Music, because "find"
    # appears in the phrasings of ``marketplace.search`` and ``music.search`` — the verb
    # names the operation, never the subject. "Why is my account acting strange?" opened
    # Social relationships, because the word "account" appears in the prose describing
    # blocking and muting. In both cases the area accumulated a respectable score out of
    # words that say nothing about what the request is about, and the workspace would
    # then have been offered ``social.follow`` — a write — for somebody reporting that
    # their account was behaving oddly.
    eligible: list[Area] = []
    incidental: list[str] = []
    for area in candidates:
        if any(cue.source_field in _STRUCTURAL_FIELDS for cue in area.cues):
            eligible.append(area)
        else:
            incidental.append(area.product_area)

    if not eligible:
        return Focus(
            terms=terms, withheld=tuple(incidental), considered=considered, ok=True,
            reason="every match was incidental prose, which names no subject",
            notes=notes + extra,
        )

    # The floor is a fraction of the strongest *eligible* area, not of the strongest
    # candidate. Measuring against an area that is about to be discarded would let an
    # incidental match set the bar for the real ones.
    floor = max(a.score for a in eligible) * _SALIENCE_FLOOR

    above = [a for a in eligible if a.score >= floor]
    below = [a.product_area for a in eligible if a.score < floor] + incidental
    crowded = len(above) > max_areas
    kept = above[:max_areas]
    withheld = tuple([a.product_area for a in above[max_areas:]] + below)

    # ---- cap capabilities --------------------------------------------------------
    #
    # Spent one area at a time, round-robin in rank order, rather than filling from the
    # strongest area downwards. Greedy filling was the obvious version and it produced a
    # focus carrying three ways of reading sessions and no way of reading notifications
    # — a narrower answer than the request asked for, arrived at by an implementation
    # detail. Round-robin means every activated area gets its best capability before any
    # area gets its second.
    taken: dict[str, int] = {area.product_area: 0 for area in kept}
    remaining = max_capabilities
    round_index = 0
    while remaining > 0:
        progressed = False
        for area in kept:
            if remaining <= 0:
                break
            if round_index < len(area.capability_ids):
                taken[area.product_area] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
        round_index += 1

    trimmed: list[Area] = []
    for area in kept:
        cut = taken[area.product_area]
        trimmed.append(Area(
            product_area=area.product_area,
            score=area.score,
            resource_types=area.resource_types,
            capability_ids=area.capability_ids[:cut],
            deferred=area.capability_ids[cut:],
            unreachable=area.unreachable,
            cues=area.cues,
        ))

    return Focus(
        areas=tuple(trimmed),
        withheld=withheld,
        terms=terms,
        concerns=tuple(fired),
        considered=considered,
        crowded=crowded,
        ok=True,
        notes=notes + extra,
    )


def place_into(focus: Focus, space: Workspace) -> tuple[Refusal, ...]:
    """Put a focus into a working context, and return whatever it refused.

    This is the join §5 left open: the workspace could bound a context but nothing filled
    it, so context was still assembled per call site. Here the skill slot receives the
    handful of capabilities attention selected rather than the eighty in the registry,
    which is the guarantee — *do not load all capabilities into every request* — enforced
    rather than described.

    Refusals are returned, not raised and not swallowed. A caller that ignores them gets
    a smaller workspace than it expected, which is safe; a caller that reads them can say
    which part of the focus did not fit.
    """
    refusals: list[Refusal] = []
    if not focus.ok or not space.ok:
        return ()

    for area in focus.areas:
        for cid in area.capability_ids:
            record = _RECORD_OF.get(cid)
            if record is None:
                continue
            refusal = space.place(
                Slot.SKILL, cid, record.description,
                source="attention:knowledge_map", confidence=1.0,
            )
            if refusal:
                refusals.append(refusal)
        if not area.reachable:
            # A relevant area with nothing executable is a gap, and the workspace has a
            # slot whose entire purpose is recording gaps. Saying it here is what stops
            # it being discovered at dispatch time as a failure.
            refusal = space.place(
                Slot.UNKNOWN, area.product_area,
                "relevant to this request, but nothing here is executable today",
                source="attention:knowledge_map", confidence=1.0,
            )
            if refusal:
                refusals.append(refusal)
    return tuple(refusals)


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


def _clamp(raw: Any, fallback: int, low: int, high: int) -> int:
    """A configured ceiling, never above the compiled one.

    Same asymmetry as :mod:`services.undx_brain.knowledge`: configuration may narrow
    attention and may not widen it, so a mistyped environment variable cannot turn the
    router into a system that activates everything.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        value = fallback
    else:
        value = raw
    return max(low, min(high, value))
