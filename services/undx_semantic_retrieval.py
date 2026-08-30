"""Semantic retrieval placed *beside* the lexical matcher, never in front of it.

``services/undx_platform_knowledge.retrieve`` is unchanged and remains the baseline. It
scores the source-derived manifest by term overlap, which works when the user writes the
words the manifest uses and fails when they write anything else — a paraphrase, a typo,
slang, or Haitian Creole. This module adds a second opinion built from embeddings and
fuses the two. It does not replace the first one, and the flag that turns it on defaults
to off.

What this module is allowed to answer
-------------------------------------
One question: *what information is probably relevant?*

It does not answer *what is true*, *who owns this*, *is this action allowed*, or
*should this mutation execute*. Those four are answered by canonical PulseSoc state, the
capability registry, ``undx_agent_policy``, the confirmation mint, the tool gateway and
the verifiers, and nothing here touches any of them. A document that scores 0.99 against
a query is a document about a topic; it is not permission and it is not evidence.

The boundary is enforced by construction rather than by discipline. The corpus is the
sanitised public manifest — capability descriptions, route and service summaries,
product terminology. It contains no account state, no ownership edges and no
permissions, so there is nothing in the index that *could* be mistaken for authority
even if a caller tried. :func:`retrieve` returns the same four prompt-shaped keys the
lexical path returns, so no downstream consumer gains a new field to over-trust.

Degradation
-----------
UNDX must keep working when Perplexity does not. The chain, in order:

    provider call → embedding cache → lexical retrieval → truthful empty result

A provider timeout, a 429 past its retry budget, a 5xx, a malformed body, a missing
API key and an unreachable database all land in the same place: the lexical answer the
system would have given anyway. There is no configuration in which the absence of the
embedding provider makes UNDX unavailable, and :func:`retrieve` never raises.

Vector storage
--------------
The existing database, one new table, no new provider. See
:func:`ensure_schema` for the decision and its measurements.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from services import undx_embedding_service as embed
from services import undx_platform_knowledge as lexical

logger = logging.getLogger(__name__)

#: Stated once, in the code, so it is reviewable. Every result this module produces
#: carries this weight in any decision about state, ownership or permission.
AUTHORITY = "none"

CANONICAL_CORPUS = "pulsesoc_platform_manifest"

#: Rollout ladder. Each rung is a superset of the one before it in *risk*, not in
#: behaviour: ``shadow`` computes the semantic answer and throws it away, which is the
#: only rung that produces comparison data at zero user-visible risk.
STAGE_OFF = "off"
STAGE_SHADOW = "shadow"
STAGE_QA = "qa"
STAGE_PRODUCTION = "production"
STAGES: tuple[str, ...] = (STAGE_OFF, STAGE_SHADOW, STAGE_QA, STAGE_PRODUCTION)

STAGE_ENV = "UNDX_SEMANTIC_RETRIEVAL_STAGE"
QA_USERS_ENV = "UNDX_AGENT_QA_USER_IDS"

#: Cosine floor below which a semantic candidate is discarded. Embeddings always return
#: a nearest neighbour, so without a floor an unrelated query gets a confident-looking
#: answer — which is exactly the failure the lexical path avoids by returning nothing.
#: The existing test suite asserts that "xylophone nebula quasar" retrieves nothing, and
#: that assertion has to keep holding with semantic retrieval on.
DEFAULT_SIMILARITY_FLOOR = 0.30

#: Reciprocal-rank-fusion constant. The standard value; fusion is used instead of a
#: weighted score blend because lexical term counts and cosine similarities are not on a
#: comparable scale and any weighting between them would be a number invented here.
RRF_K = 60


# ------------------------------------------------------------------------ configuration


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def stage() -> str:
    """Current rollout rung. Anything unrecognised reads as ``off``.

    Fail-closed on a typo: ``UNDX_SEMANTIC_RETRIEVAL_STAGE=prod`` must not silently
    behave like ``production``, and must not silently behave like ``shadow`` either.
    Off is the only safe reading of a value nobody meant to type.
    """
    value = _env(STAGE_ENV, STAGE_OFF).lower()
    if value not in STAGES:
        if value:
            logger.warning("undx_semantic: %s=%r is not a stage; treating as off", STAGE_ENV, value)
        return STAGE_OFF
    return value


def similarity_floor() -> float:
    raw = _env("UNDX_SEMANTIC_SIMILARITY_FLOOR")
    if not raw:
        return DEFAULT_SIMILARITY_FLOOR
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SIMILARITY_FLOOR


def _qa_user_ids() -> set[str]:
    return {
        part.strip() for part in _env(QA_USERS_ENV).split(",")
        if part.strip().isdigit()
    }


def user_in_scope(user_id: int | None) -> bool:
    """Whether this account may *receive* semantically-influenced results.

    Reuses ``UNDX_AGENT_QA_USER_IDS`` rather than declaring a second cohort variable,
    because two cohort lists drift and the drift is invisible. An empty list means
    nobody, never everybody — the same rule ``undx_agent_policy.user_enabled`` applies.
    """
    current = stage()
    if current in (STAGE_OFF, STAGE_SHADOW):
        return False
    if current == STAGE_PRODUCTION:
        return True
    return bool(user_id) and str(int(user_id)) in _qa_user_ids()


# ------------------------------------------------------------------------------ schema

#: Two tables on the database PulseSoc already runs.
#:
#: **Why not a vector database.** The canonical corpus is 1,729 manifest entries, of
#: which 1,673 are public. At the configured 256 dimensions that is about 1.7 MB of
#: float32 — small enough to hold in process memory — and a brute-force scan over it in
#: pure Python was measured at 6.3 ms (25.3 ms at 1024 dimensions). A vector index
#: exists to avoid an O(n) scan; at this n the scan is already faster than the network
#: call that produced the query vector. Buying Pinecone, Weaviate or Qdrant here would
#: add a provider, a bill, a failure mode and a second copy of the corpus in order to
#: optimise six milliseconds.
#:
#: **Why not pgvector.** It would be the right answer at a hundred times this corpus,
#: and it is the migration path if user content is ever indexed. Today it would require
#: an extension on the production PostgreSQL instance and would diverge from the local
#: SQLite that every test runs against, in exchange for no measurable gain. Revisit when
#: the index passes roughly 10^5 vectors or when per-request latency from the scan
#: exceeds the provider call.
#:
#: **Why base64 TEXT rather than BLOB/bytea.** The same statement has to run on SQLite
#: locally and PostgreSQL in production through this repository's compatibility layer,
#: and binary parameter adaptation is where those two disagree in a way that only
#: surfaces in production.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS undx_embedding_cache (
        cache_key TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        model_version TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        vector TEXT NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS undx_semantic_index (
        doc_id TEXT PRIMARY KEY,
        corpus TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        content_class TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_undx_semantic_index_corpus ON undx_semantic_index (corpus)",
)


def ensure_schema(conn) -> None:
    """Idempotent table creation. Safe to call on every boot and from any worker.

    This repository has no migration framework — ``bot.init_db`` creates schema
    imperatively — so this follows the house pattern rather than introducing a second
    one.
    """
    cursor = conn.cursor()
    for statement in _SCHEMA_STATEMENTS:
        cursor.execute(statement)
    conn.commit()


def _connect():
    from services import db  # noqa: PLC0415 - deferred so tests can import without a DB

    return db.connect()


# ------------------------------------------------------------------------------- cache


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cache_get(conn, keys: Sequence[str]) -> dict[str, list[float]]:
    """Fetch cached vectors by key. Missing keys are simply absent from the result."""
    if not keys:
        return {}
    out: dict[str, list[float]] = {}
    cursor = conn.cursor()
    # Chunked to stay clear of SQLite's parameter ceiling; the chunk size is well under
    # both engines' limits.
    for start in range(0, len(keys), 400):
        chunk = list(keys[start:start + 400])
        placeholders = ",".join("?" for _ in chunk)
        cursor.execute(
            f"SELECT cache_key, vector FROM undx_embedding_cache WHERE cache_key IN ({placeholders})",
            tuple(chunk),
        )
        for row in cursor.fetchall():
            key, encoded = row[0], row[1]
            try:
                out[str(key)] = embed.decode_vector(encoded)
            except Exception:
                # A corrupt row is a cache miss, not an error. It will be rewritten on
                # the next index pass.
                logger.warning("undx_semantic: dropping undecodable cache row")
    return out


def cache_put(conn, key: str, vector: Sequence[float], *, tokens: int = 0) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM undx_embedding_cache WHERE cache_key = ?", (key,))
    cursor.execute(
        "INSERT INTO undx_embedding_cache "
        "(cache_key, model, model_version, dimensions, vector, tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            embed.configured_model(),
            embed.configured_model_version(),
            len(vector),
            embed.encode_vector(vector),
            int(tokens),
            _now(),
        ),
    )


# ----------------------------------------------------------------------------- indexing


@dataclass(frozen=True)
class IndexDocument:
    """One thing that may be embedded, with its content class declared explicitly.

    ``content_class`` is required rather than inferred. Inference is how private
    material ends up in an index: a heuristic that decides a row "looks public" is a
    heuristic that will be wrong once, silently, at scale. Declaring the class forces
    whoever adds a corpus to state what they are sending.
    """

    doc_id: str
    title: str
    body: str
    content_class: str = "canonical_public"
    corpus: str = CANONICAL_CORPUS

    def embed_text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


class ForbiddenContent(ValueError):
    """A document was offered whose content class may never leave PulseSoc.

    Deliberately fatal rather than a skip. A pipeline that silently drops private
    documents and a pipeline that silently sends them look identical from the outside;
    only one of them stops the mission.
    """


def _reject_forbidden(documents: Iterable[IndexDocument]) -> list[IndexDocument]:
    out: list[IndexDocument] = []
    for document in documents:
        content_class = str(document.content_class or "").strip().lower()
        if content_class in embed.FORBIDDEN_CONTENT_CLASSES:
            raise ForbiddenContent(
                f"{document.doc_id}: content class {content_class!r} may never be embedded"
            )
        if not content_class:
            raise ForbiddenContent(f"{document.doc_id}: content class must be declared")
        out.append(document)
    return out


def canonical_documents() -> list[IndexDocument]:
    """The approved starting corpus: the sanitised source-derived platform manifest.

    Exactly the entries ``undx_platform_knowledge`` already serves to prompts, and
    nothing else. Entries flagged ``public: false`` are excluded here for the same
    reason they are excluded there. No user content, no messages, no account state — the
    manifest is generated from source and describes what PulseSoc *is*, not what any
    account *has*.
    """
    documents: list[IndexDocument] = []
    for item in lexical.load_manifest().get("entries") or []:
        if not isinstance(item, dict) or item.get("public") is False:
            continue
        doc_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not doc_id or not name:
            continue
        kind = str(item.get("kind") or "capability").replace("_", " ")
        summary = " ".join(str(item.get("public_summary") or "").split())
        search_text = " ".join(str(item.get("search_text") or "").split())
        if not summary:
            continue
        documents.append(IndexDocument(
            doc_id=doc_id,
            title=f"PulseSoc {kind}: {name}",
            # ``search_text`` carries the terminology the lexical matcher scores on;
            # including it in the embedded text means the semantic index sees the same
            # vocabulary rather than a different slice of the entry.
            body=f"{summary} {search_text}".strip()[:1200],
            content_class="canonical_public",
        ))
    return documents


@dataclass
class IndexResult:
    documents: int = 0
    embedded: int = 0
    cached: int = 0
    tokens: int = 0
    skipped: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)
    ok: bool = True


def index_documents(documents: Sequence[IndexDocument], *, conn=None) -> IndexResult:
    """Embed and store any document whose content has changed. Never re-pays otherwise.

    Cache identity is content plus model plus model version plus dimensionality plus
    normalization version, so an unchanged manifest costs nothing to re-index and a
    changed one costs only its delta. Running this twice in a row makes zero provider
    calls the second time; that is the property the cost model depends on.
    """
    approved = _reject_forbidden(documents)
    if not approved:
        return IndexResult()

    owned = conn is None
    connection = conn or _connect()
    result = IndexResult(documents=len(approved))
    try:
        ensure_schema(connection)
        model = embed.configured_model()
        model_version = embed.configured_model_version()
        dimensions = embed.configured_dimensions()
        keys = [
            embed.cache_key(
                document.embed_text(), model=model, model_version=model_version, dimensions=dimensions
            )
            for document in approved
        ]
        cached = cache_get(connection, keys)
        result.cached = sum(1 for key in keys if key in cached)

        pending = [index for index, key in enumerate(keys) if key not in cached]
        if pending:
            try:
                batch = embed.embed_texts(
                    [approved[index].embed_text() for index in pending], purpose="canonical_index"
                )
            except embed.EmbeddingUnavailable as exc:
                # A partial index is still useful — every document already cached stays
                # searchable — so this reports rather than raises.
                result.ok = False
                result.skipped = len(pending)
                result.notes = (f"provider unavailable: {exc.reason}",)
                connection.commit()
                return result
            result.embedded = len(pending)
            result.tokens = batch.tokens
            for position, index in enumerate(pending):
                cache_put(connection, keys[index], batch.vectors[position])
                cached[keys[index]] = list(batch.vectors[position])

        cursor = connection.cursor()
        for document, key in zip(approved, keys):
            cursor.execute("DELETE FROM undx_semantic_index WHERE doc_id = ?", (document.doc_id,))
            cursor.execute(
                "INSERT INTO undx_semantic_index "
                "(doc_id, corpus, cache_key, title, body, content_class, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document.doc_id,
                    document.corpus,
                    key,
                    document.title[:400],
                    document.body[:2000],
                    document.content_class,
                    embed.content_hash(document.embed_text()),
                    _now(),
                ),
            )
        connection.commit()
    finally:
        if owned:
            try:
                connection.close()
            except Exception:
                pass
    invalidate_cache()
    return result


# ------------------------------------------------------------------- in-memory index


@dataclass(frozen=True)
class _LoadedIndex:
    fingerprint: str
    doc_ids: tuple[str, ...]
    titles: tuple[str, ...]
    bodies: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]


_INDEX_LOCK = threading.Lock()
_index_cache: dict[str, _LoadedIndex] = {}


def invalidate_cache() -> None:
    with _INDEX_LOCK:
        _index_cache.clear()


def _fingerprint() -> str:
    return "|".join((
        embed.configured_model(),
        embed.configured_model_version(),
        str(embed.configured_dimensions()),
        embed.NORMALIZATION_VERSION,
        embed.ENCODING_VERSION,
    ))


def load_index(corpus: str = CANONICAL_CORPUS) -> _LoadedIndex | None:
    """Load the corpus into process memory, once per configuration fingerprint.

    Returns ``None`` when the index is empty or unreachable, which every caller treats
    as "no semantic retrieval right now" rather than as an error. Rows whose stored
    dimensionality does not match the current configuration are skipped rather than
    compared: mixing vector spaces produces plausible numbers and wrong answers.
    """
    fingerprint = _fingerprint()
    with _INDEX_LOCK:
        loaded = _index_cache.get(corpus)
        if loaded is not None and loaded.fingerprint == fingerprint:
            return loaded

    dimensions = embed.configured_dimensions()
    doc_ids: list[str] = []
    titles: list[str] = []
    bodies: list[str] = []
    vectors: list[tuple[float, ...]] = []
    try:
        connection = _connect()
    except Exception as exc:
        logger.warning("undx_semantic: index unavailable (%s)", type(exc).__name__)
        return None
    try:
        ensure_schema(connection)
        cursor = connection.cursor()
        cursor.execute(
            "SELECT i.doc_id, i.title, i.body, c.vector "
            "FROM undx_semantic_index i "
            "JOIN undx_embedding_cache c ON c.cache_key = i.cache_key "
            "WHERE i.corpus = ? AND c.model = ? AND c.model_version = ? AND c.dimensions = ? "
            "ORDER BY i.doc_id",
            (corpus, embed.configured_model(), embed.configured_model_version(), dimensions),
        )
        for row in cursor.fetchall():
            try:
                vector = embed.decode_vector(row[3])
            except Exception:
                continue
            if len(vector) != dimensions:
                continue
            doc_ids.append(str(row[0]))
            titles.append(str(row[1] or ""))
            bodies.append(str(row[2] or ""))
            vectors.append(tuple(vector))
    except Exception as exc:
        logger.warning("undx_semantic: index read failed (%s)", type(exc).__name__)
        return None
    finally:
        try:
            connection.close()
        except Exception:
            pass

    if not vectors:
        return None
    loaded = _LoadedIndex(
        fingerprint=fingerprint,
        doc_ids=tuple(doc_ids),
        titles=tuple(titles),
        bodies=tuple(bodies),
        vectors=tuple(vectors),
    )
    with _INDEX_LOCK:
        _index_cache[corpus] = loaded
    return loaded


def index_status(corpus: str = CANONICAL_CORPUS) -> dict[str, Any]:
    loaded = load_index(corpus)
    return {
        "corpus": corpus,
        "documents_indexed": len(loaded.vectors) if loaded else 0,
        "dimensions": embed.configured_dimensions(),
        "model": embed.configured_model(),
        "model_version": embed.configured_model_version(),
        "loaded": loaded is not None,
    }


# --------------------------------------------------------------------------- retrieval


@dataclass(frozen=True)
class Candidate:
    doc_id: str
    title: str
    body: str
    score: float
    source: str


def _query_vector(query: str) -> list[float] | None:
    """Embed the query, using the cache first. Returns ``None`` to mean "fall back".

    Query embeddings are cached for the same reason document embeddings are: the same
    handful of questions are asked constantly, and a repeated question should not be a
    repeated invoice. The cache is checked before the provider and written after it.
    """
    key = embed.cache_key(
        query,
        model=embed.configured_model(),
        model_version=embed.configured_model_version(),
        dimensions=embed.configured_dimensions(),
    )
    connection = None
    try:
        connection = _connect()
        ensure_schema(connection)
        hit = cache_get(connection, [key]).get(key)
        if hit is not None:
            embed.record_counter("embedding_cache_hits")
            return hit
        embed.record_counter("embedding_cache_misses")
    except Exception as exc:
        logger.warning("undx_semantic: query cache unavailable (%s)", type(exc).__name__)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    try:
        vector = list(embed.embed_one(query, purpose="semantic_query"))
    except embed.EmbeddingUnavailable as exc:
        logger.info("undx_semantic: falling back to lexical (%s)", exc.reason)
        return None
    except Exception as exc:  # defence in depth: this path may never raise upward
        logger.warning("undx_semantic: unexpected embedding failure (%s)", type(exc).__name__)
        return None

    try:
        connection = _connect()
        ensure_schema(connection)
        cache_put(connection, key, vector)
        connection.commit()
    except Exception:
        # A cache write failure costs money, not correctness.
        logger.warning("undx_semantic: query cache write failed")
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass
    return vector


def semantic_candidates(query: str, *, limit: int = 12, corpus: str = CANONICAL_CORPUS) -> list[Candidate]:
    """Vector retrieval. Returns ``[]`` for every failure — this never raises."""
    started = time.perf_counter()
    loaded = load_index(corpus)
    if loaded is None:
        return []
    vector = _query_vector(query)
    if vector is None or len(vector) != len(loaded.vectors[0]):
        return []

    # Both sides are already L2-normalised at write time, so the dot product *is* the
    # cosine similarity. Perplexity returns unnormalised vectors and its documentation
    # is explicit that dot product is not a valid similarity for them; normalising once
    # is what makes this loop both correct and cheap.
    floor = similarity_floor()
    scored: list[tuple[float, int]] = []
    for index, candidate in enumerate(loaded.vectors):
        total = 0.0
        for a, b in zip(vector, candidate):
            total += a * b
        if total >= floor:
            scored.append((total, index))
    scored.sort(key=lambda row: (-row[0], loaded.doc_ids[row[1]]))
    embed.record_latency("semantic_retrieval_latency_ms", (time.perf_counter() - started) * 1000)
    return [
        Candidate(
            doc_id=loaded.doc_ids[index],
            title=loaded.titles[index],
            body=loaded.bodies[index],
            score=round(score, 6),
            source="semantic",
        )
        for score, index in scored[: max(1, int(limit))]
    ]


def lexical_candidates(query: str, *, limit: int = 12) -> list[Candidate]:
    """The existing baseline, expressed as candidates so the two can be fused.

    Calls ``undx_platform_knowledge.retrieve`` rather than reimplementing its scoring,
    so the baseline measured by the benchmark is the baseline that runs in production.
    """
    results = lexical.retrieve(query, limit=min(int(limit), lexical.MAX_RESULTS))
    return [
        Candidate(
            doc_id=f"lexical:{position}:{item.get('title', '')}",
            title=str(item.get("title") or ""),
            body=str(item.get("body") or ""),
            score=float(len(results) - position),
            source="lexical",
        )
        for position, item in enumerate(results)
    ]


def _fuse(semantic: Sequence[Candidate], lexical_hits: Sequence[Candidate]) -> list[Candidate]:
    """Reciprocal rank fusion.

    Chosen over a weighted score blend because term-overlap counts and cosine
    similarities are not on a comparable scale, and any weight between them would be a
    constant invented in this file with no evidence behind it. RRF needs only the
    rankings, which both sides genuinely produce.
    """
    ranked: dict[str, tuple[float, Candidate]] = {}
    for group in (semantic, lexical_hits):
        for position, candidate in enumerate(group):
            # Titles are the only identity the two sides share: the lexical path returns
            # rendered prompt items, not manifest ids.
            identity = candidate.title.strip().lower() or candidate.doc_id
            contribution = 1.0 / (RRF_K + position + 1)
            score, existing = ranked.get(identity, (0.0, candidate))
            preferred = existing
            if existing.source == "semantic" and candidate.source == "lexical":
                # Prefer the lexical rendering when both sides found it, because that is
                # the text the current production prompt already contains.
                preferred = candidate
            ranked[identity] = (score + contribution, preferred)
    fused = sorted(ranked.values(), key=lambda row: (-row[0], row[1].title))
    return [
        Candidate(
            doc_id=candidate.doc_id,
            title=candidate.title,
            body=candidate.body,
            score=round(score, 6),
            source="hybrid",
        )
        for score, candidate in fused
    ]


_SOURCE_PATH_PATTERN = re.compile(r"\b[\w/.\-]+\.(?:py|ts|tsx|js|jsx|sql|yaml|yml|json)\b")


def _authority_filter(
    candidates: Sequence[Candidate], *, limit: int, char_limit: int
) -> list[dict[str, Any]]:
    """Grounding and authority gate: the last thing between retrieval and a prompt.

    Four jobs, all of them subtractive:

    * bound the output, in items and in characters, so retrieval can never become the
      prompt;
    * strip anything shaped like a source path or a schema fragment, matching the
      guarantee ``undx_platform_knowledge`` already makes and the tests that assert it;
    * emit the same four keys the lexical path emits, so no downstream consumer gains a
      new field it might read as confidence;
    * drop the similarity score entirely. A number that says "0.94" travelling beside a
      claim is an invitation to treat the claim as verified, and this module's whole
      contract is that it verifies nothing.
    """
    results: list[dict[str, Any]] = []
    used = 0
    ceiling = max(300, min(int(char_limit), lexical.MAX_CONTEXT_CHARS))
    for candidate in candidates[: max(1, min(int(limit), lexical.MAX_RESULTS))]:
        title = _SOURCE_PATH_PATTERN.sub("", candidate.title).strip()[:160]
        body = " ".join(_SOURCE_PATH_PATTERN.sub("", candidate.body).split())[:600]
        if not title or not body:
            continue
        if used + len(title) + len(body) > ceiling:
            continue
        results.append({
            "id": 0,
            "title": title,
            "category": "source_derived_platform_knowledge",
            "body": body,
        })
        used += len(title) + len(body)
    return results


@dataclass(frozen=True)
class RetrievalDiagnostics:
    """What happened, for telemetry and the benchmark. Never reaches a prompt."""

    stage: str = STAGE_OFF
    served: str = "lexical"
    semantic_count: int = 0
    lexical_count: int = 0
    fused_count: int = 0
    fell_back: bool = False
    latency_ms: int = 0
    authority: str = AUTHORITY


def retrieve_with_diagnostics(
    query: str,
    *,
    user_id: int | None = None,
    limit: int = lexical.MAX_RESULTS,
    char_limit: int = lexical.MAX_CONTEXT_CHARS,
) -> tuple[list[dict[str, Any]], RetrievalDiagnostics]:
    """The whole decision, in one place, with the reason it went the way it did."""
    started = time.perf_counter()
    current = stage()
    lexical_hits = lexical_candidates(query, limit=max(limit, 6))
    lexical_results = lexical.retrieve(query, limit=limit, char_limit=char_limit)

    def _finish(results: list[dict[str, Any]], **fields: Any) -> tuple[list[dict[str, Any]], RetrievalDiagnostics]:
        diagnostics = RetrievalDiagnostics(
            stage=current,
            lexical_count=len(lexical_hits),
            latency_ms=int((time.perf_counter() - started) * 1000),
            **fields,
        )
        return results, diagnostics

    if current == STAGE_OFF:
        return _finish(lexical_results, served="lexical")

    semantic = []
    try:
        semantic = semantic_candidates(query, limit=max(limit, 12))
    except Exception as exc:  # the fallback must survive a bug in this module too
        logger.warning("undx_semantic: retrieval error, using lexical (%s)", type(exc).__name__)

    if not semantic:
        embed.record_counter("semantic_fallback_count")
        return _finish(lexical_results, served="lexical", fell_back=True)

    if current == STAGE_SHADOW:
        # Compute, compare, discard. The user gets exactly what they get today.
        fused = _fuse(semantic, lexical_hits)
        logger.info(
            "undx_semantic shadow: semantic=%d lexical=%d fused=%d overlap=%d",
            len(semantic), len(lexical_hits), len(fused),
            len({c.title.strip().lower() for c in semantic}
                & {c.title.strip().lower() for c in lexical_hits}),
        )
        return _finish(
            lexical_results, served="lexical", semantic_count=len(semantic), fused_count=len(fused)
        )

    if not user_in_scope(user_id):
        return _finish(lexical_results, served="lexical", semantic_count=len(semantic))

    fused = _fuse(semantic, lexical_hits)
    results = _authority_filter(fused, limit=limit, char_limit=char_limit)
    if not results:
        embed.record_counter("semantic_fallback_count")
        return _finish(lexical_results, served="lexical", fell_back=True, semantic_count=len(semantic))
    return _finish(
        results, served="hybrid", semantic_count=len(semantic), fused_count=len(fused)
    )


def retrieve(
    query: str,
    *,
    user_id: int | None = None,
    limit: int = lexical.MAX_RESULTS,
    char_limit: int = lexical.MAX_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Drop-in replacement for ``undx_platform_knowledge.retrieve``. Never raises.

    Identical return shape, identical bounds, and identical behaviour whenever the stage
    is off or the provider is unavailable — which is every deployment until an operator
    changes a variable.
    """
    try:
        results, _ = retrieve_with_diagnostics(
            query, user_id=user_id, limit=limit, char_limit=char_limit
        )
        return results
    except Exception as exc:
        # The contract is that this function cannot be the reason a request fails.
        logger.warning("undx_semantic: retrieve failed closed to lexical (%s)", type(exc).__name__)
        try:
            return lexical.retrieve(query, limit=limit, char_limit=char_limit)
        except Exception:
            return []


def health() -> dict[str, Any]:
    """Operator-facing status. Contains no key, no content and no user identifier."""
    return {
        "stage": stage(),
        "similarity_floor": similarity_floor(),
        "authority": AUTHORITY,
        "provider": embed.describe_for_report(),
        "budget": embed.budget_state(),
        "index": index_status(),
        "telemetry": embed.telemetry_snapshot(),
    }
