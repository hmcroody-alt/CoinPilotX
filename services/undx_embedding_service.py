"""The embedding provider edge: one HTTP call, one cache, one budget, no authority.

This module is the *only* place in PulseSoc that talks to an embedding provider. It
exists as its own module for the same reason ``undx_router`` does — so that the answer
to "where does UNDX send text to a third party?" is a file, not a grep.

Three properties are load-bearing, and each of them is here because the alternative is
a specific failure rather than a style preference.

**It has no authority.** An embedding is a list of floats describing where a piece of
text sits relative to other text. It cannot name an account, authorise a write, or
assert that something is true. Nothing in this module returns a permission, an owner,
or a decision, and nothing downstream may treat a similarity score as one. The
authority boundary is enforced by shape: the only thing that leaves here is a vector.

**It fails open, downward.** Retrieval that degrades to the existing lexical matcher is
a slightly worse answer. Retrieval that raises is an outage in the assistant. Every
provider failure — timeout, 429, 5xx, malformed body, missing key — surfaces as
:class:`EmbeddingUnavailable`, which callers are expected to catch and fall back on.
The one thing this module will never do is guess a vector.

**It does not pay twice for the same sentence.** Canonical PulseSoc material changes
rarely and is re-read constantly. The cache key is derived from the content, the model,
the model version, the output dimensionality and the normalization version, so any
change to how text is prepared or how the vector is shaped produces a different key
instead of silently reusing a vector computed under different rules. A cache that
ignored the normalization version would be worse than no cache: it would serve vectors
from a superseded pipeline and there would be no symptom.

**Secrets.** ``PERPLEXITY_API_KEY`` is read from the environment at call time and put
into an ``Authorization`` header. It is never logged, never returned, never included in
telemetry, and never written to the database. :func:`describe_for_report` reports
whether it is set and nothing else. Embedded *content* is also never logged — log lines
carry counts, hashes and byte lengths, because a debug log full of user-adjacent text is
a data-retention problem that arrives without anybody deciding to create one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import struct
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- provider

#: Verified against Perplexity's official embeddings documentation. Kept as a module
#: constant rather than inlined so that the one place a base URL could be pointed at a
#: proxy is visible.
DEFAULT_ENDPOINT = "https://api.perplexity.ai/v1/embeddings"

#: The model the owner authorised. Substituting another model silently would change
#: both the cost per token and the vector space — vectors from two models are not
#: comparable, so a silent substitution corrupts an index without failing anything.
#: Selection is configurable through ``UNDX_EMBEDDING_MODEL``; it is not hardcoded at
#: call sites, and the model name is part of every cache key.
DEFAULT_MODEL = "pplx-embed-v1-0.6b"

API_KEY_ENV = "PERPLEXITY_API_KEY"

#: Published per-million-token prices, read from Perplexity's official pricing
#: documentation on 2026-08-30. Used only for local cost *accounting* — this module
#: never asserts these are current, and :func:`estimated_cost_usd` labels its output as
#: an estimate. A model absent from this table accounts at the most expensive known
#: rate rather than at zero, so an unrecognised model cannot look free.
PRICE_PER_MILLION_TOKENS_USD: dict[str, float] = {
    "pplx-embed-v1-0.6b": 0.004,
    "pplx-embed-v1-4b": 0.03,
    "pplx-embed-context-v1-0.6b": 0.008,
    "pplx-embed-context-v1-4b": 0.05,
}
_UNKNOWN_MODEL_PRICE_USD = max(PRICE_PER_MILLION_TOKENS_USD.values())

#: Provider request bounds, from the official documentation: at most 512 inputs per
#: request, at most 32,000 tokens in any one input, at most 120,000 tokens across the
#: whole request. Batching respects all three; the token figures are estimated, so the
#: batcher targets a fraction of the ceiling rather than the ceiling itself.
MAX_INPUTS_PER_REQUEST = 512
MAX_TOKENS_PER_INPUT = 32_000
MAX_TOKENS_PER_REQUEST = 120_000
_TOKEN_BUDGET_SAFETY = 0.8

#: Bumped whenever :func:`normalize_text` changes. Part of every cache key: a change to
#: text preparation invalidates the cache instead of quietly mixing pipelines.
NORMALIZATION_VERSION = "1"

#: Bumped whenever the stored vector encoding changes. Separate from the normalization
#: version because the two can move independently.
#:
#: "2" — 2026-08-30. Corrected to the real Perplexity wire contract. The provider returns
#: ``base64_int8`` (a base64 string decoding to signed int8 bytes), not a JSON array of
#: floats; the decode-then-L2-normalise path is a different pipeline from the one "1"
#: described, so it gets a different cache namespace.
ENCODING_VERSION = "2"

#: Output encoding requested from the provider. Perplexity's ``encoding_format`` accepts
#: only ``base64_int8`` and ``base64_binary`` — **there is no float option**. int8 is the
#: documented default and the one that is compared with cosine similarity; ``base64_binary``
#: packs one bit per dimension and requires Hamming distance, which is not what the
#: retrieval scorer does. Sent explicitly so a change to the provider's default cannot
#: silently swap the vector space underneath a stored index.
EMBEDDING_ENCODING_FORMAT = "base64_int8"


class EmbeddingUnavailable(RuntimeError):
    """The provider could not be reached, or answered with something unusable.

    Always recoverable by the caller. Raising this is how the module says "fall back",
    and every raise site is a place where the honest answer is a lexical result rather
    than a guess.
    """

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


# ------------------------------------------------------------------------ text policy

#: Content classes that must never be sent to an embedding provider, named here so the
#: prohibition is checkable rather than a paragraph in a report. The indexer refuses a
#: document whose declared class is in this set; the refusal is a hard error rather
#: than a skip, because a corpus that silently drops private material and a corpus that
#: silently includes it look identical from the outside.
FORBIDDEN_CONTENT_CLASSES: frozenset[str] = frozenset({
    "private_message",
    "direct_message",
    "credential",
    "secret",
    "api_key",
    "password",
    "session_token",
    "payment_information",
    "card_number",
    "bank_detail",
    "authentication_secret",
    "support_conversation",
    "precise_location",
    "security_sensitive_account_information",
    "private_user_data",
})


def normalize_text(text: str) -> str:
    """Deterministic preparation applied before hashing and before embedding.

    Unicode NFKC plus whitespace collapse plus a hard length clamp. The clamp is not
    cosmetic: a single pathological input above the provider's 32,000-token ceiling
    fails the whole batch it lands in, so it is truncated here where the truncation is
    visible and versioned rather than at the provider where it is a 400.
    """
    collapsed = " ".join(unicodedata.normalize("NFKC", str(text or "")).split())
    # Four characters per token is a deliberate over-estimate of English density and a
    # safe one for the languages UNDX serves; the point is a bound, not an accurate
    # tokenizer.
    return collapsed[: MAX_TOKENS_PER_INPUT * 4]


def estimate_tokens(text: str) -> int:
    """A conservative token estimate. Over-estimates rather than under-estimates.

    Used for batching and for cost accounting, never for billing. Perplexity bills on
    its own count; this number exists so that a runaway indexing loop trips the budget
    guard before it trips the invoice.
    """
    return max(1, (len(str(text or "")) + 3) // 4)


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def cache_key(text: str, *, model: str, model_version: str, dimensions: int) -> str:
    """Deterministic cache identity.

    Every component that could change the resulting vector is in the key. Getting this
    wrong is the failure this whole module is shaped to avoid: a cache keyed on content
    alone returns a 256-dimension vector from an old model to a caller that asked for a
    1024-dimension vector from a new one, cosine similarity still returns a number, and
    retrieval quietly degrades with no error anywhere.
    """
    material = "|".join((
        "undx-embed",
        ENCODING_VERSION,
        NORMALIZATION_VERSION,
        str(model or ""),
        str(model_version or ""),
        str(int(dimensions or 0)),
        content_hash(text),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# -------------------------------------------------------------------------- telemetry

_TELEMETRY_LOCK = threading.Lock()

#: Bounded by construction: a fixed set of integer counters plus two latency reservoirs
#: capped at a fixed length. There is no per-query, per-user or per-document key here,
#: so telemetry cannot grow with traffic and cannot come to hold content.
_COUNTER_NAMES: tuple[str, ...] = (
    "embedding_requests",
    "embedding_cache_hits",
    "embedding_cache_misses",
    "embedding_provider_errors",
    "embedding_429",
    "embedding_timeouts",
    "embedding_budget_blocks",
    "embedding_texts_embedded",
    "embedding_tokens_embedded",
    "semantic_fallback_count",
)
_LATENCY_RESERVOIR_SIZE = 512

_counters: dict[str, int] = {name: 0 for name in _COUNTER_NAMES}
_latencies: dict[str, list[int]] = {"embedding_latency_ms": [], "semantic_retrieval_latency_ms": []}


def record_counter(name: str, amount: int = 1) -> None:
    if name not in _counters:
        return
    with _TELEMETRY_LOCK:
        _counters[name] += int(amount)


def record_latency(name: str, milliseconds: float) -> None:
    if name not in _latencies:
        return
    with _TELEMETRY_LOCK:
        bucket = _latencies[name]
        bucket.append(int(milliseconds))
        if len(bucket) > _LATENCY_RESERVOIR_SIZE:
            del bucket[: len(bucket) - _LATENCY_RESERVOIR_SIZE]


def _percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return int(ordered[index])


def telemetry_snapshot() -> dict[str, Any]:
    """Counters, latency percentiles and derived rates. Contains no content and no key."""
    with _TELEMETRY_LOCK:
        counters = dict(_counters)
        latencies = {name: list(values) for name, values in _latencies.items()}
    hits = counters["embedding_cache_hits"]
    misses = counters["embedding_cache_misses"]
    looked_up = hits + misses
    snapshot: dict[str, Any] = dict(counters)
    snapshot["embedding_cache_hit_rate"] = round(hits / looked_up, 4) if looked_up else 0.0
    for name, values in latencies.items():
        snapshot[f"{name}_p50"] = _percentile(values, 0.50)
        snapshot[f"{name}_p95"] = _percentile(values, 0.95)
        snapshot[f"{name}_samples"] = len(values)
    snapshot["estimated_cost_usd"] = estimated_cost_usd(
        counters["embedding_tokens_embedded"], model=configured_model()
    )
    return snapshot


def reset_telemetry() -> None:
    """Test-only. Production never resets; the process lifetime is the window."""
    with _TELEMETRY_LOCK:
        for name in _counters:
            _counters[name] = 0
        for values in _latencies.values():
            values.clear()


# ------------------------------------------------------------------------ configuration


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("undx_embedding: %s=%r is not an integer; using %s", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("undx_embedding: %s=%r is not a number; using %s", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def configured_model() -> str:
    return _env("UNDX_EMBEDDING_MODEL") or DEFAULT_MODEL


def configured_model_version() -> str:
    """An operator-set label that participates in the cache key.

    Perplexity does not currently expose a build identifier on the embeddings response,
    so there is no way to detect a silent model refresh from the wire. This variable is
    the manual lever: bump it and the entire cache is invalidated. It exists because the
    alternative — trusting that a model name pins a vector space forever — is a
    correctness assumption nobody can verify.
    """
    return _env("UNDX_EMBEDDING_MODEL_VERSION") or "1"


def configured_dimensions() -> int:
    """Output dimensionality, defaulting to 256 via Matryoshka truncation.

    256 is chosen from measurement rather than taste: brute-force cosine over the 1,673
    canonical manifest entries costs about 6 ms at 256 dimensions and about 25 ms at
    1024, and the retrieval it feeds sits in a request that is already calling a model
    provider. Raise it if the benchmark shows recall paying for the latency; the flag
    is here so that is a configuration change and not a code change.
    """
    return _env_int("UNDX_EMBEDDING_DIMENSIONS", 256, minimum=32, maximum=4096)


def configured_endpoint() -> str:
    return _env("UNDX_EMBEDDING_ENDPOINT") or DEFAULT_ENDPOINT


def configured_timeout_seconds() -> float:
    # Whole seconds, matching the catalog declaration. A float here and an int in the
    # catalog would make the operator dashboard report a timeout the code is not using.
    return float(_env_int("UNDX_EMBEDDING_TIMEOUT_SECONDS", 8, minimum=1, maximum=60))


def configured_max_retries() -> int:
    """Bounded retry count for 429 and 5xx. Zero is a legitimate production setting."""
    return _env_int("UNDX_EMBEDDING_MAX_RETRIES", 2, minimum=0, maximum=5)


def configured_monthly_budget_usd() -> float:
    """Cost guard. Zero disables the guard; the default does not.

    A runaway indexing loop is the failure this stops. The guard is checked before every
    provider call and compares against tokens billed in the current calendar month as
    recorded locally, so it is an approximation — deliberately a conservative one, since
    the estimator over-counts tokens.
    """
    return _env_float("UNDX_EMBEDDING_MONTHLY_BUDGET_USD", 5.0, minimum=0.0, maximum=100_000.0)


def api_key_configured() -> bool:
    return bool(_env(API_KEY_ENV))


def estimated_cost_usd(tokens: int, *, model: str | None = None) -> float:
    rate = PRICE_PER_MILLION_TOKENS_USD.get(model or configured_model(), _UNKNOWN_MODEL_PRICE_USD)
    return round(max(0, int(tokens)) * rate / 1_000_000, 6)


def describe_for_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Operator-facing configuration state. Structurally incapable of printing the key."""
    source = os.environ if env is None else env
    return {
        "model": configured_model(),
        "model_version": configured_model_version(),
        "dimensions": configured_dimensions(),
        "endpoint": configured_endpoint(),
        "timeout_seconds": configured_timeout_seconds(),
        "max_retries": configured_max_retries(),
        "monthly_budget_usd": configured_monthly_budget_usd(),
        "normalization_version": NORMALIZATION_VERSION,
        "encoding_version": ENCODING_VERSION,
        "api_key": "set" if str(source.get(API_KEY_ENV, "") or "").strip() else "unset",
        "price_per_million_tokens_usd": PRICE_PER_MILLION_TOKENS_USD.get(
            configured_model(), _UNKNOWN_MODEL_PRICE_USD
        ),
    }


