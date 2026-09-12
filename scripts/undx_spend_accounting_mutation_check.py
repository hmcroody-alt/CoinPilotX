#!/usr/bin/env python3
"""Prove the spend-accounting tests fail when the accounting is broken.

§50, applied to money. The tests in `tests/test_undx_cost_budget.py` and
`tests/test_undx_capabilities.py` assert things like "this call contributed zero
dollars", and an assertion that a number is zero is the easiest kind in this repo
to satisfy by accident — a ledger that records nothing at all passes most of them.
So every mutation here is paired with a test that distinguishes **zero because we
measured zero** from **zero because we do not know**, which is the one distinction
§34 exists to protect and the one that reads as harmless cleanup when removed.

A separate harness from `undx_classification_subject_mutation_check.py` on purpose.
That script's subject is which provider a request reaches; this one's is what a
month's spend report says. Sharing the `MUTATIONS` list would mean every
accounting change reran fourteen routing suites to learn nothing, and the reverse.
`build_sandbox` *is* shared, by import rather than by copy, for the reason written
in its own docstring: a duplicated copy with a depth-two assumption once truncated
real files in the working tree.

Three of these mutations are shapes that would plausibly survive code review:

  * `cost_micro or 0` reads as defensive coercion. It converts every unknown price
    into a measured $0.00, which is precisely the claim §34 forbids, and it does it
    without changing a single dollar total — only the count of calls the total
    excludes.
  * Returning early when the price is unknown reads as "do not record garbage". It
    drops the call count for every unpriced provider, so `image` and `translation`
    spend would report as *no calls made* rather than as unpriced calls.
  * Delegating the dollar branch to `to_micro_usd` reads as removing duplication.
    That function returns 0 for unparseable input by documented contract and leaves
    the uncosted decision to its caller, so delegating makes a malformed price free
    in one input form and unknown in the other.

Run: .venv/bin/python3 scripts/undx_spend_accounting_mutation_check.py
     .venv/bin/python3 scripts/undx_spend_accounting_mutation_check.py --only "known zero"
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

COST = "services/undx_cost.py"
CAPS = "services/undx_capabilities.py"

COST_TESTS = "tests/test_undx_cost_budget.py"
CAPS_TESTS = "tests/test_undx_capabilities.py"

#: (label, file, old, new, test that must fail, test file)
MUTATIONS = [
    (
        # The collapse this whole column exists to prevent. DuckDuckGo is keyless
        # and free, so its 0 is a measurement; gpt-image-1 has no published price,
        # so its 0 would be a guess. `if micro:` makes them the same row.
        "cost: treat a known-zero price as an unknown one",
        COST,
        '    micro = usage.get("cost_micro_usd")\n    if micro is not None:\n',
        '    micro = usage.get("cost_micro_usd")\n    if micro:\n',
        "test_a_known_zero_and_an_unknown_price_are_not_the_same_row",
        COST_TESTS,
    ),
    (
        # Looks like deduplication. `to_micro_usd` returns 0 for junk by contract
        # and documents that its caller owns the uncosted decision, so this makes
        # an unparseable dollar price free while an unparseable micro price stays
        # unknown - the two input forms disagreeing about the same bad input.
        "cost: delegate the dollar branch to to_micro_usd",
        COST,
        '    try:\n        return int(round(float(usd) * MICRO_PER_USD)), 0\n'
        '    except (TypeError, ValueError):\n        return 0, 1\n',
        '    return to_micro_usd(usd), 0\n',
        "test_a_malformed_price_is_unknown_in_both_forms",
        COST_TESTS,
    ),
    (
        # The pre-refactor state, restored. `_apply` feeds the process mirror and
        # `record` feeds the durable row; deriving the pair separately means the
        # mirror never learns the micro form, so during a database outage the
        # degraded path reports non-chat spend as $0.00 uncosted while the durable
        # path had been recording it as money. Visible only during an outage.
        "cost: let the process mirror derive its own cost again",
        COST,
        'def _apply(bucket: dict[str, int], usage: dict[str, Any]) -> None:\n'
        '    cost_micro, uncosted = _cost_fields(usage)\n',
        'def _apply(bucket: dict[str, int], usage: dict[str, Any]) -> None:\n'
        '    cost_micro = to_micro_usd(usage.get("cost_usd"))\n'
        '    uncosted = 1 if usage.get("cost_usd") is None else 0\n',
        "test_the_mirror_and_the_ledger_agree_about_the_micro_form",
        COST_TESTS,
    ),
    (
        # Unrecognised is not chat. Mapping it there hides non-chat spend inside
        # the one number everybody already reads, which is worse than a column
        # named `unknown` that looks like the defect it is.
        "cost: normalise an unrecognised call kind to chat",
        COST,
        '    return text if text in KNOWN_CALL_KINDS else CALL_KIND_UNKNOWN\n',
        '    return text if text in KNOWN_CALL_KINDS else CALL_KIND_CHAT\n',
        "test_an_unrecognised_kind_is_not_laundered_into_chat",
        CAPS_TESTS,
    ),
    (
        # Reads as defensive coercion against a None the type checker complained
        # about. Changes no dollar total anywhere - it only stops the ledger from
        # recording that the total excludes this call.
        "capabilities: coerce an unknown price to zero before recording",
        CAPS,
        '        "cost_micro_usd": cost_micro,\n',
        '        "cost_micro_usd": cost_micro or 0,\n',
        "test_an_unpriced_provider_is_recorded_and_counted_as_uncosted",
        CAPS_TESTS,
    ),
    (
        # Reads as "do not write rows we cannot price". Loses the call count for
        # every unpriced provider, so image and translation spend would report as
        # no calls rather than as unpriced calls - the gap becomes invisible
        # instead of merely unquantified.
        "capabilities: skip recording when the price is unknown",
        CAPS,
        '    cost_micro = price_micro_usd(kind, provider, units, model=model)\n',
        '    cost_micro = price_micro_usd(kind, provider, units, model=model)\n'
        '    if cost_micro is None:\n        return {}\n',
        "test_an_unpriced_provider_is_recorded_and_counted_as_uncosted",
        CAPS_TESTS,
    ),
    (
        # The tempting fix for the case that caught the author of these tests: a
        # model name the table has not heard of. Guessing the cheapest sibling
        # rate reports a figure nobody measured, and reports it as measured.
        "capabilities: price an unknown model at its cheapest sibling's rate",
        CAPS,
        '    if rate is None:\n        # The provider is priced but this *model* is not.',
        '    if rate is None:\n        rate = min(entry.prices.values())\n'
        '    if False:\n        # The provider is priced but this *model* is not.',
        "test_an_unknown_model_on_a_priced_provider_is_uncosted",
        CAPS_TESTS,
    ),
    (
        # Must stay GREEN. The distinction between a known zero and an unknown
        # price is stated in prose immediately above the code that implements it,
        # and prose cannot fail - so if rewording it turns anything red, a test
        # is matching on a comment instead of on behaviour.
        "capabilities: reword the comment about the load-bearing None (must stay GREEN)",
        CAPS,
        '        # None here is load-bearing and must not become 0: it is what makes the\n',
        '        # Passing None through matters here and it must not turn into 0: that is\n',
        None,
        CAPS_TESTS,
    ),
]


def main() -> int:
    only = ""
    args = sys.argv[1:]
    if args and args[0] == "--only":
        if len(args) < 2:
            print("--only needs a substring", file=sys.stderr)
            return 2
        only = args[1]

    selected = [m for m in MUTATIONS if not only or only in m[0]]
    if not selected:
        print(f"no mutation label contains {only!r}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for label, target, old, new, expect, tests in selected:
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
                [sys.executable, "-m", "pytest", tests, "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=900,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: a comment is not supposed to trip "
                                    f"anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            marker = "BAD" if failures and failures[-1].startswith(f"{label}:") else "ok "
            print(f"{marker} {label}: {verdict}")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    scope = f" (filtered to {only!r}; {len(MUTATIONS)} exist)" if only else ""
    print(f"All {len(selected)} mutations behaved as specified{scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
