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
        is true and is a different sentence.

        The fixture sends the request. It did not used to — it was a bare
        `ENDPOINT = "..."` assignment, and it passed, because the scanner could
        not tell a declared URL from a called one and called everything a call.
        A test named for a call has to make one, or it is asserting on wording.
        """
        findings = self._scan(**{"embed.py": '''
            import requests
            ENDPOINT = "https://api.perplexity.ai/v1/embeddings"
            def embed(rows):
                return requests.post(ENDPOINT, json={"input": rows}, timeout=8)
        '''})
        self.assertEqual([f["code"] for f in findings], ["unmetered_provider_call"])
        self.assertEqual(findings[0]["severity"], drift.WARNING)

    def test_declaring_an_endpoint_is_reported_as_declaring_it(self):
        """`services/undx_brain/config.py` holds `UNDX_EMBEDDING_ENDPOINT` in a
        flag catalog and performs no HTTP anywhere in the module. It was being
        told it "calls the vendor directly... outside the circuit breaker", and
        advised to meter the call through `undx_cost.record` — advice that cannot
        be followed at a constant. The endpoint is still worth reporting, because
        a base URL a deployment can redirect is precisely what §12 is about, but a
        finding that describes something the file does not do is a finding people
        learn to wave past, and the true ones go with it."""
        findings = self._scan(**{"catalog.py": '''
            FLAGS = [("UNDX_EMBEDDING_ENDPOINT", "https://api.perplexity.ai/v1/embeddings")]
        '''})
        self.assertEqual([f["code"] for f in findings], ["provider_url_declared"])
        self.assertNotIn("calls", findings[0]["detail"])
        self.assertNotIn("undx_cost.record", findings[0]["fix"])

    def test_a_chat_path_composed_onto_a_configurable_base_is_critical(self):
        """The shape §12 names, and one this repository has really had:
        `services/pulse_ai_provider_router.py` built its URL as
        `f"{base}/chat/completions"` from `UNDX_CANDIDATE_BASE_URL`. No vendor
        host appears anywhere in the file, so a check keyed on the host list
        cannot see it — which made the call sites that can be pointed at *any*
        vendor, including a training tier, the ones that did not count."""
        findings = self._scan(**{"candidate.py": '''
            import os, requests
            BASE = os.getenv("UNDX_CANDIDATE_BASE_URL", "")
            def ask(body):
                return requests.post(BASE + "/chat/completions", json=body, timeout=20)
        '''})
        self.assertEqual([f["code"] for f in findings], ["unrouted_chat_call"])
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_a_bare_chat_path_nobody_sends_is_not_a_call(self):
        """The other half of the rule above. A list of path fragments a test
        asserts against is not a call, and a scanner that said otherwise would
        report this repository's own protection suite — the same way it used to
        report its own docstrings, before it started parsing instead of
        grepping."""
        findings = self._scan(**{"guard.py": '''
            BANNED = ("/chat/completions", "/v1/messages", ":generateContent")
            def clean(source):
                return all(fragment not in source for fragment in BANNED)
        '''})
        self.assertEqual(findings, [])

    def test_a_provider_sdk_import_is_critical_even_when_lazy(self):
        """Every AI call here is hand-rolled HTTP, so this detector currently
        reports nothing — which is the reason to have it, not a reason to skip
        writing it. An SDK takes the base URL, the model, the timeout and the
        retry policy out of the fabric in one import, and leaves behind no URL
        literal for the checks above to find.

        Lazy, and inside a function nobody calls, because that is the only shape
        that proves anything: a module-scope `import openai` raises
        `ModuleNotFoundError` here at collection time, so a suite with this
        detector deleted would 'catch' it just as loudly."""
        findings = self._scan(**{"legacy.py": '''
            def client():
                import openai
                return openai.OpenAI()
        '''})
        self.assertEqual([f["code"] for f in findings], ["provider_sdk_import"])
        self.assertEqual(findings[0]["severity"], drift.CRITICAL)

    def test_an_sdk_reached_through_a_submodule_is_still_an_sdk(self):
        """`import openai.types` names a module that is not in `_PROVIDER_SDKS`.

        The list holds roots, so the check is `name in _PROVIDER_SDKS or root in
        _PROVIDER_SDKS`, and the test above only exercises the first half — its
        fixture imports plain `openai`, which matches by exact name. A mutation
        deleting the `root` branch therefore survived it. Importing a submodule is
        the ordinary way an SDK actually arrives (`from anthropic.types import
        Message`), so the half that was untested is the half that matters.
        """
        findings = self._scan(**{"legacy.py": '''
            def client():
                import anthropic.types
                from openai.lib import azure
                return anthropic.types, azure
        '''})
        self.assertEqual([f["code"] for f in findings],
                         ["provider_sdk_import", "provider_sdk_import"])
        self.assertTrue(all(f["severity"] == drift.CRITICAL for f in findings))

    def test_a_url_that_reaches_the_wire_indirectly_is_still_a_call(self):
        """Two hops, because one hop is resolved by name and proves less.

        `_provider_urls_in` marks a literal as `in_request` when it sits inside a
        request call or in a variable handed straight to one. When the URL travels
        any further than that — through a helper, a dict, a class attribute — that
        one-hop resolution loses it, and the only remaining reason to call it a
        call is that the module performs HTTP at all (`_performs_http`).

        The embeddings test above cannot see this: its fixture passes the constant
        directly to `requests.post`, so `in_request` is already true and `sends`
        never decides anything. Replacing `_performs_http(tree)` with `False`
        survived it. Here `sends` is the only thing standing between a declaration
        and a call, which is the case the real
        `services/pulse_ai/automated_image_pipeline.py` is closer to than the
        fixture was.
        """
        findings = self._scan(**{"indirect.py": '''
            import requests
            ENDPOINT = "https://api.perplexity.ai/v1/embeddings"
            def _target():
                return ENDPOINT
            def embed(rows):
                return requests.post(_target(), json={"input": rows}, timeout=8)
        '''})
        self.assertEqual([f["code"] for f in findings], ["unmetered_provider_call"])

    def test_a_dict_lookup_named_get_does_not_make_a_module_an_http_client(self):
        """`_is_http_call` requires a verb *and* a client, and this is the `and`.

        `config.get("timeout")` ends in a verb from `_HTTP_VERBS`. If the client
        requirement is dropped, every module that reads a dict becomes a module
        that sends requests — and every provider URL constant in one of them turns
        from a declaration into a CRITICAL unrouted call. That is not a
        hypothetical shape: `services/undx_brain/config.py` is a flag catalog full
        of `.get` calls and a vendor endpoint, and it is the exact file whose
        finding this phase had to correct.

        `test_a_bare_chat_path_nobody_sends_is_not_a_call` could not catch the
        mutation because its fixture contains no attribute call at all. The
        receiver is the thing under test, so the fixture has to have one.
        """
        findings = self._scan(**{"catalog.py": '''
            CONFIG = {"timeout": 8}
            ENDPOINT = "https://api.openai.com/v1/chat/completions"
            def timeout():
                return CONFIG.get("timeout")
        '''})
        self.assertEqual([f["code"] for f in findings], ["provider_url_declared"])
        self.assertEqual(findings[0]["severity"], drift.WARNING)

    def test_the_adapter_allowlist_is_a_path_not_a_name(self):
        """§19 asks for an explicit allowlist. The entry reads `undx_router.py`
        and was compared against the bare filename, so every file in the tree
        with that name was exempt — including one a contributor could add under
        `services/vendor/`. An allowlist keyed on a name is a wildcard that
        happens to be spelled specifically."""
        findings = self._scan(**{
            "undx_router.py":
                'import requests\n'
                'requests.post("https://api.openai.com/v1/chat/completions", json={}, timeout=5)',
            "services/vendor/undx_router.py":
                'import requests\n'
                'requests.post("https://api.anthropic.com/v1/messages", json={}, timeout=5)',
        })
        self.assertEqual([f["where"] for f in findings],
                         ["services/vendor/undx_router.py:2"])

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