# ------------------------------------------------------------------- vector encoding


def encode_vector(values: Sequence[float]) -> str:
    """float32 little-endian, base64. Stored as TEXT.

    TEXT rather than BLOB/bytea because the same statement has to run against SQLite
    locally and PostgreSQL in production through this repository's compatibility layer,
    and binary parameter adaptation is the one place those two disagree in a way that
    only shows up in production.
    """
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode("ascii")


def decode_vector(encoded: str) -> list[float]:
    raw = base64.b64decode(str(encoded or "").encode("ascii"), validate=True)
    if len(raw) % 4:
        raise ValueError("encoded vector length is not a multiple of 4 bytes")
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def l2_norm(values: Iterable[float]) -> float:
    total = 0.0
    for value in values:
        total += float(value) * float(value)
    return total ** 0.5


def unit_vector(values: Sequence[float]) -> list[float]:
    """Return the L2-normalised vector.

    Perplexity returns *unnormalised* embeddings and its documentation says so
    explicitly: similarity must be cosine, not dot product. Normalising once at index
    time and once per query turns every subsequent comparison into a plain dot product,
    which is both correct and the reason a pure-Python scan is fast enough to avoid
    buying a vector database. A zero vector normalises to itself rather than raising —
    it scores zero against everything, which is the right answer for empty input.
    """
    norm = l2_norm(values)
    if norm <= 0.0:
        return [0.0] * len(values)
    return [float(value) / norm for value in values]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity over unnormalised inputs. Prefer pre-normalised dot products."""
    if len(left) != len(right):
        return 0.0
    denominator = l2_norm(left) * l2_norm(right)
    if denominator <= 0.0:
        return 0.0
    return sum(float(a) * float(b) for a, b in zip(left, right)) / denominator


# ------------------------------------------------------------------------ budget guard

_BUDGET_LOCK = threading.Lock()
_budget_state: dict[str, Any] = {"month": "", "tokens": 0}


def _current_month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def budget_state() -> dict[str, Any]:
    with _BUDGET_LOCK:
        month = _budget_state["month"] or _current_month()
        tokens = int(_budget_state["tokens"])
    limit = configured_monthly_budget_usd()
    spent = estimated_cost_usd(tokens)
    return {
        "month": month,
        "tokens_embedded": tokens,
        "estimated_spend_usd": spent,
        "budget_usd": limit,
        "remaining_usd": round(limit - spent, 6) if limit > 0 else None,
        "enforced": limit > 0,
    }


def _budget_would_exceed(tokens: int) -> bool:
    limit = configured_monthly_budget_usd()
    if limit <= 0:
        return False
    with _BUDGET_LOCK:
        month = _current_month()
        if _budget_state["month"] != month:
            _budget_state["month"] = month
            _budget_state["tokens"] = 0
        projected = int(_budget_state["tokens"]) + int(tokens)
    return estimated_cost_usd(projected) > limit


def _budget_record(tokens: int) -> None:
    with _BUDGET_LOCK:
        month = _current_month()
        if _budget_state["month"] != month:
            _budget_state["month"] = month
            _budget_state["tokens"] = 0
        _budget_state["tokens"] = int(_budget_state["tokens"]) + int(tokens)


def reset_budget() -> None:
    """Test-only."""
    with _BUDGET_LOCK:
        _budget_state["month"] = ""
        _budget_state["tokens"] = 0


# ------------------------------------------------------------------------- the client


@dataclass(frozen=True)
class EmbeddingBatch:
    """One provider response, already normalised and paired back to its inputs."""

    vectors: tuple[tuple[float, ...], ...] = ()
    model: str = ""
    dimensions: int = 0
    tokens: int = 0
    latency_ms: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _batches(texts: Sequence[str]) -> list[list[int]]:
    """Split indices into provider-legal batches on both the count and token ceilings."""
    out: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0
    ceiling = int(MAX_TOKENS_PER_REQUEST * _TOKEN_BUDGET_SAFETY)
    for index, text in enumerate(texts):
        tokens = estimate_tokens(text)
        too_many = len(current) >= MAX_INPUTS_PER_REQUEST
        too_big = current and (current_tokens + tokens) > ceiling
        if too_many or too_big:
            out.append(current)
            current, current_tokens = [], 0
        current.append(index)
        current_tokens += tokens
    if current:
        out.append(current)
    return out


def _post(payload: dict[str, Any], *, api_key: str, timeout: float) -> dict[str, Any]:
    """One HTTP round trip. Raises :class:`EmbeddingUnavailable` for everything.

    ``requests`` is imported inside the function so that importing this module on a
    machine without it — a test runner, a schema-only script — does not fail. The
    provider path is the only thing that needs it.
    """
    try:
        import requests  # noqa: PLC0415 - deliberate: keep import off the module path
    except Exception as exc:  # pragma: no cover - requests is in requirements.txt
        raise EmbeddingUnavailable(f"http client unavailable: {type(exc).__name__}") from exc

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(
            configured_endpoint(), headers=headers, data=json.dumps(payload), timeout=timeout
        )
    except Exception as exc:
        name = type(exc).__name__
        if "Timeout" in name:
            record_counter("embedding_timeouts")
            raise EmbeddingUnavailable(f"provider timeout after {timeout}s", retryable=True) from exc
        raise EmbeddingUnavailable(f"provider transport error: {name}", retryable=True) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        record_counter("embedding_429")
        raise EmbeddingUnavailable("provider rate limited (429)", retryable=True)
    if 500 <= status < 600:
        raise EmbeddingUnavailable(f"provider server error ({status})", retryable=True)
    if status == 401 or status == 403:
        # Not retryable and deliberately non-specific. The key is wrong or revoked; the
        # response body may echo material we do not want in a log line.
        raise EmbeddingUnavailable("provider rejected the credential", retryable=False)
    if status != 200:
        raise EmbeddingUnavailable(f"provider returned {status}", retryable=False)
    try:
        body = response.json()
    except Exception as exc:
        raise EmbeddingUnavailable("provider returned a non-JSON body", retryable=False) from exc
    if not isinstance(body, dict):
        raise EmbeddingUnavailable("provider returned a non-object body", retryable=False)
    return body


def _decode_embedding(raw: Any) -> list[float]:
    """Turn one ``data[].embedding`` value into a list of floats.

    Perplexity's documented wire format is ``base64_int8``: the field is a **base64
    string** that decodes to exactly ``dimensions`` signed int8 bytes. It is not a JSON
    array of floats. Assuming the OpenAI float shape is what made the live probe fail
    with "provider returned an empty vector" — the string is not a ``list``, so the
    old shape check rejected a perfectly good HTTP 200.

    A JSON array is still accepted, because a proxy or a future ``encoding_format`` may
    speak floats and there is no reason to break on a response that is easier to read
    than the one we asked for.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise EmbeddingUnavailable("provider returned an empty vector", retryable=False)
        try:
            decoded = base64.b64decode(text, validate=True)
        except Exception as exc:
            raise EmbeddingUnavailable(
                "provider returned a vector that is not valid base64", retryable=False
            ) from exc
        if not decoded:
            raise EmbeddingUnavailable("provider returned an empty vector", retryable=False)
        # "b" is signed char. int8 is always finite, so no isfinite check is needed here.
        return [float(value) for value in struct.unpack(f"{len(decoded)}b", decoded)]

    if isinstance(raw, list):
        if not raw:
            raise EmbeddingUnavailable("provider returned an empty vector", retryable=False)
        try:
            values = [float(value) for value in raw]
        except (TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("provider returned a non-numeric vector", retryable=False) from exc
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingUnavailable(
                "provider returned a non-finite vector component", retryable=False
            )
        return values

    raise EmbeddingUnavailable(
        f"provider returned a vector of unsupported type {type(raw).__name__}", retryable=False
    )


def _parse(body: dict[str, Any], *, expected: int, dimensions: int) -> tuple[list[list[float]], int]:
    """Pull vectors out of an OpenAI-shaped embeddings response, defensively.

    Every shape assumption is checked. A response that is *almost* right — the correct
    number of objects but one of them missing its ``embedding`` — must raise rather than
    yield a short list, because a short list silently misaligns vectors against the
    documents they were computed for, and the resulting index is wrong in a way that
    looks like poor model quality.
    """
    data = body.get("data")
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingUnavailable(
            f"provider returned {len(data) if isinstance(data, list) else 'no'} vectors "
            f"for {expected} inputs",
            retryable=False,
        )
    vectors: list[list[float]] = []
    for item in sorted(data, key=lambda row: int((row or {}).get("index", 0)) if isinstance(row, dict) else 0):
        if not isinstance(item, dict):
            raise EmbeddingUnavailable("provider returned a malformed vector entry", retryable=False)
        if "embedding" not in item:
            raise EmbeddingUnavailable("provider returned an entry with no embedding", retryable=False)
        values = _decode_embedding(item.get("embedding"))
        if len(values) != dimensions:
            raise EmbeddingUnavailable(
                f"provider returned {len(values)} dimensions, expected {dimensions}", retryable=False
            )
        vectors.append(values)
    usage = body.get("usage")
    tokens = 0
    if isinstance(usage, dict):
        for key in ("total_tokens", "prompt_tokens", "input_tokens"):
            candidate = usage.get(key)
            if isinstance(candidate, int) and candidate > 0:
                tokens = candidate
                break
    return vectors, tokens


def embed_texts(texts: Sequence[str], *, purpose: str = "unspecified") -> EmbeddingBatch:
    """Embed a list of texts. Returns L2-normalised vectors in input order.

    This is the raw provider call with no cache — :mod:`services.undx_semantic_retrieval`
    owns the cache. Kept separate so the cache can be tested without a network and the
    network can be tested without a database.

    Raises :class:`EmbeddingUnavailable` on every failure, including a missing key. A
    missing key is deliberately the same class of event as an outage: both mean "there
    is no semantic retrieval right now", and both are answered by the lexical fallback.
    """
    prepared = [normalize_text(text) for text in texts]
    prepared = [text for text in prepared if text]
    if not prepared:
        return EmbeddingBatch(vectors=(), model=configured_model(), dimensions=configured_dimensions())

    api_key = _env(API_KEY_ENV)
    if not api_key:
        raise EmbeddingUnavailable("PERPLEXITY_API_KEY is not configured", retryable=False)

    model = configured_model()
    dimensions = configured_dimensions()
    timeout = configured_timeout_seconds()
    retries = configured_max_retries()

    projected = sum(estimate_tokens(text) for text in prepared)
    if _budget_would_exceed(projected):
        record_counter("embedding_budget_blocks")
        raise EmbeddingUnavailable(
            f"monthly embedding budget of ${configured_monthly_budget_usd():.2f} would be exceeded",
            retryable=False,
        )

    all_vectors: list[list[float]] = [None] * len(prepared)  # type: ignore[list-item]
    total_tokens = 0
    total_latency = 0
    notes: list[str] = []

    for indices in _batches(prepared):
        payload = {
            "model": model,
            "input": [prepared[index] for index in indices],
            "dimensions": dimensions,
            "encoding_format": EMBEDDING_ENCODING_FORMAT,
        }
        attempt = 0
        while True:
            started = time.perf_counter()
            record_counter("embedding_requests")
            try:
                body = _post(payload, api_key=api_key, timeout=timeout)
                vectors, tokens = _parse(body, expected=len(indices), dimensions=dimensions)
            except EmbeddingUnavailable as exc:
                elapsed = int((time.perf_counter() - started) * 1000)
                record_latency("embedding_latency_ms", elapsed)
                record_counter("embedding_provider_errors")
                if exc.retryable and attempt < retries:
                    attempt += 1
                    # Bounded exponential backoff. Capped low because this sits inside a
                    # user-facing request: the fallback answer is better than a slow one.
                    time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
                    notes.append(f"retry {attempt} after {exc.reason}")
                    continue
                logger.warning(
                    "undx_embedding: provider unavailable purpose=%s inputs=%d reason=%s",
                    purpose, len(indices), exc.reason,
                )
                raise
            elapsed = int((time.perf_counter() - started) * 1000)
            record_latency("embedding_latency_ms", elapsed)
            total_latency += elapsed
            billed = tokens or sum(estimate_tokens(prepared[index]) for index in indices)
            total_tokens += billed
            _budget_record(billed)
            record_counter("embedding_texts_embedded", len(indices))
            record_counter("embedding_tokens_embedded", billed)
            for position, index in enumerate(indices):
                all_vectors[index] = unit_vector(vectors[position])
            break

    return EmbeddingBatch(
        vectors=tuple(tuple(vector) for vector in all_vectors),
        model=model,
        dimensions=dimensions,
        tokens=total_tokens,
        latency_ms=total_latency,
        notes=tuple(notes),
    )


def embed_one(text: str, *, purpose: str = "query") -> tuple[float, ...]:
    """Convenience wrapper for the single-query path."""
    batch = embed_texts([text], purpose=purpose)
    if not batch.vectors:
        raise EmbeddingUnavailable("nothing to embed", retryable=False)
    return batch.vectors[0]
