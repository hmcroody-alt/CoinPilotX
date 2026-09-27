"""Whether commerce may appear beside this content at all.

Every post may be commerce-capable. Not every post should display commerce.
Those are two different sentences and until this module existed the system only
implemented the first one: ``eligibility.py`` decides whether a *listing* is fit
to be shown, and nothing anywhere asked whether the *content* was fit to show it
beside.

The measurement that made this a defect rather than a gap
--------------------------------------------------------
Run against the shipped engine on a 100-listing simulated catalogue, a post
reading ``"we lost my father this morning, rest in peace dad"`` tagged
``["memorial", "grief"]`` returns **2 placements on feed, 1 on post_detail, 0 on
reels**. The zero is not a protection. Reels refused it because those words
matched no product tokens, so ``relevance`` scored 0.0 and the surface's 0.55
floor — the strictest in the system, set for an unrelated reason — happened to
be out of reach.

Change one word so the grief is expressed through an object, which is one of the
commonest ways grief is expressed:

    "wearing dad's old watch to the funeral today, miss you"   tags: memorial, watches

and the count is **1 on reels, 1 on post_detail, 2 on feed**. Every floor in the
system cleared. The better the sensitive post matches, the higher it scores.

That is why this is a refusal and not a weight. Raising the floors does not
help; it is the wrong axis, and on this input a higher floor is *more* likely to
admit the sensitive post than the benign one next to it. A low score is also not
enough on its own, for the reason ``ads_intelligence.context`` already wrote
down about advertising: a low score still wins when nothing else is eligible.
So the refusal is checked before any candidate is retrieved, and no score can
outrank it.

Why the vocabulary is shared with the advertising layer and the policy is not
----------------------------------------------------------------------------
:data:`SENSITIVE_CATEGORIES` is imported from
``services.business_os.ads_intelligence.context`` rather than restated. That
import crosses a package boundary ``promotion.py`` otherwise keeps shut, and it
does so on that module's own terms: "The one thing all three genuinely share is
*placement safety* — a buyer being shown too much commerce does not care which
budget line paid for it." A category where a placement is itself the harm is
placement safety, so it is the shared half by that module's own account, and a
second copy of the list here would drift — one subsystem would learn about a
sensitive category and the other would not.

The *policy* cannot be shared. ``ad_permitted`` refuses every surface outside
``{feed, reels, explore, search}``, which would refuse ``post_detail``,
``messenger``, ``marketplace`` and ``product_detail`` — four live commerce
surfaces. Advertising and commerce have genuinely different surface sets, and
collapsing them would have taken the whole shop down.

Why this is not a keyword blacklist, and where it still is one
-------------------------------------------------------------
A flat list of banned words fails in both directions at once. "I'm dying over
these shoes 😍 link in bio" is a commerce post; "wearing dad's old watch to the
funeral" is not, and no single word separates them. So the detector is tiered by
how much a match actually tells you, and the tiers have different consequences:

* :data:`CERTAIN` — multi-word phrases that are not ambiguous in ordinary use
  ("passed away", "took his own life", "in loving memory"). Refuses on its own
  and **cannot be cleared** by commercial intent. A post selling something in
  the same breath as a bereavement is still not a post to hang a shelf on.
* :data:`STRONG` — single terms that are rare in commercial language and common
  in the contexts we are protecting ("obituary", "hospice", "palliative").
  Refuses on its own.
* :data:`WEAK` — terms with a heavy benign register ("died", "crash", "sick",
  "lost"). One is **not** enough; two from the same category corroborate each
  other and refuse. Refusing on one would suppress a large share of ordinary
  enthusiastic product posts while catching almost nothing, and §3's rule is
  that every post *may* be commerce-capable — a gate that silences most of the
  feed has not implemented the rule, it has repealed the feature.

Corroboration is counted **per category**, which is what makes the weak tier
usable rather than noisy. "this jacket is sick and these boots are killing me"
is one health term and one violence term: two hits, no corroboration, permitted.
"lost my dad this morning, I miss him" is two grief terms and is refused. The
discriminating fact is not how many worrying words a post contains but whether
they agree with each other about what the post is.

There is deliberately no "but it looks commercial" override. An earlier draft
cleared a single weak hit when the post also said "link in bio" or "back in
stock"; it was removed because per-category corroboration already permits every
case it was meant to rescue, so the only thing it could still do was weaken a
refusal that had already corroborated — and a bereavement announcement that
also links a fundraiser is not a post to hang a third party's shelf on.

Structural facts outrank all of it. ``post_type``, ``moderation_status`` and
``risk_score`` were computed at publish time by systems that looked at more than
this module can, so they are checked first and a text scan never gets to
overrule them.

What it cannot do, and why that is survivable
---------------------------------------------
It reads one language, it cannot read an image, it cannot hear sarcasm, and it
will not recognise a bereavement described without any of these words. Those are
real gaps. They are survivable because the failure they produce lands on a
*different* rule: a post this module cannot read is a post
:func:`content.derive` also cannot read, and :data:`NO_SUBJECT` refuses commerce
on a content surface with no derivable subject. So the detector's blind spot and
the no-subject refusal cover each other — the case where the word lists are
useless is the case where there is nothing for a shelf to be relevant to
either.

Nothing here reads the database, writes anything, or sees a viewer. The refusal
records the *category* it fired on and never the text that matched it, for the
reason ``ads_intelligence.context.describe`` gives about its own log: storing
the evidence would create exactly the sensitive-content record the refusal
exists to prevent.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Mapping, Optional

from services.business_os.ads_intelligence.context import (
    SENSITIVE_CONTEXT_CATEGORIES as SENSITIVE_CATEGORIES,
)

LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Outcome vocabulary
# --------------------------------------------------------------------------- #

#: Closed, because these codes surface in "why am I seeing this", in admin
#: diagnostics and in aggregate suppression counts. A free-text reason is
#: unaggregatable, and a suppression nobody can count is a suppression nobody
#: will notice has stopped firing.
PERMITTED = "PERMITTED"
SENSITIVE_CONTEXT = "SENSITIVE_CONTEXT"
POST_TYPE_EXCLUDED = "POST_TYPE_EXCLUDED"
MODERATION_NOT_CLEARED = "MODERATION_NOT_CLEARED"
CONTENT_RISK = "CONTENT_RISK"
NO_SUBJECT = "NO_SUBJECT"

REFUSAL_CODES = (
    SENSITIVE_CONTEXT, POST_TYPE_EXCLUDED, MODERATION_NOT_CLEARED,
    CONTENT_RISK, NO_SUBJECT,
)
ALL_CODES = (PERMITTED,) + REFUSAL_CODES

#: The surfaces that place commerce *beside a piece of content* and therefore
#: make a claim about it. Not the same question as "which surfaces run the
#: engine": ``marketplace`` and ``messenger`` run it and are absent here because
#: neither has surrounding content to be unsuitable — Marketplace's shelves sit
#: in a shop and the messenger strip must never have a conversation derived into
#: a context at all. ``product_detail`` is absent for a different reason: its
#: context is built server-side from the anchor listing by ``_anchor_context``,
#: so it describes a product in our own catalogue rather than anybody's post,
#: and a seller-supplied listing is ``eligibility.py``'s problem.
#:
#: Kept here rather than in ``schema`` because it is a policy claim, not a
#: registry: it answers "does a placement here assert something about nearby
#: content", and only this module cares.
CONTENT_SURFACES = frozenset({"feed", "reels", "post_detail"})

#: Evidence strength, reported so a suppression can be audited without storing
#: what was suppressed.
CERTAIN = "certain"
STRONG = "strong"
CORROBORATED = "corroborated"
STRUCTURAL = "structural"
DECLARED = "declared"

# --------------------------------------------------------------------------- #
# Structural gates
# --------------------------------------------------------------------------- #

#: Post types that never carry commerce whatever they say. ``scam_report`` is
#: the one that matters and the reason this list exists at all: it is a warning
#: that someone is being defrauded, and a product shelf under it is both
#: grotesque and a plausible vector — the fraud being reported could buy the
#: slot. It is a real ``post_type`` accepted by ``CreatePostPayload``.
EXCLUDED_POST_TYPES = frozenset({"scam_report", "memorial", "obituary", "tribute"})

#: Moderation states that may carry commerce. Anything else — pending, flagged,
#: rejected, under review, or a value this module has never heard of — does not.
#: Default-deny on an unknown state is the whole point: a new moderation status
#: added by another team should suspend commerce until someone decides it
#: shouldn't, rather than inheriting permission by not being on a denylist.
CLEARED_MODERATION_STATES = frozenset({"approved", "auto_approved", "clean", "ok"})

#: ``pulse_posts.risk_score`` runs 0–100. ``eligibility.py`` drops a *listing* at
#: 30 and a *seller* at 60; content sits at 30 with the listing, because the
#: question is the same question — is there enough doubt about this thing that we
#: should not be building a commercial surface on top of it.
MAX_CONTENT_RISK = 30

# --------------------------------------------------------------------------- #
# Text evidence
# --------------------------------------------------------------------------- #

#: Phrases that are not ambiguous in ordinary use. Refuse alone, uncleaarable.
#: Multi-word on purpose: single words are what makes a blacklist wrong, and
#: every entry here survives the test "can I write a product post containing
#: this phrase". Grouped by the category reported on refusal.
CERTAIN_PHRASES: dict[str, tuple[str, ...]] = {
    "grief": (
        "passed away", "rest in peace", "in loving memory", "celebration of life",
        "laid to rest", "my condolences", "deepest condolences", "funeral service",
        "memorial service", "he is survived by", "she is survived by",
        "gone too soon", "will be missed by",
    ),
    "self_harm": (
        "took his own life", "took her own life", "took their own life",
        "end my life", "kill myself", "killing myself", "want to die",
        "suicide hotline", "crisis hotline", "self harm", "self harming",
    ),
    "health_condition": (
        "diagnosed with", "terminal diagnosis", "palliative care",
        "in the icu", "in intensive care", "life support",
        "starting chemo", "on chemo", "cancer treatment",
    ),
    "disaster": (
        "death toll", "state of emergency", "evacuation order",
        "mass shooting", "natural disaster", "search and rescue",
    ),
    "violence": (
        "shot and killed", "found dead", "war crimes", "air strike",
    ),
}

#: Single terms rare in commercial language and common in what we protect.
#: Refuse alone. The test for membership is the same one: a term belongs here
#: only if a plausible product post containing it does not exist.
#: ``wake`` is the instructive exclusion: a funeral wake is exactly what this
#: tier is for, and "wake up early" is the most ordinary sentence in the feed.
#: It fails the membership test, so it is not here — the test is not "does this
#: word appear in bereavements" but "is there no plausible product post
#: containing it".
STRONG_TERMS: dict[str, tuple[str, ...]] = {
    "grief": ("obituary", "obituaries", "pallbearer", "eulogy", "bereavement",
              "bereaved", "mourners", "condolences"),
    "self_harm": ("suicidal", "selfharm"),
    "health_condition": ("hospice", "palliative", "chemotherapy", "terminally",
                         "malignant", "metastatic"),
    "disaster": ("casualties", "fatalities"),
    "violence": ("manslaughter", "homicide"),
}

#: Terms with a heavy benign register. One is not evidence; two from the same
#: category are. Every entry here has an obvious commercial use — that is the
#: criterion for being in this tier rather than :data:`STRONG_TERMS`.
WEAK_TERMS: dict[str, tuple[str, ...]] = {
    "grief": ("died", "die", "dies", "dying", "death", "dead", "funeral",
              "grave", "buried", "burial", "mourning", "grief", "grieving",
              "memorial", "widow", "widower", "loss", "lost", "miss", "gone",
              "heaven", "angel", "anniversary"),
    "self_harm": ("crisis", "suicide", "overdose", "hopeless", "struggling",
                  "breakdown", "relapse"),
    # No "treatment" and no "ill": a hair treatment and a skin treatment are
    # products, and the cost of pairing one of those with "sick" — which in a
    # beauty caption means the opposite — is a suppressed shelf on exactly the
    # posts this layer is for, bought for no safety at all.
    "health_condition": ("cancer", "tumour", "tumor", "diagnosis", "surgery",
                         "hospital", "hospitalised", "hospitalized", "illness",
                         "sick", "symptoms", "ward", "icu"),
    "disaster": ("earthquake", "hurricane", "wildfire", "flood", "flooding",
                 "evacuated", "evacuate", "tsunami", "typhoon", "quake"),
    "violence": ("shooting", "shot", "stabbed", "murdered", "murder", "assault",
                 "attacked", "attack", "war", "bombing", "bombed", "killed",
                 "crash", "collision", "accident"),
}

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _flat(*values: Any) -> str:
    """Text folded for phrase search, padded so every match is word-bounded.

    NFKC first: a fullwidth or styled character is the cheapest way past a
    literal phrase match, and the platform accepts both. Every non-word
    character collapses to one space, which means punctuation cannot break a
    phrase ("passed away." and "passed  away" both match) and a phrase cannot
    match across a word boundary it does not own.
    """
    parts: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            parts.extend(str(item) for item in value if item not in (None, ""))
        else:
            parts.append(str(value))
    joined = unicodedata.normalize("NFKC", " ".join(parts)).lower()
    return " " + _NON_WORD.sub(" ", joined).strip() + " "


def _words(flat: str) -> set[str]:
    return {match.group(0) for match in _WORD.finditer(flat)}


def _phrase_hit(flat: str, phrases) -> bool:
    return any(f" {phrase} " in flat for phrase in phrases)


def _clean_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _risk(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        # Unparseable is not zero. A risk score we cannot read is a risk score,
        # and defaulting it to clean would make a malformed row the safest kind
        # of row to have.
        return MAX_CONTENT_RISK + 1


def _decision(code: str, detail: str, *, category: Optional[str] = None,
              evidence: Optional[str] = None) -> dict:
    return {
        "permitted": code == PERMITTED,
        "code": code,
        "detail": detail,
        # The category is safe to store and to show; the text that matched it is
        # not, and is never returned.
        "category": category,
        "evidence": evidence,
    }


def declared_sensitive(post: Mapping[str, Any]) -> Optional[str]:
    """A sensitive category the content already declares, or ``None``.

    Checked against the tags and type the platform stored, not against prose.
    A label someone applied deliberately is better evidence than anything a word
    scan produces, and it is the path by which a human moderator or a future
    classifier can suppress commerce on a post without this module needing to
    learn new words.
    """
    candidates = {_clean_status(post.get("post_type"))}
    for key in ("tags_json", "tags", "ai_tags_json", "ai_tags", "topic", "category"):
        raw = post.get(key)
        if isinstance(raw, (list, tuple, set)):
            candidates.update(_clean_status(item) for item in raw)
        elif raw not in (None, ""):
            # A JSON array arrives as a string; its words are enough here,
            # because we are testing membership in a closed category set rather
            # than parsing structure.
            candidates.update(_words(_flat(raw)))
    for candidate in candidates:
        if candidate and candidate in SENSITIVE_CATEGORIES:
            return candidate
    return None


def text_sensitive(post: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    """``(category, evidence_tier)`` for a text match worth acting on, else ``None``.

    Order matters and is strongest-first: a :data:`CERTAIN` phrase must not be
    downgraded by a commercial phrase appearing later in the same post, so the
    counter-evidence check is reached only by the weak tier.
    """
    # ``topic``, ``category`` and ``subcategory`` are here so that the same scan
    # works on a *ranking context* as well as on a post row — see
    # :func:`assess_context`. A post row has none of the three and a context has
    # no ``body``, so each shape contributes the keys it has and the absent ones
    # cost nothing.
    #
    # The two taxonomy fields are scanned even though a legitimate client fills
    # them from our own category tree, where nothing sensitive lives. They are
    # scanned because on that path they are *client-supplied strings* and this is
    # the only check that sees the wire: a caller that put a bereavement in a
    # subcategory, by bug or on purpose, would otherwise pass unread.
    flat = _flat(post.get("title"), post.get("body"), post.get("ai_summary"),
                 post.get("topic"), post.get("category"),
                 post.get("subcategory"), post.get("tags_json"),
                 post.get("tags"))
    if flat.strip() == "":
        return None

    for category, phrases in CERTAIN_PHRASES.items():
        if _phrase_hit(flat, phrases):
            return category, CERTAIN

    words = _words(flat)
    for category, terms in STRONG_TERMS.items():
        if words & set(terms):
            return category, STRONG

    # Corroboration: two distinct terms from one category. Counted per category
    # rather than across all of them, because "crash" (violence) plus "sick"
    # (health) is a description of a skateboard video, and a single total across
    # categories would refuse it. A single hit within a category returns nothing
    # at all rather than a "near miss" — a state nothing acts on is a state
    # somebody will eventually start acting on.
    for category, terms in WEAK_TERMS.items():
        if len(words & set(terms)) >= 2:
            return category, CORROBORATED
    return None


def assess_adjacency(post: Optional[Mapping[str, Any]]) -> dict:
    """May commerce appear *beside* this content at all.

    This is the harm question, and it is separate from the claim question — a
    distinction worth naming because conflating them suppresses the wrong posts.

    * **Adjacency** (here): a product shelf next to a bereavement is offensive
      whether or not it claims any connection to it. Proximity is the harm, so
      the answer depends only on the content.
    * **Subject** (:func:`assess`): whether this content can justify a shelf
      that says it is *about* it. That is a question about evidence, and its
      answer is :data:`NO_SUBJECT`.

    The feed needs only the first. Its commerce row is a sibling row inserted
    between posts rather than an attachment to one, so it never claims to be
    about its neighbour — and marking a caption-less photo unsuitable because the
    server could derive no subject from it would remove commerce from a large
    population to protect nobody. The structural gates still apply, because a
    ``scam_report`` or an unmoderated post is an adjacency problem too.
    """
    if not post:
        # No row is not a benign default. A caller that reached commerce without
        # the content it is decorating cannot have checked it.
        return _decision(NO_SUBJECT, "no content was supplied to assess",
                         evidence=STRUCTURAL)

    post_type = _clean_status(post.get("post_type"))
    if post_type in EXCLUDED_POST_TYPES:
        return _decision(POST_TYPE_EXCLUDED,
                         f"{post_type} never carries commerce",
                         category=post_type, evidence=STRUCTURAL)

    status = _clean_status(post.get("moderation_status"))
    if status not in CLEARED_MODERATION_STATES:
        return _decision(MODERATION_NOT_CLEARED,
                         "content has not cleared moderation",
                         category=status or "unknown", evidence=STRUCTURAL)

    if _risk(post.get("risk_score")) >= MAX_CONTENT_RISK:
        return _decision(CONTENT_RISK,
                         "content risk score is at or above the commerce limit",
                         evidence=STRUCTURAL)

    declared = declared_sensitive(post)
    if declared:
        return _decision(SENSITIVE_CONTEXT,
                         "content is labelled with a category where a "
                         "placement is itself the harm",
                         category=declared, evidence=DECLARED)

    found = text_sensitive(post)
    if found:
        category, evidence = found
        return _decision(SENSITIVE_CONTEXT,
                         "content reads as a context where a placement is "
                         "itself the harm",
                         category=category, evidence=evidence)

    return _decision(PERMITTED, "content may have commerce beside it")


def assess(post: Optional[Mapping[str, Any]], *,
           context: Optional[Mapping[str, Any]] = None) -> dict:
    """Whether commerce may be displayed beside this content **and be about it**.

    Both questions, adjacency first: see :func:`assess_adjacency` for why they
    are two. This is the entry point for a surface whose row makes a claim about
    the content it sits next to, which is every content surface except the feed.

    Called before retrieval, not after ranking. A refused post must cost nothing
    — no candidate pool, no exposure ledger write, no impression token — and a
    gate that runs after scoring would have already paid for all three and would
    be one ``except`` away from being skipped.

    ``context`` is :func:`content.derive`'s output for the same post. It is read
    only to answer "is there anything here for a shelf to be about", which is
    the :data:`NO_SUBJECT` rule: with no derivable subject, ``relevance`` scores
    ``NEUTRAL`` rather than zero, so an unreadable post does not suppress itself
    — it gets whatever the other signals happen to like, which is the
    post-to-random-carousel shape this whole layer exists to avoid. Measured: a
    ``post_detail`` request with no context at all returns a placement today.

    Surfaces with no surrounding content — Marketplace's own shelves, the
    messenger strip — do not call this. Neither reads content and neither makes
    a claim about any, so there is nothing for this function to assess; the
    messenger strip in particular must never have a conversation derived into a
    context, which is enforced by never passing one rather than by a check here.
    """
    verdict = assess_adjacency(post)
    if not verdict["permitted"]:
        return verdict

    if not context or not (context.get("topic") or context.get("tags")):
        return _decision(NO_SUBJECT,
                         "content has no derivable subject for a placement to "
                         "be about",
                         evidence=STRUCTURAL)

    return _decision(PERMITTED, "content carries commerce")


def assess_context(context: Optional[Mapping[str, Any]]) -> dict:
    """The sensitivity half, judged on a ranking context alone.

    For the caller that has no row to read. The serve endpoint is one: the
    client posts a ``context`` describing what the viewer is looking at and does
    not send an id for it, so on those surfaces this is the only check that can
    run at all today.

    It is deliberately the **weaker** of the two entry points and the difference
    is worth stating, because the temptation is to treat them as equivalent:

    * It cannot see ``post_type``, ``moderation_status`` or ``risk_score``, so
      every structural gate is absent. A ``scam_report`` is invisible here.
    * It is judging the client's *description* of the content rather than the
      content. A client that sent a cheerful topic for a bereavement passes.

    That second point is not a reason to skip the check — it is the reason to
    run it in both places. This one refuses a sensitive context whatever its
    provenance, including one a buggy or hostile client constructed, which is a
    guarantee the row-reading path cannot make because it never sees the wire.
    They are defence in depth, not alternatives, and :func:`assess` remains the
    one that can actually be trusted.
    """
    if not context or not any(context.get(key) for key in
                              ("topic", "tags", "category", "subcategory")):
        return _decision(NO_SUBJECT,
                         "no subject was described for a placement to be about",
                         evidence=STRUCTURAL)

    declared = declared_sensitive(context)
    if declared:
        return _decision(SENSITIVE_CONTEXT,
                         "described context is a category where a placement is "
                         "itself the harm",
                         category=declared, evidence=DECLARED)

    found = text_sensitive(context)
    if found:
        category, evidence = found
        return _decision(SENSITIVE_CONTEXT,
                         "described context reads as one where a placement is "
                         "itself the harm",
                         category=category, evidence=evidence)

    return _decision(PERMITTED, "described context carries commerce")


def permitted(post: Optional[Mapping[str, Any]], *,
              context: Optional[Mapping[str, Any]] = None) -> bool:
    """:func:`assess` reduced to a boolean, for a caller that logs elsewhere."""
    return assess(post, context=context)["permitted"]


#: The key :func:`annotate` writes. Named on the payload rather than inside the
#: commerce namespace because the clients read flat keys off a post, and it is
#: the only commerce field a post carries — deliberately, per the rule that
#: Marketplace stays the one source of price, stock and seller truth.
PAYLOAD_KEY = "commerce_suitable"


def annotate(posts: Any) -> Any:
    """Stamp :data:`PAYLOAD_KEY` onto every post in a serialized feed page.

    This is what closes the feed's exposure, and it has to happen here rather
    than at the serve endpoint because of how the feed's commerce row works: it
    is a sibling row inserted *between* posts by ``injectCommerceRows``, so the
    request that fetched the products never knew which posts it would land
    between. Only the feed response knows that, and only the client knows where
    it finally put the row. So the server answers the question it can answer —
    "may commerce sit next to this one?" — for every post, and the client uses
    the answers to choose a position.

    Free, which is why it can run on every feed page. ``pulse_feed_engine``'s
    payload already carries ``post_type``, ``moderation_status``, ``risk_score``,
    ``title``, ``body``, ``ai_summary``, ``tags`` and ``ai_tags``, which is every
    field :func:`assess_adjacency` reads — so this is pure string work over a
    dict that is already in memory. No query, no extra round trip, nothing to
    batch. That matters more than it sounds: a per-post check that cost a query
    would be the first thing dropped the next time the feed got slow.

    :func:`assess_adjacency` and not :func:`assess`, because a feed row makes no
    claim about its neighbour — see that function for the distinction.

    Only the boolean is emitted, never the category or the evidence tier. A feed
    response is read by every viewer of the post, and "this post was classified
    as grief" is a derived sensitive attribute about its author; shipping it to
    other people's devices to save a debugging round trip is not a trade worth
    making. The refusal reason stays server-side in the log line.

    Never raises. A post this cannot classify is left alone rather than marked
    either way, so a malformed row degrades to today's behaviour instead of
    taking down the feed — §82's rule that the post must render even if every
    commerce layer fails, applied to the layer that decorates it.
    """
    if not isinstance(posts, list):
        return posts
    for post in posts:
        if not isinstance(post, dict):
            continue
        try:
            post[PAYLOAD_KEY] = assess_adjacency(post)["permitted"]
        except Exception:  # pragma: no cover - defensive; see the docstring
            LOGGER.exception("COMMERCE_SUITABILITY_ANNOTATE_FAILED post_id=%s",
                             post.get("id"))
    return posts
