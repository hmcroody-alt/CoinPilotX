"""The diagnostic's own acceptance: does it stay inside its stated bounds?

The module claims two things that are worth testing rather than trusting. First, that
its output is exhaustively metadata -- no vector components, no API key, no headers, no
provider response body -- and that this holds on the *failure* paths too, which is where
leaks normally happen, because that is where a provider's own words get near a log line.
Second, that it is genuinely inert: unset the flag and nothing reaches the network.

The leak tests work by planting a sentinel where a secret would be (in the API key, in a
provider error string, in an exception message) and asserting the sentinel cannot be
found anywhere in the serialised result. That is stronger than checking a key list,
because it fails even if someone later adds a well-meaning "detail" field.
"""

from __future__ import annotations

import json
import math
import os
import unittest
from unittest import mock

from services import undx_embedding_diagnostic as diag
from services import undx_embedding_service as embed


SENTINEL = "pplx-SUPERSECRET-do-not-log-4242"


def _unit_vector(dimension: int) -> tuple[float, ...]:
    """A well-formed vector: right length, finite, unit norm, non-constant."""
    raw = [float(index + 1) for index in range(dimension)]
    norm = math.sqrt(sum(value * value for value in raw))
    return tuple(value / norm for value in raw)


class ProbeOutputBoundsTests(unittest.TestCase):
    """Everything the probe can return, on every path it can take."""

    DIMENSION = 8

    def setUp(self):
        patches = [
            mock.patch.object(embed, "configured_dimensions", return_value=self.DIMENSION),
            mock.patch.object(embed, "configured_model", return_value="pplx-embed-v1-0.6b"),
            mock.patch.object(embed, "configured_model_version", return_value="1"),
            mock.patch.object(embed, "configured_endpoint",
                              return_value="https://api.perplexity.ai/embeddings?key=" + SENTINEL),
            mock.patch.object(embed, "api_key_configured", return_value=True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _blob(self, result) -> str:
        """Serialise everything the caller could see, values and keys alike."""
        return json.dumps(result, default=repr)

    def test_pass_when_every_condition_holds(self):
        vector = _unit_vector(self.DIMENSION)
        with mock.patch.object(embed, "embed_one", return_value=vector):
            result = diag.probe()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["error_code"], "none")
        self.assertEqual(result["dimension"], self.DIMENSION)
        self.assertAlmostEqual(result["norm"], 1.0, places=5)
        self.assertEqual(result["failed_checks"], [])
        self.assertTrue(result["not_degenerate"])
        self.assertIsInstance(result["latency_ms"], float)

    def test_pass_result_contains_no_vector_components(self):
        """A PASS is the path that actually holds a vector, so it is the leak risk."""
        vector = _unit_vector(self.DIMENSION)
        with mock.patch.object(embed, "embed_one", return_value=vector):
            result = diag.probe()
        blob = self._blob(result)
        for value in vector:
            self.assertNotIn(repr(value), blob)
            self.assertNotIn("%.6f" % value, blob)
        # The only numbers that survive are counts and aggregates.
        self.assertEqual(result["distinct_components"], self.DIMENSION)

    def test_endpoint_is_reduced_to_a_host(self):
        """An operator can put anything in the endpoint URL, including a key."""
        with mock.patch.object(embed, "embed_one", return_value=_unit_vector(self.DIMENSION)):
            result = diag.probe()
        self.assertEqual(result["endpoint_host"], "api.perplexity.ai")
        self.assertNotIn(SENTINEL, self._blob(result))

    def test_provider_reason_is_mapped_not_forwarded(self):
        """The failure path: a provider that echoes a secret still cannot get it out."""
        failure = embed.EmbeddingUnavailable(
            "provider rejected the credential: %s" % SENTINEL, retryable=False
        )
        with mock.patch.object(embed, "embed_one", side_effect=failure):
            result = diag.probe()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error_code"], "auth_rejected")
        self.assertFalse(result["retryable"])
        self.assertNotIn(SENTINEL, self._blob(result))

    def test_unrecognised_reason_becomes_unknown_error(self):
        """The default must not be 'echo what we were told'."""
        failure = embed.EmbeddingUnavailable("something nobody wrote a code for " + SENTINEL)
        with mock.patch.object(embed, "embed_one", side_effect=failure):
            result = diag.probe()
        self.assertEqual(result["error_code"], "unknown_error")
        self.assertNotIn(SENTINEL, self._blob(result))

    def test_unexpected_exception_reports_type_only(self):
        with mock.patch.object(embed, "embed_one", side_effect=ValueError(SENTINEL)):
            result = diag.probe()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error_code"], "unexpected_valueerror")
        self.assertNotIn(SENTINEL, self._blob(result))

    def test_probe_never_raises(self):
        """A diagnostic that crashes tells you nothing about the thing it measures."""
        with mock.patch.object(embed, "embed_one", side_effect=TypeError("x")):
            result = diag.probe()
        self.assertEqual(result["status"], "FAIL")

    def test_missing_key_short_circuits_before_any_request(self):
        with mock.patch.object(embed, "api_key_configured", return_value=False), \
             mock.patch.object(embed, "embed_one") as embed_one:
            result = diag.probe()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error_code"], "api_key_missing")
        self.assertEqual(embed_one.call_count, 0)

    def test_probe_text_is_fixed_and_not_user_derived(self):
        with mock.patch.object(embed, "embed_one", return_value=_unit_vector(self.DIMENSION)) as one:
            diag.probe()
        one.assert_called_once_with(diag.PROBE_TEXT, purpose="diagnostic_probe")
        self.assertEqual(diag.PROBE_TEXT, "PulseSoc notification preferences")


class VectorValidationTests(unittest.TestCase):
    """A vector that is present but the wrong shape is not a working provider."""

    DIMENSION = 8

    def setUp(self):
        for patch in (
            mock.patch.object(embed, "configured_dimensions", return_value=self.DIMENSION),
            mock.patch.object(embed, "api_key_configured", return_value=True),
            mock.patch.object(embed, "configured_endpoint", return_value="https://api.perplexity.ai/x"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _probe_with(self, vector):
        with mock.patch.object(embed, "embed_one", return_value=vector):
            return diag.probe()

    def test_wrong_dimension_fails(self):
        result = self._probe_with(_unit_vector(self.DIMENSION - 1))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("dimension_matches_config", result["failed_checks"])
        self.assertEqual(result["error_code"], "vector_validation_failed")

    def test_unnormalised_vector_fails(self):
        result = self._probe_with(tuple(2.0 * value for value in _unit_vector(self.DIMENSION)))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("unit_normalised", result["failed_checks"])

    def test_constant_vector_fails_as_degenerate(self):
        """Decodes cleanly, normalises cleanly, carries no information."""
        value = 1.0 / math.sqrt(self.DIMENSION)
        result = self._probe_with(tuple([value] * self.DIMENSION))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("not_degenerate", result["failed_checks"])

    def test_non_finite_component_fails_without_a_norm(self):
        vector = list(_unit_vector(self.DIMENSION))
        vector[0] = float("inf")
        result = self._probe_with(tuple(vector))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("all_components_finite", result["failed_checks"])
        self.assertIsNone(result["norm"])

    def test_empty_vector_fails(self):
        result = self._probe_with(())
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("vector_non_empty", result["failed_checks"])


class ClassificationTests(unittest.TestCase):
    def test_every_table_entry_maps_to_its_code(self):
        for needle, code in diag.ERROR_CODES:
            self.assertEqual(diag.classify("provider said: %s here" % needle), code)

    def test_classification_is_case_insensitive(self):
        self.assertEqual(diag.classify("RATE LIMITED by upstream"), "rate_limited")

    def test_empty_and_none_are_unknown(self):
        self.assertEqual(diag.classify(""), "unknown_error")
        self.assertEqual(diag.classify(None), "unknown_error")  # type: ignore[arg-type]


class StartupHookTests(unittest.TestCase):
    """Off by default is the whole point: this runs inside a worker's boot path."""

    def setUp(self):
        self._previous = os.environ.pop(diag.PROBE_ENV, None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop(diag.PROBE_ENV, None)
        if self._previous is not None:
            os.environ[diag.PROBE_ENV] = self._previous

    def test_absent_flag_performs_no_request(self):
        with mock.patch.object(diag, "probe") as probe:
            self.assertIsNone(diag.run_startup_probe_if_enabled())
        self.assertEqual(probe.call_count, 0)

    def test_falsy_values_perform_no_request(self):
        for value in ("", "0", "false", "no", "off", "maybe"):
            os.environ[diag.PROBE_ENV] = value
            with mock.patch.object(diag, "probe") as probe:
                self.assertIsNone(diag.run_startup_probe_if_enabled(), value)
            self.assertEqual(probe.call_count, 0, value)

    def test_truthy_values_run_exactly_once(self):
        for value in ("1", "true", "YES", "on"):
            os.environ[diag.PROBE_ENV] = value
            with mock.patch.object(diag, "probe", return_value={"status": "PASS"}) as probe:
                result = diag.run_startup_probe_if_enabled()
            self.assertEqual(probe.call_count, 1, value)
            self.assertEqual(result, {"status": "PASS"})

    def test_a_failing_probe_cannot_stop_a_worker_starting(self):
        os.environ[diag.PROBE_ENV] = "1"
        with mock.patch.object(diag, "probe", side_effect=RuntimeError(SENTINEL)):
            self.assertIsNone(diag.run_startup_probe_if_enabled())

    def test_log_line_carries_metadata_only(self):
        os.environ[diag.PROBE_ENV] = "1"
        payload = {
            "status": "PASS", "model": "pplx-embed-v1-0.6b", "endpoint_host": "api.perplexity.ai",
            "requested_dimension": 256, "dimension": 256, "norm": 1.0, "latency_ms": 91.4,
            "encoding_format": "base64_int8", "error_code": "none",
            # A field the log line does not name; it must not appear anyway.
            "secret_leaked_by_a_future_edit": SENTINEL,
        }
        with mock.patch.object(diag, "probe", return_value=payload), \
             self.assertLogs(diag.logger, level="INFO") as logs:
            diag.run_startup_probe_if_enabled()
        rendered = "\n".join(logs.output)
        self.assertIn("UNDX_EMBEDDING_PROBE status=PASS", rendered)
        self.assertIn("dimension=256", rendered)
        self.assertNotIn(SENTINEL, rendered)


class WiringTests(unittest.TestCase):
    """The module is reachable from exactly one place, and it is not a route."""

    def test_no_http_route_references_the_diagnostic(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        bot_source = (root / "bot.py").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn("undx_embedding_diagnostic", bot_source)

    def test_alert_worker_calls_the_gated_hook(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        worker = (root / "alert_worker.py").read_text(encoding="utf-8")
        self.assertIn("undx_embedding_diagnostic", worker)
        self.assertIn("run_startup_probe_if_enabled()", worker)
        # Called at boot, not inside the cycle loop: a repeating probe would be a
        # recurring charge and a recurring third-party dependency.
        self.assertLess(worker.index("run_startup_probe_if_enabled"), worker.index("while RUNNING"))


if __name__ == "__main__":
    unittest.main()
