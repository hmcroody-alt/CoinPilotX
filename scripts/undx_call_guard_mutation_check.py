#!/usr/bin/env python3
"""Prove the Phase 9 gate tests fail when the gate is undone.

§50. Two files are under test here and they answer the same question two ways:
`tests/test_undx_config_drift.py` checks that the *source* contains no unrouted
chat call, `tests/test_undx_call_guard.py` checks that if one ran anyway something
would say so. Both are the kind of check that passes for the wrong reason more
easily than it fails, so both need this.

The guard is the harder of the two to test honestly, because the value it reports
when everything is correct is zero — and almost every way of breaking it also
reports zero. A guard that never installed reports zero. A classifier that never
matches reports zero. A wrapper around a function nobody calls reports zero. So
the mutations below include several whose whole job is to break the guard in a way
that leaves the number looking right, and each one names the paired test that is
supposed to notice.

Four of these exist because something in the gate was genuinely wrong before this
phase, and the mutation is what keeps it from coming back:

* `revert the allowlist to a basename comparison` — it was one, so every file in
  the tree named `undx_router.py` was exempt without being listed. §19 asks for an
  explicit allowlist and a name is not a location.
* `stop looking for a chat path without a host` — `_CHAT_PATHS` used to be
  consulted only *after* a host matched, so `f"{base}/chat/completions"` over an
  env-supplied base was invisible. That is §12's named shape and this repository
  has really had it.
* `call a declared URL a call again` — two of the three findings on a clean tree
  described a module constant as a call "outside the circuit breaker" and advised
  metering it, which cannot be done at a constant.
* `count one call twice` — `requests.post` reaches the network through
  `Session.request` and both are wrapped, so the first draft double-counted, which
  would have made a metric whose target is zero depend on which spelling a caller
  used.

The `expect=None` mutations are the opposite shape and they are the ones that stop
this from decaying into a word filter. Every other mutation here removes
mechanism; none of them remove prose. A test that fires on the paragraph
explaining a rule looks identical from in here to one that fires on the rule. So
the prose mutations spell out, in full, every host, every path fragment, every SDK
name and every counter name, in docstrings and comments, and demand GREEN.

Never mutates the working tree: builds a sandbox of symlinks and replaces only the
mutated file. Shares `build_sandbox` with `undx_call_domain_mutation_check.py`,
whose guard raises rather than writing through a symlink — it did write through
one, for every depth-3 target, and this file's targets are depth 2.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

DRIFT = "services/undx_config_drift.py"
GUARD = "services/undx_call_guard.py"
HEALTH = "services/undx_fabric_health.py"

#: Both suites, every time. A mutation to the scanner can be caught by either
#: file, and running only the obvious one would let a mutation "pass" because the
#: check that noticed lives next door.
TESTS = "tests/test_undx_config_drift.py tests/test_undx_call_guard.py"

#: Each mutation is (label, target, old, new, test that must fail).
#: `expect=None` means the mutation must be allowed — the suites must stay green.
MUTATIONS = [
    # ---------------------------------------------------------- the allowlist (§19)
    (
        "revert the allowlist to a basename comparison",
        DRIFT,
        "            relative = os.path.relpath(path, root)\n"
        "            if relative in _ADAPTER_ALLOWLIST or relative == _SELF:\n"
        "                continue",
        "            relative = os.path.relpath(path, root)\n"
        "            if filename in _ADAPTER_ALLOWLIST or relative == _SELF:\n"
        "                continue",
        "test_the_adapter_allowlist_is_a_path_not_a_name",
    ),
    (
        "widen the allowlist to anything under services",
        DRIFT,
        '_ADAPTER_ALLOWLIST: tuple[str, ...] = (\n    "undx_router.py",\n)',
        '_ADAPTER_ALLOWLIST: tuple[str, ...] = (\n    "undx_router.py",\n'
        '    "services/vendor/undx_router.py",\n)',
        "test_the_adapter_allowlist_is_a_path_not_a_name",
    ),

    # ------------------------------------------- host-less chat paths (§12)
    (
        "stop looking for a chat path without a host",
        DRIFT,
        "        names_chat_path = in_request and any(\n"
        "            part in lowered for part in _CHAT_PATHS)",
        "        names_chat_path = False",
        "test_a_chat_path_composed_onto_a_configurable_base_is_critical",
    ),
    (
        "require a host before a path counts, as it used to",
        DRIFT,
        "        if names_host or names_chat_path:",
        "        if names_host:",
        "test_a_chat_path_composed_onto_a_configurable_base_is_critical",
    ),
    (
        "let a bare path count even when nothing sends it",
        DRIFT,
        "        names_chat_path = in_request and any(\n"
        "            part in lowered for part in _CHAT_PATHS)",
        "        names_chat_path = any(\n"
        "            part in lowered for part in _CHAT_PATHS)",
        "test_a_bare_chat_path_nobody_sends_is_not_a_call",
    ),
    (
        "forget that an f-string's literal halves are one URL",
        DRIFT,
        "        elif isinstance(node, ast.JoinedStr):",
        "        elif isinstance(node, ast.JoinedStr) and False:",
        "test_an_fstring_endpoint_is_found_once_at_the_right_severity",
    ),

    # ------------------------------------- declaration versus call (the false positive)
    (
        "call a declared URL a call again",
        DRIFT,
        "                if not (in_request or sends):",
        "                if False:",
        "test_declaring_an_endpoint_is_reported_as_declaring_it",
    ),
    (
        "treat every module as one that sends",
        DRIFT,
        "            sends = _performs_http(tree)",
        "            sends = True",
        "test_declaring_an_endpoint_is_reported_as_declaring_it",
    ),
    (
        "treat no module as one that sends",
        DRIFT,
        "            sends = _performs_http(tree)",
        "            sends = False",
        "test_a_url_that_reaches_the_wire_indirectly_is_still_a_call",
    ),
    (
        "accept a verb without a client, so a local list named requests counts",
        DRIFT,
        "    return bool(parts) and parts[-1] in _HTTP_VERBS and any(\n"
        "        part in _HTTP_CLIENTS for part in parts[:-1])",
        "    return bool(parts) and parts[-1] in _HTTP_VERBS",
        "test_a_dict_lookup_named_get_does_not_make_a_module_an_http_client",
    ),

    # ------------------------------------------------------- the SDK detector (§11)
    (
        "delete the SDK detector",
        DRIFT,
        "            for number, module in _sdk_usage_in(tree):",
        "            for number, module in ():",
        "test_a_provider_sdk_import_is_critical_even_when_lazy",
    ),
    (
        "only notice an SDK imported at module scope",
        DRIFT,
        "    for node in ast.walk(tree):\n"
        "        if isinstance(node, ast.Import):\n"
        "            names = [alias.name for alias in node.names]",
        "    for node in getattr(tree, 'body', []):\n"
        "        if isinstance(node, ast.Import):\n"
        "            names = [alias.name for alias in node.names]",
        "test_a_provider_sdk_import_is_critical_even_when_lazy",
    ),
    (
        "only notice an SDK imported under its exact dotted name",
        DRIFT,
        "            if name in _PROVIDER_SDKS or root in _PROVIDER_SDKS:",
        "            if name in _PROVIDER_SDKS:",
        "test_an_sdk_reached_through_a_submodule_is_still_an_sdk",
    ),

    # --------------------------------------------- the runtime guard: classification
    (
        "stop classifying chat separately from other capabilities",
        GUARD,
        "    if any(part in lowered for part in _CHAT_PATHS):\n"
        "        return UNROUTED_CHAT\n"
        "    return UNMETERED_CAPABILITY",
        "    return UNMETERED_CAPABILITY",
        "test_a_chat_call_outside_the_router_is_counted",
    ),
    (
        "fold embeddings into the headline counter",
        GUARD,
        "    if any(part in lowered for part in _CHAT_PATHS):\n"
        "        return UNROUTED_CHAT\n"
        "    return UNMETERED_CAPABILITY",
        "    return UNROUTED_CHAT",
        "test_embeddings_and_images_are_charged_to_the_other_counter",
    ),
    (
        "charge every outbound call to the counter",
        GUARD,
        "    if not any(host in lowered for host in _PROVIDER_HOSTS):\n        return None",
        "    if False:\n        return None",
        "test_a_call_to_anything_else_is_not",
    ),
    (
        "keep a private copy of the host list instead of importing one",
        GUARD,
        "from services.undx_config_drift import _CHAT_PATHS, _PROVIDER_HOSTS",
        'from services.undx_config_drift import _CHAT_PATHS\n\n'
        '_PROVIDER_HOSTS = ("api.openai.com",)',
        "test_embeddings_and_images_are_charged_to_the_other_counter",
    ),

    # -------------------------------------- the runtime guard: the frame walk
    (
        "let any frame count as the router's",
        GUARD,
        "        if os.path.realpath(frame.f_code.co_filename) in adapters:\n"
        "            return True",
        "        if adapters:\n"
        "            return True",
        "test_a_chat_call_outside_the_router_is_counted",
    ),
    (
        "match the adapter by filename rather than by path",
        GUARD,
        "        if os.path.realpath(frame.f_code.co_filename) in adapters:\n"
        "            return True",
        "        if os.path.basename(frame.f_code.co_filename) in {\n"
        "                os.path.basename(item) for item in adapters}:\n"
        "            return True",
        "test_a_decoy_file_named_like_the_router_does_not_launder_a_call",
    ),
    (
        "stop resolving the adapter path, so a symlinked checkout never matches",
        GUARD,
        "        if path:\n            found.add(os.path.realpath(path))",
        "        if path:\n            found.add(path)",
        "test_the_adapter_is_matched_by_path_not_by_filename",
    ),
    (
        "walk only the immediate caller instead of the whole stack",
        GUARD,
        "    frame = sys._getframe(1)\n"
        "    while frame is not None:\n"
        "        if os.path.realpath(frame.f_code.co_filename) in adapters:\n"
        "            return True\n"
        "        frame = frame.f_back\n"
        "    return False",
        "    frame = sys._getframe(1)\n"
        "    return os.path.realpath(frame.f_code.co_filename) in adapters",
        "test_a_routed_call_is_not_counted",
    ),

    # ------------------------------------- the runtime guard: counting and installation
    (
        "count one call twice by dropping the re-entry guard",
        GUARD,
        '        if getattr(_reentry, "inside", False):\n'
        "            return original(*args, **kwargs)",
        "        if False:\n            return original(*args, **kwargs)",
        "test_one_call_is_counted_once",
    ),
    (
        "stop wrapping Session.request, so a pooled caller is invisible",
        GUARD,
        '            _wrap(requests.sessions.Session, "request", url_argument=2)',
        "            pass",
        "test_a_caller_holding_its_own_session_is_still_seen",
    ),
    (
        "wrap only the convenience verbs, not urlopen",
        GUARD,
        '        _wrap(urllib.request, "urlopen")',
        "        pass",
        "test_a_urllib_request_object_is_read_for_its_url",
    ),
    (
        "read the URL positionally only, missing a Request object",
        GUARD,
        "    if not isinstance(url, str):\n"
        "        # `urlopen(Request(...))` passes an object, and the URL is on it.\n"
        '        url = getattr(url, "full_url", None) or getattr(url, "url", None)',
        "    if not isinstance(url, str):\n        url = None",
        "test_a_urllib_request_object_is_read_for_its_url",
    ),
    (
        "read Session.request's url from the wrong position",
        GUARD,
        '            _wrap(requests.sessions.Session, "request", url_argument=2)',
        '            _wrap(requests.sessions.Session, "request", url_argument=1)',
        "test_a_caller_holding_its_own_session_is_still_seen",
    ),
    (
        "let install run twice and stack a wrapper on a wrapper",
        GUARD,
        "    if any(existing is owner and name == attribute\n"
        "           for existing, name, _ in _original):\n        return",
        "    if False:\n        return",
        "test_the_flag_is_not_the_only_thing_stopping_a_double_wrap",
    ),
    (
        "report installed whether or not anything was wrapped",
        GUARD,
        '        "installed": _installed,',
        '        "installed": True,',
        "test_installed_is_read_from_the_state_and_not_asserted",
    ),

    # -------------------------------------------- the runtime guard: posture and safety
    (
        "block the call instead of counting it",
        GUARD,
        "        if _enforcing:\n"
        '            raise UnroutedProviderCall(f"{kind}: unrouted provider call from {where}")',
        '        raise UnroutedProviderCall(f"{kind}: unrouted provider call from {where}")',
        "test_the_guard_counts_and_does_not_block_by_default",
    ),
    (
        "let a failure in the guard reach the caller",
        GUARD,
        "    except Exception:  # pragma: no cover - the guard must not break the caller\n"
        '        log.exception("undx_call_guard failed while observing an outbound call")\n'
        "        return None",
        "    except Exception:\n        raise",
        "test_a_broken_guard_does_not_break_the_caller",
    ),
    (
        "log the URL, which on Gemini's endpoint is a live key",
        GUARD,
        '        log.error("%s: a provider call bypassed the router, from %s", kind, where)',
        '        log.error("%s: a provider call bypassed the router: %s from %s",\n'
        "                  kind, url, where)",
        "test_the_url_is_never_logged",
    ),
    (
        "let witnesses grow without bound",
        GUARD,
        "            if len(_witnesses[kind]) < MAX_WITNESSES:\n"
        "                _witnesses[kind].append(where)",
        "            _witnesses[kind].append(where)",
        "test_witnesses_are_bounded",
    ),
    (
        "let the capability counter drag the guard's ok to false",
        GUARD,
        '        "ok": counts[UNROUTED_CHAT] == 0,',
        '        "ok": sum(counts.values()) == 0,',
        "test_ok_is_about_the_chat_counter_alone",
    ),

    # ---------------------------------------------------- the health surface (§42)
    (
        "drop routing from the fabric's ok",
        HEALTH,
        '                 and bool((out.get("config") or {}).get("ok", False))\n'
        '                 and bool((out.get("routing") or {}).get("ok", False)))',
        '                 and bool((out.get("config") or {}).get("ok", False)))',
        "test_routing_is_the_reason_the_fabric_is_not_ok_and_not_the_weather",
    ),
    (
        "stop collecting the routing section at all",
        HEALTH,
        '                             ("config", _drift), ("routing", _routing)):',
        '                             ("config", _drift)):',
        "test_the_fabric_is_not_ok_while_a_chat_call_is_bypassing_the_router",
    ),

    # ------------------------------------------------------------------ prose (GREEN)
    #
    # Every mutation above removes mechanism. None removes prose, so a check that
    # fired on the explanation rather than the rule would look exactly as correct
    # from in here. These name every banned string in full, in the places a
    # reviewer would actually write them.
    (
        "name every host and path in a comment (must stay GREEN)",
        GUARD,
        "def _classify(url: str) -> str | None:",
        "# The hosts are api.openai.com, api.anthropic.com,\n"
        "# generativelanguage.googleapis.com, api.deepseek.com, api.groq.com,\n"
        "# api.perplexity.ai and api.meta.ai; the chat paths are /chat/completions,\n"
        "# /v1/messages, :generateContent and /v1/responses. A comment is not a call.\n"
        "def _classify(url: str) -> str | None:",
        None,
    ),
    (
        "name every SDK in the scanner's own docstring (must stay GREEN)",
        DRIFT,
        '    """Vendor endpoints, as (line, url, reaches_an_http_client).',
        '    """Vendor endpoints, as (line, url, reaches_an_http_client).\n\n'
        "    The SDKs are openai, anthropic, google.generativeai, groq, cohere,\n"
        "    mistralai, litellm, langchain and llama_index. Naming them is not\n"
        "    importing them, and a docstring is an ast.Constant, not an ast.Import.\n",
        None,
    ),
    (
        "spell the counter names out in the health surface (must stay GREEN)",
        HEALTH,
        "def _routing() -> dict[str, Any]:",
        "# Publishes undx_unrouted_provider_calls_total and\n"
        "# undx_unmetered_capability_calls_total.\n"
        "def _routing() -> dict[str, Any]:",
        None,
    ),
    (
        "add a docstring naming a full provider URL to the guard (must stay GREEN)",
        GUARD,
        "def counters() -> dict[str, int]:",
        "def counters() -> dict[str, int]:\n"
        '    """Before this existed, a call to https://api.openai.com/v1/chat/completions\n'
        "    from outside undx_router.py was unobservable in a running process.\n"
        '    """',
        None,
    ),
]


