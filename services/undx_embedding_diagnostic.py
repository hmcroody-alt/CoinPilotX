"""One bounded question, asked from production: does the embedding provider work?

The wire-contract fix (``base64_int8`` decoding, commit a69579ab) is verified offline by
:mod:`tests.undx_agent.test_embedding_wire_contract` against a recorded response. That
proves the decoder matches the documentation. It cannot prove the documentation matches
the provider, and the failure this whole line of work started from was exactly that gap:
an HTTP 200 that the old decoder rejected as an empty vector.

Closing the gap needs one real request from the deployed network. Every route to making
that happen was circular or unsafe:

* enabling ``UNDX_SEMANTIC_RETRIEVAL_STAGE=shadow`` to trigger a call is the thing the
  probe is supposed to gate, so it cannot also be the probe;
* the only in-process consumer is :mod:`services.pulse_ai_service`, reached through user
  conversation traffic and gated by that same flag;
* a public HTTP route would turn a one-time acceptance question into a permanent piece of
  product surface that spends money per request.

So this module is the whole diagnostic: a single short embedding, executed on demand,
returning bounded metadata. It is not wired into any request path. It changes no state, is
not called by the retrieval code, and does nothing at all unless an operator sets a flag.

**What it may return, exhaustively:** status, model, endpoint host, requested and observed
dimension, latency, vector norm, a small set of derived booleans, and an ``error_code``
drawn from a fixed enum. It has no code path that can emit vector components, the API key,
request headers, or any provider response body -- ``ERROR_CODES`` is a lookup over the
client's own sanitised classification, so a provider that echoed something sensitive in an
error string still could not get it into this output.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any
from urllib.parse import urlsplit

from . import undx_embedding_service as embed

logger = logging.getLogger(__name__)

#: Deliberately short and deliberately not user-derived: a fixed English phrase costs a
#: fraction of a cent and can never carry someone's content to a third party.
PROBE_TEXT = "PulseSoc notification preferences"

PROBE_ENV = "UNDX_EMBEDDING_PROBE"

#: Provider failures, collapsed to a closed vocabulary. The client's ``reason`` strings are
#: already sanitised, but mapping rather than forwarding means the output is bounded by
#: this table instead of by a promise about what the provider might say.
ERROR_CODES: tuple[tuple[str, str], ...] = (
    ("is not configured", "api_key_missing"),
    ("rejected the credential", "auth_rejected"),
    ("rate limited", "rate_limited"),
    ("server error", "provider_server_error"),
    ("timeout", "timeout"),
    ("transport error", "transport_error"),
    ("http client unavailable", "http_client_missing"),
    ("budget", "budget_blocked"),
    ("non-JSON body", "malformed_response"),
    ("non-object body", "malformed_response"),
    ("not valid base64", "decode_failed"),
    ("non-numeric", "decode_failed"),
    ("non-finite", "decode_failed"),
    ("unsupported type", "decode_failed"),
    ("empty vector", "empty_vector"),
    ("no embedding", "malformed_response"),
    ("malformed vector entry", "malformed_response"),
    ("dimensions, expected", "dimension_mismatch"),
    ("vectors for", "batch_size_mismatch"),
)


def classify(reason: str) -> str:
    """Map a client reason to a stable code. Unrecognised text becomes ``unknown_error``
    rather than being passed through -- the default must not be "echo what we were told"."""
    lowered = str(reason or "").lower()
    for needle, code in ERROR_CODES:
        if needle.lower() in lowered:
            return code
    return "unknown_error"


def _endpoint_host() -> str:
    """Host only. The full endpoint could carry a path or query an operator embedded."""
    try:
        return urlsplit(embed.configured_endpoint()).hostname or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def probe() -> dict[str, Any]:
    """One embedding request. Returns safe metadata; never raises.

    A PASS means all six acceptance conditions held at once: the request was accepted, the
    body decoded under the documented ``base64_int8`` contract, the vector was non-empty,
    every component was finite, the dimension matched the configured value, and the L2
    norm came back at unit length. Any one of them failing is a FAIL with a code, because
    a vector that is present but the wrong shape is not a working provider.
    """
    result: dict[str, Any] = {
        "probe": "undx_embedding",
        "model": embed.configured_model(),
        "model_version": embed.configured_model_version(),
        "endpoint_host": _endpoint_host(),
        "encoding_format": embed.EMBEDDING_ENCODING_FORMAT,
        "encoding_version": embed.ENCODING_VERSION,
        "requested_dimension": embed.configured_dimensions(),
        "api_key_present": embed.api_key_configured(),
        "dimension": None,
        "latency_ms": None,
        "norm": None,
        "error_code": "none",
    }
    if not result["api_key_present"]:
        return {**result, "status": "FAIL", "error_code": "api_key_missing"}

    started = time.perf_counter()
    try:
        vector = embed.embed_one(PROBE_TEXT, purpose="diagnostic_probe")
    except embed.EmbeddingUnavailable as exc:
        return {
            **result,
            "status": "FAIL",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_code": classify(exc.reason),
            "retryable": bool(exc.retryable),
        }
    except Exception as exc:  # noqa: BLE001 - a diagnostic that crashes tells you nothing
        logger.exception("undx_embedding_probe: unexpected failure")
        return {
            **result,
            "status": "FAIL",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            # The exception *type*, never its message: messages can quote a response body.
            "error_code": f"unexpected_{type(exc).__name__.lower()}",
        }

    latency = round((time.perf_counter() - started) * 1000, 1)
    dimension = len(vector)
    norm = embed.l2_norm(vector)
    finite = all(math.isfinite(value) for value in vector)
    checks = {
        "vector_non_empty": dimension > 0,
        "all_components_finite": finite,
        "dimension_matches_config": dimension == result["requested_dimension"],
        "unit_normalised": finite and abs(norm - 1.0) < 1e-6,
        # A constant vector decodes and normalises cleanly but carries no information;
        # a count of distinct components catches that without revealing any of them.
        "not_degenerate": len(set(vector)) > 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        **result,
        "status": "PASS" if not failed else "FAIL",
        "dimension": dimension,
        "latency_ms": latency,
        "norm": round(float(norm), 6) if finite else None,
        "distinct_components": len(set(vector)),
        **checks,
        "error_code": "none" if not failed else "vector_validation_failed",
        "failed_checks": failed,
    }


def run_startup_probe_if_enabled() -> dict[str, Any] | None:
    """Called once at worker start. Does nothing unless ``UNDX_EMBEDDING_PROBE`` is set.

    A one-shot hook rather than a scheduled check: this answers an acceptance question, and
    a diagnostic that re-ran every cycle would be a recurring charge and a recurring
    dependency on a third party at boot. Clear the flag after reading the log line.
    """
    if str(os.getenv(PROBE_ENV, "") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        result = probe()
    except Exception:  # noqa: BLE001 - must never keep a worker from starting
        logger.exception("undx_embedding_probe: probe raised; continuing startup")
        return None
    logger.info(
        "UNDX_EMBEDDING_PROBE status=%s model=%s host=%s requested_dim=%s dimension=%s "
        "norm=%s latency_ms=%s encoding=%s error_code=%s",
        result.get("status"), result.get("model"), result.get("endpoint_host"),
        result.get("requested_dimension"), result.get("dimension"), result.get("norm"),
        result.get("latency_ms"), result.get("encoding_format"), result.get("error_code"),
    )
    return result
