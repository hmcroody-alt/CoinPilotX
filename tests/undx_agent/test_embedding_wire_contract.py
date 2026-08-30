"""The Perplexity embeddings wire contract, pinned.

These tests exist because of a specific production incident: the live acceptance probe
reached Perplexity, got a clean HTTP 200 with a valid vector in it, and the client threw
``"provider returned an empty vector"`` anyway. The parser had been written against the
OpenAI shape — ``data[i].embedding`` as a JSON array of floats — but Perplexity's
documented default is ``base64_int8``, a base64 **string** decoding to signed int8 bytes.
``isinstance(raw, list)`` was False, so a good response was rejected as a bad one.

The lesson is that a shape assumption about a third party needs a test that encodes the
third party's *documented* shape, not the shape we expected. Everything below is built
from the published OpenAPI schema for ``POST /v1/embeddings``:

* ``embedding`` is a base64 string; for ``base64_int8`` it decodes to signed int8 values
  of length ``dimensions``
* ``encoding_format`` accepts only ``base64_int8`` and ``base64_binary`` — **there is no
  float option**, so "just ask for floats" is not an available fix
* embeddings are unnormalised and must be compared with cosine similarity

No network. The provider edge is driven through a fake ``requests`` module so the real
status-code classification in ``_post`` and the real decoding in ``_parse`` both run.
"""

from __future__ import annotations

import base64
import json
import struct
import unittest
from unittest.mock import patch

from services import undx_embedding_service as embed


DIMENSIONS = 64

BASE_ENV = {
    "PERPLEXITY_API_KEY": "test-key-not-a-real-credential",
    "UNDX_EMBEDDING_DIMENSIONS": str(DIMENSIONS),
    "UNDX_EMBEDDING_MAX_RETRIES": "0",
    "UNDX_EMBEDDING_TIMEOUT_SECONDS": "1",
    "UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "5",
}


def int8_vector(seed: int, dimensions: int = DIMENSIONS) -> list[int]:
    """A deterministic, non-constant int8 vector. Never all-zero: a zero vector has no
    direction, so it would hide a normalisation bug behind a division guard."""
    return [(((seed + index) * 37) % 255) - 127 for index in range(dimensions)]


def encode_int8(values: list[int]) -> str:
    """Exactly what Perplexity puts on the wire for ``base64_int8``."""
    return base64.b64encode(struct.pack(f"{len(values)}b", *values)).decode("ascii")


def wire_response(vectors: list[list[int]], *, tokens: int = 12) -> dict:
    """A response byte-for-byte shaped like the published example."""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": encode_int8(values)}
            for index, values in enumerate(vectors)
        ],
        "model": "pplx-embed-v1-0.6b",
        "usage": {
            "prompt_tokens": tokens,
            "total_tokens": tokens,
            "cost": {"input_cost": 4.8e-08, "total_cost": 4.8e-08, "currency": "USD"},
        },
    }


class FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body

    @property
    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class FakeRequests:
    """Captures the outgoing payload so the request contract can be asserted too."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers or {}, "payload": json.loads(data), "timeout": timeout}
        )
        return self.response


def run_with(response: FakeResponse, texts=("hello world",)):
    fake = FakeRequests(response)
    with patch.dict("os.environ", BASE_ENV, clear=False):
        with patch.dict("sys.modules", {"requests": fake}):
            batch = embed.embed_texts(list(texts), purpose="test")
    return batch, fake


def expect_failure(response: FakeResponse, texts=("hello world",)):
    fake = FakeRequests(response)
    with patch.dict("os.environ", BASE_ENV, clear=False):
        with patch.dict("sys.modules", {"requests": fake}):
            try:
                embed.embed_texts(list(texts), purpose="test")
            except embed.EmbeddingUnavailable as exc:
                return exc
    raise AssertionError("expected EmbeddingUnavailable, got a successful batch")


class DocumentedResponseIsAccepted(unittest.TestCase):
    """The regression that caused the incident: a good 200 must produce a vector."""

    def test_base64_int8_response_yields_a_vector(self):
        original = int8_vector(1)
        batch, _ = run_with(FakeResponse(200, wire_response([original])))

        self.assertEqual(len(batch.vectors), 1)
        self.assertEqual(len(batch.vectors[0]), DIMENSIONS)
        self.assertEqual(batch.tokens, 12)

    def test_vector_is_l2_normalised_and_preserves_direction(self):
        """Perplexity returns unnormalised int8. We normalise on the way in so that the
        stored index can be compared with a dot product, which is what cosine similarity
        reduces to once both sides are unit length."""
        original = int8_vector(2)
        batch, _ = run_with(FakeResponse(200, wire_response([original])))
        vector = batch.vectors[0]

        norm = sum(value * value for value in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=9)

        expected_norm = sum(value * value for value in original) ** 0.5
        for index, raw in enumerate(original):
            self.assertAlmostEqual(vector[index], raw / expected_norm, places=9)

    def test_batch_order_follows_the_index_field_not_arrival_order(self):
        """``data`` is not promised to be sorted. A short or shuffled list must not
        silently misalign vectors against the documents they were computed for."""
        first, second, third = int8_vector(3), int8_vector(4), int8_vector(5)
        body = wire_response([first, second, third])
        body["data"] = [body["data"][2], body["data"][0], body["data"][1]]

        batch, _ = run_with(FakeResponse(200, body), texts=("a", "b", "c"))

        for position, original in enumerate((first, second, third)):
            expected_norm = sum(value * value for value in original) ** 0.5
            self.assertAlmostEqual(batch.vectors[position][0], original[0] / expected_norm, places=9)


class RequestContractIsExplicit(unittest.TestCase):
    def test_encoding_format_is_sent_and_is_base64_int8(self):
        """Sent explicitly rather than relying on the provider's default, so a change to
        that default cannot silently swap the vector space under a stored index."""
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(6)])))
        payload = fake.calls[0]["payload"]

        self.assertEqual(payload["encoding_format"], "base64_int8")
        self.assertEqual(payload["model"], "pplx-embed-v1-0.6b")
        self.assertEqual(payload["dimensions"], DIMENSIONS)
        self.assertEqual(payload["input"], ["hello world"])

    def test_binary_encoding_is_not_requested(self):
        """``base64_binary`` is one bit per dimension and needs Hamming distance. The
        retrieval scorer does cosine, so asking for it would be a silent correctness bug
        rather than a loud failure."""
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(7)])))
        self.assertNotEqual(fake.calls[0]["payload"]["encoding_format"], "base64_binary")

    def test_credential_goes_in_the_authorization_header_only(self):
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(8)])))
        call = fake.calls[0]

        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key-not-a-real-credential")
        self.assertNotIn("test-key-not-a-real-credential", json.dumps(call["payload"]))
        self.assertNotIn("test-key-not-a-real-credential", call["url"])


class MalformedResponsesStillFailClosed(unittest.TestCase):
    """Accepting base64 must not turn the parser into something that accepts anything.
    Each of these has a distinct message, because "it failed" and "it failed *this way*"
    are different amounts of information at three in the morning."""

    def test_undecodable_base64_is_reported_as_such(self):
        body = wire_response([int8_vector(9)])
        body["data"][0]["embedding"] = "!!!not base64!!!"
        self.assertIn("not valid base64", expect_failure(FakeResponse(200, body)).reason)

    def test_empty_string_is_an_empty_vector(self):
        body = wire_response([int8_vector(10)])
        body["data"][0]["embedding"] = ""
        self.assertIn("empty vector", expect_failure(FakeResponse(200, body)).reason)

    def test_missing_embedding_key_is_named_precisely(self):
        body = wire_response([int8_vector(11)])
        del body["data"][0]["embedding"]
        self.assertIn("no embedding", expect_failure(FakeResponse(200, body)).reason)

    def test_null_embedding_reports_the_type(self):
        body = wire_response([int8_vector(12)])
        body["data"][0]["embedding"] = None
        self.assertIn("NoneType", expect_failure(FakeResponse(200, body)).reason)

    def test_wrong_dimension_count_is_rejected_not_padded(self):
        """A vector of the wrong length must never be silently accepted — it would sit in
        the index looking like poor model quality forever."""
        body = wire_response([int8_vector(13, dimensions=DIMENSIONS // 2)])
        reason = expect_failure(FakeResponse(200, body)).reason
        self.assertIn(f"expected {DIMENSIONS}", reason)

    def test_short_data_list_is_rejected(self):
        body = wire_response([int8_vector(14)])
        reason = expect_failure(FakeResponse(200, body), texts=("a", "b")).reason
        self.assertIn("for 2 inputs", reason)

    def test_float_array_is_still_accepted_for_proxy_compatibility(self):
        """Not the documented shape, but a proxy that speaks floats is not a reason to
        break. Explicitly tested so the tolerance is deliberate rather than accidental."""
        body = wire_response([int8_vector(15)])
        body["data"][0]["embedding"] = [0.5] * DIMENSIONS
        batch, _ = run_with(FakeResponse(200, body))
        self.assertEqual(len(batch.vectors[0]), DIMENSIONS)

    def test_non_finite_float_array_is_rejected(self):
        body = wire_response([int8_vector(16)])
        body["data"][0]["embedding"] = [float("nan")] * DIMENSIONS
        self.assertIn("non-finite", expect_failure(FakeResponse(200, body)).reason)


class StatusClassificationIsUnchanged(unittest.TestCase):
    """The decode fix must not have blurred the distinction between "the provider said
    no" and "the provider said yes and we could not read it" — mistaking one for the
    other is exactly what cost the previous acceptance run."""

    def test_401_is_a_credential_rejection(self):
        exc = expect_failure(FakeResponse(401, {"error": "unauthorized"}))
        self.assertIn("rejected the credential", exc.reason)
        self.assertFalse(exc.retryable)

    def test_429_is_retryable(self):
        self.assertTrue(expect_failure(FakeResponse(429, {"error": "slow down"})).retryable)

    def test_500_is_retryable(self):
        self.assertTrue(expect_failure(FakeResponse(503, {"error": "unavailable"})).retryable)

    def test_422_reports_the_status(self):
        exc = expect_failure(FakeResponse(422, {"detail": [{"msg": "bad model"}]}))
        self.assertIn("422", exc.reason)
        self.assertFalse(exc.retryable)

    def test_non_json_body_is_named(self):
        self.assertIn("non-JSON", expect_failure(FakeResponse(200, "<html>gateway</html>")).reason)


if __name__ == "__main__":
    unittest.main()