def main() -> int:
    """Run every mutation, or with ``--only SUBSTRING`` just the matching labels.

    The filter exists because of how the two adapter entries were fixed. They are an
    adjacent pair — one mutation matches the allowlist by basename, the next stops
    resolving the path — and their `expect` names were attached to the wrong member,
    twice. The pre-flight validator did not notice either time, because both names
    resolve to real tests; what was wrong was which of the two. Only a run can tell
    those apart, and a full run is ~26 minutes, which is long enough that the cheap
    move is to guess again rather than to check. So: re-verify one family in two
    minutes and there is no incentive to guess.

    A filtered run deliberately still prints the full total in its closing line rather
    than claiming completeness, since a subset behaving says nothing about the rest.
    """
    only = None
    argv = sys.argv[1:]
    if argv:
        if argv[0] != "--only" or len(argv) != 2:
            print("usage: undx_call_guard_mutation_check.py [--only SUBSTRING]")
            return 2
        only = argv[1]

    selected = [m for m in MUTATIONS if only is None or only in m[0]]
    if not selected:
        print(f"--only {only!r} matched none of the {len(MUTATIONS)} mutations")
        return 2
    if only is not None:
        print(f"--only {only!r}: {len(selected)} of {len(MUTATIONS)} mutations\n")

    failures = []
    for label, target, old, new, expect in selected:
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
                [sys.executable, "-m", "pytest", *TESTS.split(), "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=1800,
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
    if only is not None:
        print(f"{len(selected)} of {len(MUTATIONS)} mutations behaved as specified "
              f"(filtered by {only!r}; the other {len(MUTATIONS) - len(selected)} were not run).")
        return 0
    print(f"All {len(MUTATIONS)} mutations behaved as specified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
