"""Bounded, provenance-carrying retrieval over the source corpus (PART 5).

:mod:`~services.undx_brain.corpus` answers "what does the repository contain?". This
module answers the much narrower question a request actually needs: "which handful of
those files bear on *this* sentence, and how far may an answer built on them go?"

Three constraints shape everything here.

**The corpus never arrives whole.** 1,682 records is roughly 1.4 MB of YAML. Retrieval
returns at most :data:`MAX_RESULTS` records inside at most :data:`MAX_CONTEXT_CHARS`
characters, and both ceilings are hard: configuration can lower them, never raise them.
The reason is not token cost. A model handed the entire repository index will find *some*
file whose summary is adjacent to any question, and will answer confidently from it.
Small, relevant, cited slices are the point.

**Every result carries where it came from.** A record that reaches a prompt arrives with
its path, its category, its trust level and its staleness flag attached, and
:meth:`Retrieval.citations` can name the files that informed an answer. Provenance that
is dropped at retrieval cannot be reconstructed at response time.

**Retrieval never establishes account state.** This is the rule the whole package exists
to protect, restated at the one boundary where it is easiest to lose. The corpus can say
that ``POST /api/alerts/<id>/pause`` exists; nothing in this module — no score, no trust
level, no number of corroborating records — makes it true that *this* user's alert is
paused. :attr:`Retrieval.may_claim_live_state` is a constant ``False``, and it is a
constant rather than a computation so that no future change to the scoring can make it
drift true.

Failure is deliberately asymmetric. A missing, unreadable or unusable corpus yields an
empty :class:`Retrieval` carrying a reason — never an exception. Losing product knowledge
should cost UNDX its ability to explain PulseSoc, not its ability to answer at all.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import config as brain_config
from .corpus import IngestedCorpus, KnowledgeRecord, ingest, prompt_block
from .truth import TrustLevel, may_explain_product, meets, hedge_for, rank


#: Hard ceilings. :func:`retrieve` clamps to these regardless of configuration, so a
#: mistyped environment variable cannot widen the context window from a dashboard.
MAX_RESULTS = 8
MAX_CONTEXT_CHARS = 6000

#: Terms carried by nearly every question about this product, which therefore separate
#: nothing. Dropping them is what stops "what does PulseSoc do about alerts?" from
#: scoring every file that says "PulseSoc" in its header comment.
_FUNCTION_WORDS = frozenset({
    "about", "after", "also", "and", "any", "are", "back", "been", "before", "both",
    "但", "can", "could", "did", "does", "doing", "done", "each", "for", "from", "get",
    "gets", "getting", "had", "has", "have", "help", "her", "here", "him", "his", "how",
    "into", "its", "just", "like", "make", "many", "may", "might", "more", "most",
    "much", "must", "need", "not", "now", "off", "one", "only", "our", "out", "over",
    "own", "please", "pulse", "pulsesoc", "same", "see", "she", "should", "show", "some",
    "such", "sure", "take", "tell", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "thing", "things", "this", "those", "through", "too",
    "use", "used", "using", "very", "want", "was", "way", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you", "your",
})

#: Dropped from a query for the same reason as the words above — they appear in nearly
#: every record and so separate nothing — but for a different underlying cause. These
#: are the product's own nouns, not grammatical filler, and the distinction matters when
#: a query contains nothing else. "What is the" has no subject and should return
#: nothing; "What is PulseSoc?" has a subject, and the knowledge corpus contains a
#: record titled for it. See the fallback in :func:`_terms`.
UBIQUITOUS_TERMS = frozenset({"pulse", "pulsesoc", "undx"})

STOP_WORDS = _FUNCTION_WORDS | UBIQUITOUS_TERMS

#: Maximum distinct terms drawn from one query. A pasted stack trace should not become a
#: 400-term scoring pass over 1,682 records on the request path.
MAX_TERMS = 24

#: Longest summary that may reach a prompt from a single record. Long enough to be
#: useful, short enough that eight of them cannot crowd out the conversation.
MAX_SUMMARY_CHARS = 600

_TERM = re.compile(r"[a-z0-9_./-]{3,}")
#: Splits on underscores as well as separators, so ``services/undx_tool_gateway.py``
#: yields ``services``, ``undx``, ``tool``, ``gateway``, ``py``. Keeping
#: ``undx_tool_gateway`` whole was a real bug: a user asking about "the tool gateway"
#: matched nothing in the path and the correct file lost to incidental prose hits.
_PATH_SEGMENT = re.compile(r"[a-z0-9]+")

#: Categories carrying human-verified, product-level answers rather than source code.
#: ``undx_knowledge`` is the corpus generated from UNDX_RECON into ``UNDX_TRAINING/``.
#: Membership here affects *ordering only* — see the reservation in :func:`retrieve`.
CURATED_CATEGORIES: frozenset[str] = frozenset({"undx_knowledge"})

#: How many of the returned slots are reserved for :data:`CURATED_CATEGORIES` when any
#: curated record cleared the relevance floor. Deliberately small relative to the
#: default limit of six.
_RESERVED_KNOWLEDGE_SLOTS = 2

#: A match scoring below this fraction of the best match is dropped. Without it, one
#: incidental word hit shared by 889 files survives to the sort, and the alphabetical
#: tie-break — which exists for reproducibility — then picks essentially at random.
#: A result an order of magnitude weaker than the best result is not weak evidence; it
#: is a coincidence of vocabulary, and presenting it as retrieval is worse than
#: returning less.
_RELEVANCE_FLOOR = 0.25

#: Scoring weights. Kept as named constants rather than literals inside the loop because
#: the ordering between them *is* the retrieval policy: an exact filename match should
#: beat a scattering of incidental word hits, and an endpoint match should beat a prose
#: mention, because a user naming a route wants the file that serves it.
_W_PATH_EXACT = 12
#: A term appearing in the *filename* — ``verification`` in ``undx_verification.py`` —
#: is a much stronger signal than the same term appearing in a directory name, because
#: filenames are chosen to describe contents and directories are chosen to group them.
#: Without this tier, ``services/undx_verification.py`` scored no better than any of the
#: forty files that merely live under a matching directory.
_W_STEM_TOKEN = 8
_W_PATH_SEGMENT = 5
#: Divided by the number of tokens in the filename and added on a stem hit, so a shorter,
#: more focused name wins a tie. ``undx_verification.py`` is more about verification than
#: ``pulsesoc_native_verification_qa_audit.py`` is, and before this the two tied at the
#: stem weight and were separated alphabetically — which is to say, arbitrarily.
_W_STEM_FOCUS = 2.0
#: Full weight only when the query term arrived as part of a path or route — someone
#: typing ``/api/alerts/pause`` wants the file that serves it. An ordinary prose word
#: that merely happens to be a URL segment gets :data:`_W_ENDPOINT_INCIDENTAL` instead:
#: "work" is a segment of ``/api/network/…``, and at full weight it lifted three audit
#: scripts above the module actually named for the concept being asked about.
_W_ENDPOINT = 6
_W_ENDPOINT_INCIDENTAL = 1
_W_SYMBOL = 4
_W_DOMAIN_TAG = 3
_W_SUMMARY = 1

#: Trust contributes a small tie-break, never a rank. A tested file and a source-mapped
#: file that match a query equally should come back in that order — but trust must not
#: be able to drag an irrelevant record above a relevant one, which is why this is scaled
#: to less than a single summary hit.
_TRUST_TIEBREAK = 0.1


@dataclass(frozen=True)
class Retrieval:
    """What retrieval found, and the limits on what may be said about it.

    This is returned in every case including total failure, so callers have one shape to
    handle. ``degraded`` with a ``reason`` is the failure form; an empty ``records`` with
    no reason simply means nothing matched.
    """

    query: str
    records: tuple[KnowledgeRecord, ...] = ()
    #: Set when the corpus could not be consulted — missing file, failed audit, disabled
    #: by flag. Distinct from "consulted and found nothing", which callers may want to
    #: report differently.
    degraded: bool = False
    reason: str = ""
    #: Ceilings actually applied, after clamping. Recorded so a caller (or a test) can
    #: see that the bound was enforced rather than trusting that it was.
    applied_limit: int = 0
    applied_char_limit: int = 0
    applied_min_trust: TrustLevel = TrustLevel.SOURCE_MAPPED
    #: Records that matched but were withheld, and why. Retrieval that silently discards
    #: matches is indistinguishable from retrieval that found nothing, and the two want
    #: very different follow-up.
    withheld: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return bool(self.records)

    @property
    def may_claim_live_state(self) -> bool:
        """Always ``False``.

        Product knowledge is not account evidence, at any trust level, in any quantity.
        A constant rather than a derivation, so that no future scoring change can make
        it drift true.
        """
        return False

    @property
    def may_explain_product(self) -> bool:
        """Whether anything retrieved is solid enough to describe PulseSoc from."""
        return any(may_explain_product(record.trust_level) for record in self.records)

    @property
    def weakest_trust(self) -> TrustLevel | None:
        """The floor across results — what any claim drawn from the whole set inherits.

        A floor rather than an average: an answer synthesised from a tested file and a
        source-discovered one is only as good as the weaker input, and averaging would
        launder the weaker input's uncertainty.
        """
        if not self.records:
            return None
        return min((r.trust_level for r in self.records), key=rank)

    def hedge(self) -> str:
        """The qualification a response built on this retrieval must carry."""
        weakest = self.weakest_trust
        if weakest is None:
            return ""
        text = hedge_for(weakest)
        if any(record.stale for record in self.records):
            text += ", and at least one source file has changed since the index was built"
        return text

    def citations(self) -> tuple[str, ...]:
        return tuple(record.citation() for record in self.records)

    def prompt_block(self) -> str:
        """Render for a model prompt inside the untrusted-data envelope."""
        return prompt_block(self.records, char_budget=self.applied_char_limit)

    def as_knowledge_slices(self) -> list[dict[str, Any]]:
        """Adapt to the slice shape ``pulse_ai_service`` already injects.

        ``services/undx_platform_knowledge.retrieve`` returns
        ``{"id", "title", "category", "body"}`` dicts and ``pulse_ai_service`` splices
        them into its knowledge list. Matching that shape means this can be introduced
        beside the existing retrieval without touching the call site's contract — which
        matters because the call site is on the live conversational path and the cheapest
        integration is the one that changes nothing else.

        The trust level and staleness ride in the title rather than a new field, because
        a consumer that renders only ``title`` and ``body`` would otherwise drop the
        provenance silently.
        """
        slices: list[dict[str, Any]] = []
        for record in self.records:
            mark = f", trust={record.trust_level.value}"
            if record.stale:
                mark += ", source may be out of date"
            slices.append({
                "id": 0,
                "title": f"PulseSoc source: {record.path}{mark}"[:200],
                "category": "undx_brain_source_knowledge",
                "body": record.summary[:MAX_SUMMARY_CHARS],
            })
        return slices


def _terms(query: str) -> tuple[list[str], frozenset[str]]:
    """Split a query into scoreable terms, keeping path- and route-shaped tokens whole.

    ``[a-z0-9_./-]`` rather than ``[a-z0-9]`` because users paste
    ``services/undx_tool_gateway.py`` and ``/api/alerts/pause``, and splitting those into
    word fragments loses exactly the signal that would have found the right file.

    Returns the terms and, separately, the subset that came from a path- or route-shaped
    token. The distinction carries the difference between a user *naming a route* and a
    user writing a sentence that happens to contain a word which is also a URL segment —
    see :data:`_W_ENDPOINT`.
    """
    seen: dict[str, None] = {}
    route_like: set[str] = set()
    # Product nouns are dropped for discrimination, not because they are meaningless.
    # Kept aside so a query whose only subject is one of them still has something to
    # score. Grammatical stop words are not kept: "what is the" has no subject at all.
    stopped: dict[str, None] = {}
    for raw in _TERM.findall(str(query or "").lower()):
        term = raw.strip("./-")
        if len(term) < 3:
            continue
        if term in STOP_WORDS:
            if term in UBIQUITOUS_TERMS:
                stopped.setdefault(term, None)
            continue
        is_path = "/" in term or "." in term or "_" in term
        seen.setdefault(term, None)
        if is_path:
            route_like.add(term)
        # A pasted path is also a set of segments; index both so
        # "undx_tool_gateway" finds the file whether or not the directory was included.
        if is_path:
            for part in _PATH_SEGMENT.findall(term):
                if len(part) >= 3 and part not in STOP_WORDS:
                    seen.setdefault(part, None)
                    route_like.add(part)
        if len(seen) >= MAX_TERMS:
            break
    if not seen and stopped:
        # "What is PulseSoc?" otherwise reduces to nothing, and the caller returns an
        # empty result for the most basic question the product can be asked. The stop
        # list was calibrated against a corpus of source files, where "pulsesoc" appears
        # in almost every header and separates nothing — but the knowledge corpus now
        # contains a record *titled* PulseSoc, and a term that discriminates poorly
        # still discriminates better than no term at all. Reached only when the query
        # has no other subject, so it cannot dilute a query that does.
        return list(stopped)[:MAX_TERMS], frozenset()
    return list(seen)[:MAX_TERMS], frozenset(route_like)


def _score(
    record: KnowledgeRecord,
    terms: Sequence[str],
    route_terms: frozenset[str] = frozenset(),
) -> float:
    path = record.path.lower()
    # A record may be addressed by fragment — ``UNDX_TRAINING/03_CAPABILITIES.yaml
    # #reels.save`` — in which case the fragment, not the filename, is its name. The
    # fragment has to be split off before the extension split, because ``rsplit(".", 1)``
    # on the whole path lands inside the fragment and throws it away: every one of the
    # 87 records in the capabilities file then scored identically, and product questions
    # lost to implementation source that merely had the right word in its filename.
    file_part, _, fragment = path.partition("#")
    stem = file_part.rsplit("/", 1)[-1]
    stem_bare = stem.rsplit(".", 1)[0]
    stem_tokens = set(_PATH_SEGMENT.findall(stem_bare))
    # The fragment and the curated title are scored at the filename tier for the same
    # reason filenames outrank directories: both are chosen to describe this record and
    # nothing else. A wordy title dilutes itself through _W_STEM_FOCUS, which is the
    # right behaviour — a precise name is stronger evidence than a discursive one.
    stem_tokens.update(_PATH_SEGMENT.findall(fragment))
    if record.title:
        stem_tokens.update(_PATH_SEGMENT.findall(record.title.lower()))
    segments = set(_PATH_SEGMENT.findall(file_part)) - stem_tokens
    tags = {tag.lower() for tag in record.domain_tags}
    # Tokenised, not substring-matched. Joining the endpoints into one string and asking
    # ``term in endpoints`` awards the full endpoint weight for accidental substrings:
    # "work" scored 6 against every file mentioning ``/api/network/…``, which was enough
    # to push three audit scripts above ``services/undx_verification.py`` for the query
    # "how does verification work".
    endpoints = {
        token
        for mention in record.endpoint_mentions
        for token in _PATH_SEGMENT.findall(mention.lower())
    }
    symbols = {sym.lower() for sym in record.symbols}

    total = 0.0
    for term in terms:
        if term == path or term == stem or term == stem_bare:
            total += _W_PATH_EXACT
            continue
        if term in stem_tokens:
            total += _W_STEM_TOKEN + _W_STEM_FOCUS / max(1, len(stem_tokens))
        elif term in segments:
            total += _W_PATH_SEGMENT
        if term in endpoints:
            total += _W_ENDPOINT if term in route_terms else _W_ENDPOINT_INCIDENTAL
        if term in symbols:
            total += _W_SYMBOL
        if term in tags:
            total += _W_DOMAIN_TAG
        if term in record.search_text:
            total += _W_SUMMARY
    if total:
        total += rank(record.trust_level) * _TRUST_TIEBREAK
    return total


def _limits(
    env: Mapping[str, str] | None,
) -> tuple[int, int, TrustLevel, bool, bool, tuple[str, ...]]:
    resolution = brain_config.resolve(env)
    values = resolution.values
    notes: list[str] = []
    limit = max(1, min(int(values.get("UNDX_KNOWLEDGE_MAX_RESULTS", 6)), MAX_RESULTS))
    chars = max(
        200,
        min(int(values.get("UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS", 4000)), MAX_CONTEXT_CHARS),
    )
    raw_floor = str(values.get("UNDX_KNOWLEDGE_MIN_TRUST_LEVEL", "source_mapped"))
    try:
        floor = TrustLevel(raw_floor)
    except ValueError:
        # Unreachable while ``config`` validates this flag against its choices, and kept
        # anyway: this function must produce a TrustLevel or nothing downstream has a
        # floor at all.
        floor = TrustLevel.DOCUMENTED

    # ``config.resolve`` substitutes the shipped default for any value outside a flag's
    # declared choices. For every other flag that is right. For this one it is not.
    # Trust levels are *ordered*, so a typo in a floor an operator was **raising**
    # (``UNDX_KNOWLEDGE_MIN_TRUST_LEVEL=teested``) silently lands back on the default —
    # looser than what they asked for — and the request that reveals it is one where
    # UNDX said something it was configured not to say. The generic resolver cannot know
    # which direction is safe here; this module can, so it resolves upward.
    source = os.environ if env is None else env
    requested = str(source.get("UNDX_KNOWLEDGE_MIN_TRUST_LEVEL", "")).strip()
    if requested and requested != floor.value:
        floor = TrustLevel.DOCUMENTED
        notes.append(
            f"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL={requested!r} is not a trust level; "
            f"using the stricter {floor.value} rather than the default, because an "
            f"unreadable floor must not be read as a lower one"
        )

    enabled = bool(values.get("UNDX_KNOWLEDGE_RETRIEVAL_ENABLED", True))
    allow_discovered = bool(values.get("UNDX_KNOWLEDGE_ALLOW_SOURCE_DISCOVERED", False))
    return limit, chars, floor, enabled, allow_discovered, tuple(notes)


def retrieve(
    query: str,
    *,
    env: Mapping[str, str] | None = None,
    corpus: IngestedCorpus | None = None,
    limit: int | None = None,
    char_limit: int | None = None,
) -> Retrieval:
    """Return the few corpus records bearing on ``query``, bounded and attributed.

    Never raises. Every failure path — retrieval disabled, corpus missing, corpus
    unusable, YAML unreadable — returns an empty :class:`Retrieval` with ``degraded``
    set and a reason recorded, because the caller is a conversation and a conversation
    that cannot describe the product is still a conversation.
    """
    applied_limit, applied_chars, floor, enabled, allow_discovered, config_notes = _limits(env)
    if limit is not None:
        applied_limit = max(1, min(int(limit), MAX_RESULTS))
    if char_limit is not None:
        applied_chars = max(200, min(int(char_limit), MAX_CONTEXT_CHARS))

    base = Retrieval(
        query=str(query or ""),
        applied_limit=applied_limit,
        applied_char_limit=applied_chars,
        applied_min_trust=floor,
        notes=config_notes,
    )

    if not enabled:
        return _degrade(base, "source knowledge retrieval is disabled by configuration")

    try:
        loaded = corpus if corpus is not None else ingest(env=env)
    except Exception as exc:  # pragma: no cover - ingest is written not to raise
        # Defensive rather than expected. ``ingest`` converts its own failures into a
        # fatal manifest; if it ever stops doing so, a conversation must not die with it.
        return _degrade(base, f"corpus ingestion failed: {type(exc).__name__}")

    if not loaded.ok:
        return _degrade(base, loaded.fatal or "corpus is present but not usable")

    terms, route_terms = _terms(query)
    if not terms:
        return base

    withheld_quarantine = 0
    withheld_trust = 0
    scored: list[tuple[float, str, KnowledgeRecord]] = []
    for record in loaded.records:
        if record.quarantined:
            # Counted, not scored. A record held back for tripping the injection filter
            # is exactly the record an attacker wanted retrieved.
            withheld_quarantine += 1
            continue
        score = _score(record, terms, route_terms)
        if score <= 0:
            continue
        if record.trust_level is TrustLevel.SOURCE_DISCOVERED and not allow_discovered:
            withheld_trust += 1
            continue
        if not meets(record.trust_level, floor):
            withheld_trust += 1
            continue
        scored.append((score, record.path, record))

    # Descending score, then path, so equal-scoring results are stable across runs. A
    # retrieval layer that reorders identical inputs makes every downstream comparison
    # — tests, evals, bug reports — unreproducible.
    scored.sort(key=lambda row: (-row[0], row[1]))

    if scored:
        cut = scored[0][0] * _RELEVANCE_FLOOR
        surviving = [row for row in scored if row[0] >= cut]
        withheld_weak = len(scored) - len(surviving)
        scored = surviving
    else:
        withheld_weak = 0

    kept: list[KnowledgeRecord] = []
    used = 0
    for _, _, record in scored:
        if len(kept) >= applied_limit:
            break
        summary = record.summary[:MAX_SUMMARY_CHARS]
        cost = len(record.path) + len(summary) + 40
        if used + cost > applied_chars:
            continue
        kept.append(record)
        used += cost

    # Guarantee the curated answer layer a place on the page — at the *tail*, never at
    # the head.
    #
    # The problem: "how does Marketplace work?" scored a dozen
    # ``services/marketplace_*.py`` files above the one record written to answer exactly
    # that question, because a filename match outweighs a prose match and there are
    # three thousand source files against three hundred knowledge records. The record
    # cleared the relevance floor and then lost every slot.
    #
    # Why the tail and not a score boost: an exact query for
    # ``services/undx_tool_gateway.py`` must return that file first, and a query about a
    # concept must return the module that implements it first. Boosting the curated
    # category would have moved those too. Substituting only into the lowest-ranked
    # slots leaves every head position decided by score alone, and costs at most two
    # weak matches that were about to be dropped by the limit anyway.
    if len(kept) >= applied_limit and not any(r.category in CURATED_CATEGORIES for r in kept):
        curated = [row[2] for row in scored if row[2].category in CURATED_CATEGORIES]
        if curated:
            keep_head = applied_limit - min(_RESERVED_KNOWLEDGE_SLOTS, len(curated))
            kept = kept[:keep_head] + curated[:applied_limit - keep_head]

    withheld: list[str] = []
    if withheld_quarantine:
        withheld.append(f"{withheld_quarantine} quarantined record(s) excluded")
    if withheld_trust:
        withheld.append(
            f"{withheld_trust} record(s) below the {floor.value} trust floor excluded"
        )
    if withheld_weak:
        withheld.append(f"{withheld_weak} weak match(es) below the relevance floor excluded")
    dropped = len(scored) - len(kept)
    if dropped > 0:
        withheld.append(f"{dropped} lower-scoring match(es) not shown (bounded context)")

    notes: list[str] = list(config_notes)
    if any(record.stale for record in kept):
        notes.append("at least one result is stale; regenerate the corpus")

    return Retrieval(
        query=base.query,
        records=tuple(kept),
        applied_limit=applied_limit,
        applied_char_limit=applied_chars,
        applied_min_trust=floor,
        withheld=tuple(withheld),
        notes=tuple(notes),
    )


def _degrade(base: Retrieval, reason: str) -> Retrieval:
    return Retrieval(
        query=base.query,
        records=(),
        degraded=True,
        reason=reason,
        applied_limit=base.applied_limit,
        applied_char_limit=base.applied_char_limit,
        applied_min_trust=base.applied_min_trust,
        notes=base.notes,
    )


def knowledge_slices(
    query: str,
    *,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Drop-in slices for the existing ``pulse_ai_service`` knowledge injection.

    Returns ``[]`` when the Brain knowledge stage is off, when retrieval is degraded, or
    when nothing matched — so the call site can splice the result unconditionally and an
    unconfigured deployment behaves exactly as it does today.
    """
    resolution = brain_config.resolve(env)
    if not resolution.values.get("UNDX_BRAIN_KNOWLEDGE_ENABLED"):
        return []
    return retrieve(query, env=env).as_knowledge_slices()


__all__ = [
    "MAX_CONTEXT_CHARS",
    "MAX_RESULTS",
    "MAX_SUMMARY_CHARS",
    "Retrieval",
    "knowledge_slices",
    "retrieve",
]
