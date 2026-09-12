"""Configuration drift detection, and the ways a detector like this goes hollow.

A check over configuration has a specific failure mode: it reports something
true about a setting that nothing depends on, everyone learns it is noise, and
it stops being read. So every finding here is paired with the case that must
*not* fire, and the source scanner is tested against the real repository as
well as synthetic files — because the real one contains `undx_router.py`, which
legitimately holds seven provider URLs and every model default, and a scanner
that flagged the config authority would be reporting the correct arrangement as
a defect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import undx_router  # noqa: E402
from services import undx_config_drift as drift  # noqa: E402
from services import undx_cost, undx_health, undx_privacy  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _codes(result):
    return {f["code"] for f in result["findings"]}


def _by_code(result, code):
    return [f for f in result["findings"] if f["code"] == code]


class _CleanEnv(unittest.TestCase):
    """One reachable provider and nothing else set, so each test adds exactly
    one condition and the finding it produces is attributable to that."""

    NOISE = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL", "ANTHROPIC_MODEL",
             "OPENAI_BASE_URL", "OPENAI_API_BASE",
             "UNDX_MONTHLY_COST_BUDGET_USD", "UNDX_PROVIDER_COST_BUDGET_USD",
             "UNDX_MONTHLY_TOKEN_BUDGET", "UNDX_PROVIDER_TOKEN_BUDGET",
             "UNDX_COST_BUDGET_STRICT", "UNDX_COST_LEDGER_ENABLED")

    def setUp(self):
        env = {config.key_env: f"sk-test-{name}"
               for name, config in undx_router.PROVIDERS.items()}
        self._env = mock.patch.dict(os.environ, env, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for name in self.NOISE + tuple(undx_health.HEALTH_ENV_VARS):
            os.environ.pop(name, None)
        for config in undx_router.PROVIDERS.values():
            os.environ.pop(config.model_env, None)


# ------------------------------------------------------------- runtime findings

class UnsendableKeyTest(_CleanEnv):
    """The Groq variable in this deployment holds a JSON document containing a
    key rather than the key. `_api_key` correctly refuses to send it, and the
    consequence is that the provider reports as unconfigured — identical, on
    every surface, to one nobody set up."""

    def test_a_key_that_cannot_be_sent_is_not_reported_as_absent(self):
        key_env = undx_router.PROVIDERS["groq"].key_env
        with mock.patch.dict(os.environ, {key_env: '{"api_key": "gsk_abc"}'}):
            result = drift.check()
        findings = _by_code(result, "unsendable_key")
        self.assertEqual([f["provider"] for f in findings], ["groq"])
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_a_provider_with_no_key_at_all_is_not_flagged(self):
        """Anti-vacuity, and the distinction the finding exists to draw: an
        empty variable is a decision, a mangled one is an accident."""
        key_env = undx_router.PROVIDERS["groq"].key_env
        with mock.patch.dict(os.environ, {key_env: ""}):
            result = drift.check()
        self.assertEqual(_by_code(result, "unsendable_key"), [])

    def test_an_ordinary_key_is_not_flagged(self):
        self.assertEqual(_by_code(drift.check(), "unsendable_key"), [])


class AlienCredentialTest(_CleanEnv):
    """Six ANTHROPIC_* variables in this deployment point at api.meta.ai with
    ANTHROPIC_MODEL=muse-spark-1.3-contributor. The router ignores them on
    purpose; nothing else in the ecosystem does."""

    def test_an_anthropic_variable_pointing_at_meta_is_critical(self):
        with mock.patch.dict(os.environ,
                             {"ANTHROPIC_BASE_URL": "https://api.meta.ai"}):
            findings = _by_code(drift.check(), "alien_endpoint")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)
        self.assertIn("meta.ai", findings[0]["detail"])

    def test_an_anthropic_variable_pointing_at_anthropic_is_not(self):
        """Anti-vacuity: the check is about the destination, not about the
        variable existing."""
        with mock.patch.dict(os.environ,
                             {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}):
            self.assertEqual(_by_code(drift.check(), "alien_endpoint"), [])

    def test_a_contributor_tier_model_is_named_as_one(self):
        contributor = next(
            (model for model, ceiling in undx_privacy.MODEL_CEILINGS.items()
             if ceiling == undx_privacy.PRIVACY_SYNTHETIC), None)
        self.assertIsNotNone(contributor, "no contributor-tier model declared")
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": contributor}):
            findings = _by_code(drift.check(), "contributor_tier_credentials")
        self.assertEqual(len(findings), 1)

    def test_a_standard_tier_model_is_not(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-haiku-4-5"}):
            self.assertEqual(
                _by_code(drift.check(), "contributor_tier_credentials"), [])


class PrivacyCeilingDriftTest(_CleanEnv):
    """A model override can lower what its provider may be sent. That is
    enforced at refusal time, which is correct and also too late to be the only
    notice — by then the system refuses most of its own traffic and looks, from
    outside, like an outage."""

    def _contributor_model(self):
        for model, ceiling in undx_privacy.MODEL_CEILINGS.items():
            if ceiling == undx_privacy.PRIVACY_SYNTHETIC:
                return model
        self.skipTest("no contributor-tier model declared")

    def test_an_override_that_lowers_the_ceiling_is_reported(self):
        model_env = undx_router.PROVIDERS["meta"].model_env
        with mock.patch.dict(os.environ, {model_env: self._contributor_model()}):
            findings = _by_code(drift.check(), "privacy_ceiling_lowered")
        self.assertEqual([f["provider"] for f in findings], ["meta"])

    def test_the_default_model_does_not_lower_anything(self):
        """Anti-vacuity: shipped configuration must be clean, or the check
        fires on every deployment and is therefore ignored."""
        self.assertEqual(_by_code(drift.check(), "privacy_ceiling_lowered"), [])


class BudgetCoverageTest(_CleanEnv):
    """A dollar budget over a price table that does not cover every provider
    restrains only the priced ones while reporting itself as enforced."""

    def test_a_dollar_budget_with_unpriced_providers_is_critical(self):
        with mock.patch.dict(os.environ,
                             {"UNDX_MONTHLY_COST_BUDGET_USD": "50"}):
            findings = _by_code(drift.check(), "budget_blind_spot")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_no_budget_means_no_blind_spot(self):
        """Anti-vacuity: an unpriced model is only a problem relative to a
        dollar limit. Reporting it unconditionally would make the finding a
        statement about the price table rather than about the budget."""
        self.assertEqual(_by_code(drift.check(), "budget_blind_spot"), [])

    def test_strict_mode_changes_the_advice_not_the_fact(self):
        with mock.patch.dict(os.environ,
                             {"UNDX_MONTHLY_COST_BUDGET_USD": "50",
                              "UNDX_COST_BUDGET_STRICT": "true"}):
            findings = _by_code(drift.check(), "budget_blind_spot")
        self.assertEqual(len(findings), 1)
        self.assertIn("removes them from routing", findings[0]["detail"])

    def test_a_token_budget_covers_everything_so_it_is_not_flagged(self):
        with mock.patch.dict(os.environ, {"UNDX_MONTHLY_TOKEN_BUDGET": "1000000"}):
            self.assertEqual(_by_code(drift.check(), "budget_blind_spot"), [])


class SharedStateTest(_CleanEnv):
    """Nine processes enforcing one limit each is nine limits."""

    def test_a_budget_without_the_ledger_is_critical(self):
        with mock.patch.dict(os.environ,
                             {"UNDX_MONTHLY_TOKEN_BUDGET": "1000",
                              "UNDX_COST_LEDGER_ENABLED": "false"}):
            findings = _by_code(drift.check(), "budget_without_ledger")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_the_ledger_being_off_without_a_budget_is_not(self):
        """Anti-vacuity: with nothing being enforced, a per-process tally is an
        observability figure, which is what it always was."""
        with mock.patch.dict(os.environ, {"UNDX_COST_LEDGER_ENABLED": "false"}):
            self.assertEqual(_by_code(drift.check(), "budget_without_ledger"), [])

    def test_an_unshared_breaker_is_reported(self):
        with mock.patch.dict(os.environ,
                             {"UNDX_PROVIDER_HEALTH_SHARED": "false"}):
            findings = _by_code(drift.check(), "breaker_not_shared")
        self.assertEqual(len(findings), 1)
        self.assertIn(str(undx_health.threshold()), findings[0]["detail"])

    def test_the_default_configuration_is_clean(self):
        """The shipped defaults must produce no CRITICAL. A check that fires on
        a correct deployment trains people to ignore it, and then it is not a
        check."""
        result = drift.check()
        self.assertEqual([f["code"] for f in result["findings"]
                          if f["severity"] == drift.CRITICAL], [])
        self.assertTrue(result["ok"])


class ReachabilityTest(_CleanEnv):
    def test_no_reachable_provider_is_critical(self):
        blank = {config.key_env: "" for config in undx_router.PROVIDERS.values()}
        with mock.patch.dict(os.environ, blank):
            result = drift.check()
        self.assertIn("no_reachable_provider", _codes(result))
        self.assertFalse(result["ok"])

    def test_a_single_provider_is_information_not_an_error(self):
        """Failover with one provider is not failover, but it is also a valid
        way to run. Severity is about what a person should do."""
        blank = {config.key_env: "" for name, config in undx_router.PROVIDERS.items()
                 if name != "openai"}
        with mock.patch.dict(os.environ, blank):
            result = drift.check()
        findings = _by_code(result, "single_provider")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], drift.INFO)
        self.assertTrue(result["ok"])


class RobustnessTest(_CleanEnv):
    def test_a_broken_check_does_not_take_the_audit_down(self):
        """The conditions this looks for are the ones that make other code
        behave unexpectedly, so it has to survive them."""
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://x.test"}), \
                mock.patch.object(drift, "_check_budget_coverage",
                                  side_effect=RuntimeError("boom")):
            result = drift.check()
        self.assertIsInstance(result["findings"], list)
        # The checks after the broken one still ran: the alien-credential probe
        # is last in the sequence, so finding it here proves the failure was
        # contained rather than merely logged on the way out.
        self.assertTrue(_by_code(result, "alien_endpoint"))

    def test_a_check_that_did_not_run_is_reported_not_just_logged(self):
        """Anti-vacuity. A check that raised has not found nothing; it has
        found nothing *yet*. If that only ever reached a log line, the summary
        would say `ok` about a guarantee no one verified — which is the same
        fake green this module exists to remove."""
        with mock.patch.object(drift, "_check_budget_coverage",
                               side_effect=RuntimeError("boom")):
            result = drift.check()
        findings = _by_code(result, "check_did_not_run")
        self.assertEqual(len(findings), 1)
        self.assertIn("budget_coverage", findings[0]["detail"])
        self.assertIn("RuntimeError", findings[0]["detail"])

    def test_the_handler_survives_an_error_inside_the_error_path(self):
        """`mock.patch.object` with a `side_effect` installs a MagicMock, and
        `MagicMock.__name__` raises AttributeError — so an except-block that
        read the probe's name off the function crashed *inside* the handler
        meant to contain the failure, escaped to the outer one, and returned a
        clean bill of health with every finding discarded. This is that bug."""
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://x.test"}), \
                mock.patch.object(drift, "_check_shared_state",
                                  side_effect=RuntimeError("boom")):
            result = drift.check()
        self.assertFalse(result["ok"])
        self.assertTrue(_by_code(result, "alien_endpoint"))

    def test_a_router_that_will_not_import_is_critical_not_clean(self):
        """An audit that cannot reach the thing it audits knows less than
        nothing about it."""
        with mock.patch.object(drift, "_router", side_effect=ImportError("no")):
            result = drift.check()
        self.assertFalse(result["ok"])
        self.assertEqual([f["code"] for f in result["findings"]], ["audit_unusable"])

    def test_every_severity_is_declared(self):
        blank = {config.key_env: "" for config in undx_router.PROVIDERS.values()}
        with mock.patch.dict(os.environ, dict(blank, ANTHROPIC_BASE_URL="x.test")):
            result = drift.check()
        for finding in result["findings"]:
            with self.subTest(code=finding["code"]):
                self.assertIn(finding["severity"], drift.SEVERITIES)
                self.assertTrue(finding["fix"], "a finding with no fix is noise")


# -------------------------------------------------------------- source findings

class SourceScanTest(unittest.TestCase):

    def _scan(self, **files):
        with tempfile.TemporaryDirectory() as root:
            for name, body in files.items():
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(textwrap.dedent(body))
            return drift.scan_source(root)

    def test_a_direct_chat_call_is_critical(self):
        findings = self._scan(**{"feature.py": '''
            import requests
            def ask(q):
                return requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    json={"model": "gpt-4o-mini", "messages": q})
        '''})
        self.assertEqual([f["code"] for f in findings], ["unrouted_chat_call"])
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_a_routed_call_is_not_flagged(self):
        """Anti-vacuity. This is the whole point: the fixed version has to come
        back clean, or the check cannot tell anyone they have finished."""
        findings = self._scan(**{"feature.py": '''
            import undx_router
            def ask(q):
                return undx_router.route_structured_request(None, "sys", q)
        '''})
        self.assertEqual(findings, [])

    def test_a_second_model_default_is_reported(self):
        findings = self._scan(**{"feature.py": '''
            import os
            MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        '''})
        self.assertEqual([f["code"] for f in findings], ["duplicate_model_default"])

    def test_reading_the_variable_without_a_default_is_not_drift(self):
        """Anti-vacuity: a second *reader* is fine. A second *default* is the
        second answer, and it is the answer that drifts."""
        findings = self._scan(**{"feature.py": '''
            import os
            MODEL = os.getenv("OPENAI_MODEL")
        '''})
        self.assertEqual(findings, [])

    def test_a_docstring_that_describes_the_pattern_is_not_a_finding(self):
        """Parsed, not grepped. A detector that reports its own documentation
        is one people learn to ignore — which is the same outcome as not having
        it."""
        findings = self._scan(**{"feature.py": '''
            """This module used to call os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            and post to https://api.openai.com/v1/chat/completions."""
            OK = True
        '''})
        self.assertEqual(findings, [])

    def test_an_fstring_endpoint_is_found_once_at_the_right_severity(self):
        """Gemini's URL interpolates the model, so the path segment that makes
        it a chat call sits on the far side of the interpolation. Walking the
        tree naively reports it twice — once truncated, at a lower severity."""
        findings = self._scan(**{"feature.py": '''
            import requests
            def ask(model, body):
                return requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    json=body)
        '''})
        self.assertEqual([f["code"] for f in findings], ["unrouted_chat_call"])

    def test_a_non_chat_endpoint_is_a_warning_not_a_duplicate(self):
        """Embeddings are a capability the router does not offer, so there is
        nothing to route them to. The finding is that they are unmetered, which
        is true and is a different sentence."""
        findings = self._scan(**{"embed.py": '''
            ENDPOINT = "https://api.perplexity.ai/v1/embeddings"
        '''})
        self.assertEqual([f["code"] for f in findings], ["unmetered_provider_call"])
        self.assertEqual(findings[0]["severity"], drift.WARNING)

    def test_tests_and_scripts_are_not_scanned(self):
        findings = self._scan(**{
            "tests/test_x.py": 'URL = "https://api.openai.com/v1/chat/completions"',
            "scripts/y.py": 'URL = "https://api.openai.com/v1/chat/completions"',
        })
        self.assertEqual(findings, [])

    def test_a_syntactically_broken_file_is_skipped_not_fatal(self):
        findings = self._scan(**{"broken.py": "def (:::", "ok.py": "X = 1"})
        self.assertEqual(findings, [])


class RealRepositoryTest(unittest.TestCase):

    def test_the_config_authority_is_not_reported_as_drift(self):
        """`undx_router.py` holds every model default and seven provider URLs.
        That is the correct arrangement — one place that knows — and a scanner
        that flagged it would be reporting the fix as the defect."""
        findings = drift.scan_source(REPO)
        self.assertEqual([f for f in findings if f["where"].startswith("undx_router.py")],
                         [])

    def test_the_scan_completes_on_the_real_tree(self):
        """A source check that is too slow or too fragile to run on this
        repository is not a check on this repository."""
        findings = drift.scan_source(REPO)
        for finding in findings:
            with self.subTest(where=finding["where"]):
                self.assertIn(finding["severity"], drift.SEVERITIES)
                self.assertTrue(finding["where"])


class CommandLineTest(unittest.TestCase):
    """The CLI is how anyone actually reads this, so it is part of the check.

    Run as a subprocess rather than by calling `main()`: the script inserts the
    repository on `sys.path` and reads the real environment, and an in-process
    call would test neither of those.
    """

    SCRIPT = os.path.join(REPO, "scripts", "undx_config_drift.py")

    def _run(self, *flags):
        return subprocess.run([sys.executable, self.SCRIPT, *flags],
                              capture_output=True, text=True, cwd=REPO, timeout=180)

    def test_it_emits_parseable_json_for_both_halves(self):
        done = self._run("--json")
        self.assertIn(done.returncode, (0, 1), done.stderr[-400:])
        payload = json.loads(done.stdout)
        self.assertEqual(set(payload), {"runtime", "source"})
        for half, result in payload.items():
            with self.subTest(half=half):
                self.assertIn("findings", result)
                self.assertEqual(result["ok"], result["critical"] == 0)

    def test_exit_status_follows_critical_findings_only(self):
        """A warning is true and worth fixing; failing a pipeline on one is how
        a check gets routed around rather than acted on."""
        done = self._run("--source", "--root", tempfile.mkdtemp())
        self.assertEqual(done.returncode, 0, done.stdout[-400:])

    def test_a_tree_with_an_unrouted_call_fails_the_run(self):
        """Anti-vacuity for the exit status: the clean-tree pass above means
        nothing unless a dirty tree comes back non-zero."""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "feature.py"), "w", encoding="utf-8") as handle:
                handle.write('import requests\n'
                             'requests.post("https://api.openai.com/v1/chat/completions")\n')
            done = self._run("--source", "--root", root)
        self.assertEqual(done.returncode, 1)
        self.assertIn("unrouted_chat_call", done.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
