#!/usr/bin/env python3
"""Prove `tests/test_scam_shield_routing.py` fails when U3 is undone.

§50. The fourth harness of its kind, and the mutation families here are specific to the
fact that the migrated call site is itself a security control.

* **Rebuilding the transport.** A `requests.post`, a bare `import requests`, an endpoint
  literal, a credential read, a model default. Same family as the sibling harnesses.

* **Weakening the requirement rather than the transport.** `require_json=False`, dropping
  the schema, or admitting a JSON-incapable provider to the chain. None of these break a
  single behavioural test that mocks the seam, because a mocked router returns valid JSON
  whatever it was asked for. They are the reason one class in the test file drives the
  real router down to a mocked transport. The failure they model is the worst one
  available here: a scam check that silently degrades to local-rules-only on every
  request while attaching a reassuring "AI review unavailable" note.

* **Inverting §16.** Letting the model's fields win — `result.update(ai_note)` and its
  narrower cousins. Each is one line, each reads like cleanup, and each hands text an
  attacker pasted a vote on its own risk rating. These are the mutations this phase
  exists for.

* **Loosening the classification.** Lowering the privacy class to make routing easier
  (§4), or deriving the domain from the content instead of the surface (§5).

* **Forging attribution.** Restoring the hardcoded vendor name, and switching the
  overwrite of `source` to a `setdefault` so a model reply can name its own producer.

* **Prose (must stay GREEN).** Five trailing mutations spell out, in comments and
  docstrings, the endpoint, the model name, the credential name and the vendor string
  this migration removed — including in the test file's own prose. Every mutation above
  only ever *adds mechanism*, so from inside a harness a check that fires on the
  paragraph explaining the rule is indistinguishable from a correct one. The test file
  deliberately discusses `api.openai.com` and `"Local rules + OpenAI AI review"`, so a
  literal scan that reached its own source would make deleting the explanation the
  cheapest path to green.

Never mutates the working tree: `build_sandbox` symlinks the repo and makes exactly the
one mutated file real. §53-compliant by construction — there is no `git stash` in it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

TESTS = "tests/test_scam_shield_routing.py"

SHIELD = "services/scam_shield.py"
ROUTER = "undx_router.py"

#: Each mutation is (label, target file, old, new, test that must fail).
#:
#: `expect=None` means the mutation must be *allowed* and the suite has to stay green.
MUTATIONS = [
    # ---- §11-12: the transport is gone, and so is the invitation --------------------
    (
        "restore the direct provider POST",
        SHIELD,
        "def _level(score, flags_count=0, has_urls=False):",
        'def _direct_assessment(payload):\n'
        '    return requests.post("https://api.openai.com/v1/chat/completions",\n'
        '                         json=payload, timeout=12)\n'
        '\n'
        '\n'
        'def _level(score, flags_count=0, has_urls=False):',
        "test_the_module_makes_no_network_call_of_any_kind",
    ),
    (
        # An import is not a call, so nothing behaves differently — which is exactly why
        # it is worth banning. The next person who needs an HTTP call in this file sees a
        # transport already imported and reasonably concludes it is allowed here.
        "re-import requests without calling it",
        SHIELD,
        "import undx_router\n",
        "import requests\n\nimport undx_router\n",
        "test_the_module_imports_no_provider_sdk_and_no_transport",
    ),
    (
        "put a vendor endpoint back in a string the module could request",
        SHIELD,
        'DISCLAIMER = "Do not share seed phrases',
        'ASSESSMENT_URL = "https://api.openai.com/v1/chat/completions"\n'
        'DISCLAIMER = "Do not share seed phrases',
        "test_the_module_names_no_provider_endpoint",
    ),
    (
        # The composed form, which contains no vendor string at all. A grep-based gate
        # passes this and a hostname-literal check does not.
        "compose the endpoint from a host fragment so no vendor string appears",
        SHIELD,
        'DISCLAIMER = "Do not share seed phrases',
        'ASSESSMENT_HOST = "api.openai.com"\n'
        'DISCLAIMER = "Do not share seed phrases',
        "test_the_module_names_no_provider_endpoint",
    ),
    (
        "read a provider credential again",
        SHIELD,
        "import undx_router\n",
        "import os\n\nimport undx_router\n\nSCAM_KEY = os.getenv"
        '("OPENAI_API_KEY", "")\n',
        "test_the_module_reads_no_environment_variable_at_all",
    ),
    (
        # §26: `undx_router.PROVIDERS` is the only model table. This was a real duplicate
        # default in this file, `OPENAI_SCAM_MODEL`, and a second answer to "which model
        # reviews a scam report".
        "restore the duplicate model default",
        SHIELD,
        '_ASSESSMENT_SYSTEM_PROMPT = (',
        'SCAM_MODEL = "gpt-4o-mini"\n\n'
        '_ASSESSMENT_SYSTEM_PROMPT = (',
        "test_the_module_names_no_model",
    ),

    # ---- the requirement, not the transport -----------------------------------------
    (
        # Passes every behavioural test that mocks the seam, because a mocked router
        # returns valid JSON whatever it was asked for.
        "stop requiring JSON",
        SHIELD,
        "        require_json=True,",
        "        require_json=False,",
        "test_the_request_requires_json_and_declares_the_shape_it_parses",
    ),
    (
        "ask for JSON but declare no shape",
        SHIELD,
        "        json_schema=SCAM_ASSESSMENT_SCHEMA,",
        "        json_schema=None,",
        "test_the_request_requires_json_and_declares_the_shape_it_parses",
    ),
    (
        # A schema that omits the key the parser reads is decoration: a provider can
        # satisfy it and still return nothing this module can use.
        "drop safe_actions from the required keys",
        SHIELD,
        '    "required": ["scam_type", "explanation", "safe_actions"],',
        '    "required": ["scam_type", "explanation"],',
        "test_the_request_requires_json_and_declares_the_shape_it_parses",
    ),
    (
        # Reaching past the seam: the mutation is in the router, and it is the one that
        # forces `TheRequirementIsEnforcedNotRequestedTest` to exist. A router that
        # accepts `require_json` and forwards nothing looks identical from a mock.
        "make the router accept require_json and send nothing",
        ROUTER,
        "    if require_json:\n        payload.update(_structured_output_payload(provider, json_schema))",
        "    if False:\n        payload.update(_structured_output_payload(provider, json_schema))",
        "test_the_json_requirement_reaches_every_capable_provider_in_its_own_dialect",
    ),
    (
        # The gate that keeps a JSON-incapable provider out of a chain that requires
        # JSON. Removing it routes the scam check to Claude, which 400s on
        # `response_format`, or to a provider that answers in prose.
        "let a JSON-incapable provider into a chain that requires JSON",
        ROUTER,
        "        if require_json and not config.structured_output:",
        "        if False and require_json and not config.structured_output:",
        "test_a_provider_that_cannot_be_required_to_return_json_is_not_asked",
    ),
    (
        # The opposite error, and the one that would have broken every prose caller in
        # the product: a requirement that is not opt-in.
        "require JSON of every caller whether they asked or not",
        ROUTER,
        "                \"structured_output\": config.structured_output if require_json else \"\",",
        "                \"structured_output\": config.structured_output,",
        "test_the_requirement_is_opt_in_so_every_other_caller_is_unchanged",
    ),

    # ---- §16: the deterministic layer stays on top ----------------------------------
    (
        # The refactor this whole phase guards against — `result.update(ai_note)` by
        # another name. One line, reads like cleanup.
        #
        # The first version of this mutation spread `ai_note` at the *top* of the dict
        # literal and SURVIVED, which was the harness being wrong rather than the suite
        # being weak: in a dict display the later key wins, so every explicit
        # `"risk_score": score` below still overrode the model. A mutation that does not
        # actually invert the property it names proves nothing about the test, and had it
        # been left in place it would have read like coverage.
        "merge the model's fields over the local verdict",
        SHIELD,
        '        "response": response,\n    }',
        '        "response": response,\n'
        '        **(ai_note if ai_note and not ai_note.get("error") else {}),\n    }',
        "test_the_model_cannot_lower_the_score",
    ),
    (
        "let the model downgrade the risk level",
        SHIELD,
        "    risk_level = _level(score, len(red_flags), bool(urls))\n    ai_note = _ai_assessment(original, risk_level)",
        "    risk_level = _level(score, len(red_flags), bool(urls))\n"
        "    ai_note = _ai_assessment(original, risk_level)\n"
        "    if ai_note and ai_note.get(\"risk_level\"):\n"
        "        risk_level = str(ai_note[\"risk_level\"])",
        "test_the_model_cannot_downgrade_the_risk_level",
    ),
    (
        "let the model replace the safe actions instead of adding to them",
        SHIELD,
        "    safe_actions = list(dict.fromkeys(core_actions + safe_actions))[:10]",
        "    safe_actions = list(dict.fromkeys(safe_actions))[:10]",
        "test_the_model_cannot_remove_the_core_safe_actions",
    ),
    (
        "let the model clear the red flags",
        SHIELD,
        '        for action in ai_note.get("safe_actions") or []:',
        '        red_flags = ai_note.get("red_flags", red_flags)\n'
        '        for action in ai_note.get("safe_actions") or []:',
        "test_the_model_cannot_clear_a_red_flag",
    ),
    (
        # The other half of §16. A control that routes a request and discards the answer
        # has kept its ordering and lost its point, and no absence assertion notices.
        "discard the model's contribution entirely",
        SHIELD,
        '        scam_type = ai_note.get("scam_type") or ""',
        '        scam_type = ""',
        "test_what_the_model_is_allowed_to_contribute_still_arrives",
    ),

    # ---- §4 and §5: the declaration ------------------------------------------------
    (
        "lower the privacy class to make routing easier",
        SHIELD,
        "SCAM_SHIELD_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL",
        "SCAM_SHIELD_PRIVACY_CLASS = undx_privacy.SENSITIVITY_PUBLIC",
        "test_the_privacy_class_is_declared_and_is_at_least_confidential",
    ),
    (
        "send no privacy class at all",
        SHIELD,
        "        privacy_class=SCAM_SHIELD_PRIVACY_CLASS,\n",
        "",
        "test_the_domain_and_privacy_class_are_passed_by_keyword",
    ),
    (
        # Content-derived instead of caller-derived. Twice in this mission the
        # content-derived answer has been wrong and the provenance one right.
        "derive the domain from what the text happens to say",
        SHIELD,
        "        call_domain=SCAM_SHIELD_CALL_DOMAIN,",
        "        call_domain=(undx_call_domain.CALL_DOMAIN_TELEGRAM\n"
        "                     if \"telegram\" in text.lower()\n"
        "                     else SCAM_SHIELD_CALL_DOMAIN),",
        "test_the_domain_is_scam_shield_and_comes_from_the_surface_not_the_text",
    ),
    (
        "pass the classification positionally so the next parameter shifts it",
        SHIELD,
        "        timeout=12,\n        temperature=0.1,\n        privacy_class=SCAM_SHIELD_PRIVACY_CLASS,\n        call_domain=SCAM_SHIELD_CALL_DOMAIN,",
        "        SCAM_SHIELD_PRIVACY_CLASS,\n        SCAM_SHIELD_CALL_DOMAIN,\n        timeout=12,\n        temperature=0.1,",
        "test_the_domain_and_privacy_class_are_passed_by_keyword",
    ),

    # ---- attribution ----------------------------------------------------------------
    (
        "hardcode the vendor name in the stored source status again",
        SHIELD,
        '        else "Local rules + AI review"',
        '        else "Local rules + OpenAI AI review"',
        "test_no_provider_name_is_hardcoded_anywhere_in_the_module",
    ),
    (
        # `setdefault` instead of an overwrite. The payload came from a model reading text
        # an attacker chose, so a reply carrying its own `source` happens on purpose.
        "let the model's reply name its own producer",
        SHIELD,
        '    parsed["source"] = str(envelope.get("source") or envelope.get("provider") or "")',
        '    parsed.setdefault("source", str(envelope.get("source") or envelope.get("provider") or ""))',
        "test_the_model_cannot_forge_the_attribution",
    ),

    # ---- failure semantics ----------------------------------------------------------
    (
        "collapse every router refusal into one generic string",
        SHIELD,
        'return {"error": str(envelope.get("error") or "AI review unavailable")[:240]}',
        'return {"error": "AI review unavailable"}',
        "test_the_routers_own_reason_is_carried_not_rewritten",
    ),
    (
        "report a broken JSON promise as a generic outage",
        SHIELD,
        'return {"error": "AI review returned an unparseable answer"}',
        'return {"error": "AI review unavailable"}',
        "test_a_provider_that_broke_its_json_promise_is_named_as_such",
    ),
    (
        # `json.loads("[]")` succeeds and `[].get` raises AttributeError, which escapes
        # `_ai_assessment` and 500s the scan route. The type check is load-bearing.
        "accept a JSON array as an assessment",
        SHIELD,
        "    if not isinstance(parsed, dict):\n"
        '        return {"error": "AI review returned a non-object answer"}',
        "    if False:\n"
        '        return {"error": "AI review returned a non-object answer"}',
        "test_a_json_array_is_not_accepted_as_an_assessment",
    ),
    (
        "spend a provider call on an empty input box",
        SHIELD,
        "    if not text:\n        return None",
        "    if False:\n        return None",
        "test_nothing_is_asked_when_there_is_nothing_to_ask_about",
    ),
    (
        # Restoring the guard reads like a cost saving and is an evasion: pad a scam
        # message past 5000 characters and the model layer switches itself off.
        "refuse to review anything long again",
        SHIELD,
        "    if not text:\n        return None",
        "    if not text or len(text) > 5000:\n        return None",
        "test_a_long_paste_is_still_reviewed_because_the_old_guard_was_an_evasion",
    ),
    (
        "send the whole pasted document instead of a bounded slice",
        SHIELD,
        "Text:\\n{text[:5000]}",
        "Text:\\n{text}",
        "test_the_content_sent_is_bounded",
    ),
    (
        "let the UNDX assistant identity onto a security verdict",
        SHIELD,
        "def _ai_assessment(text, local_level):",
        "def _wear_identity(messages):\n"
        "    return pulse_ai_provider_router.prepare_undx_model_request(messages)\n"
        "\n"
        "\n"
        "def _ai_assessment(text, local_level):",
        "test_the_module_does_not_wear_the_undx_assistant_identity",
    ),

    # ---- prose: must stay GREEN -----------------------------------------------------
    (
        "name the removed endpoint in a comment",
        SHIELD,
        "# `requests` and `os` are gone from this module",
        "# This module used to `requests.post` to https://api.openai.com/v1/chat/completions\n"
        "# with a key from OPENAI_API_KEY and a model from OPENAI_SCAM_MODEL, default\n"
        "# gpt-4o-mini.\n"
        "# `requests` and `os` are gone from this module",
        None,
    ),
    (
        "name the removed vendor string in a docstring",
        SHIELD,
        '    """The model\'s opinion, which is advice to this module and never its verdict.',
        '    """The model\'s opinion, which is advice to this module and never its verdict.\n\n'
        '    The status line this feeds used to read "Local rules + OpenAI AI review", which\n'
        '    was true while the transport was hardcoded to OpenAI and is a stored false\n'
        '    attribution now that any of four providers can answer.',
        None,
    ),
    (
        "explain the §16 ordering at the call site",
        SHIELD,
        "    ai_note = _ai_assessment(original, risk_level)",
        "    # Everything above this line is deterministic and is not sent to the model:\n"
        "    # score, risk_level, red_flags. Anthropic, OpenAI and Gemini all get the same\n"
        "    # prompt and none of them can lower a score.\n"
        "    ai_note = _ai_assessment(original, risk_level)",
        None,
    ),
    (
        "expand the test file's own prose about what was removed",
        TESTS,
        "U3 of `UNDX_PROVIDER_CALLSITE_CENSUS.md`:",
        "The removed call was `requests.post(\"https://api.openai.com/v1/chat/completions\")`\n"
        "with `OPENAI_API_KEY` and `OPENAI_SCAM_MODEL=gpt-4o-mini`, reporting itself as\n"
        "\"Local rules + OpenAI AI review\".\n\n"
        "U3 of `UNDX_PROVIDER_CALLSITE_CENSUS.md`:",
        None,
    ),
    (
        "add a docstring to a test naming every banned string",
        TESTS,
        "    def test_the_module_makes_no_network_call_of_any_kind(self):",
        "    def test_the_module_makes_no_network_call_of_any_kind(self):\n"
        '        """No requests.post, no httpx, no urllib.urlopen, no openai.ChatCompletion,\n'
        '        and no api.openai.com or api.anthropic.com anywhere near this file."""',
        None,
    ),
]


def main() -> int:
    failures = []
    for label, target, old, new, expect in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp), target)
            path = sandbox / target
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                failures.append(f"{label}: anchor matched {source.count(old)}x, expected 1")
                print(f"BAD {label}: anchor matched {source.count(old)}x")
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")

            env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=900,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0
            before = len(failures)

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: prose is not supposed to trip anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            print(f"{'ok ' if len(failures) == before else 'BAD'} {label}: {verdict}")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"All {len(MUTATIONS)} mutations behaved as specified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
