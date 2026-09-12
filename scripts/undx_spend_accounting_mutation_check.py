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
EMBED = "services/undx_embedding_service.py"
IMAGE = "services/pulse_ai/automated_image_pipeline.py"

COST_TESTS = "tests/test_undx_cost_budget.py"
CAPS_TESTS = "tests/test_undx_capabilities.py"
EMBED_TESTS = "tests/undx_agent/test_embedding_wire_contract.py"
IMAGE_TESTS = "tests/test_pulse_insight_image_pipeline.py"

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
    (
        # `if reported_cost_usd:` reads identically to `is not None` at a glance and
        # differs on exactly one value. A provider stating it charged nothing has
        # told us something; treating 0.0 as "said nothing" moves a measured zero
        # into the unpriced column, which is the same collapse as the first
        # mutation in this list arriving from the other direction.
        "capabilities: read a reported zero as no report at all",
        CAPS,
        '    if reported_cost_usd is not None:\n',
        '    if reported_cost_usd:\n',
        "test_a_reported_zero_is_a_measured_zero",
        CAPS_TESTS,
    ),
    (
        # Precedence inverted. The table is a price someone read on a date and the
        # report is a measurement of this call, so trusting the table means a price
        # change nobody has noticed yet makes the ledger quietly wrong while every
        # test about "is it priced" stays green.
        "capabilities: prefer the price table over the provider's own figure",
        CAPS,
        '            reported_usable = cost_micro >= 0\n',
        '            reported_usable = False\n',
        "test_a_reported_cost_beats_the_table",
        CAPS_TESTS,
    ),
    (
        # The disagreement this branch was written to remove. The embedding adapter
        # returns None for an unusable report and so lands on the table; if this
        # layer recorded unknown instead, the same garbled response would be costed
        # differently depending on which layer noticed it, and only one of the two
        # is covered by any caller's tests.
        "capabilities: record an unusable report as unknown instead of using the table",
        CAPS,
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        '    if reported_cost_usd is None:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        "test_an_unusable_report_falls_back_to_the_table_not_to_unknown",
        CAPS_TESTS,
    ),
    (
        # "Fall back to the table" implemented as "fall back to zero" - the shape
        # that passes every priced-provider test in the suite and silently reports
        # unpriced image spend as free.
        "capabilities: fall back to zero rather than to the table",
        CAPS,
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model) or 0\n',
        "test_an_unusable_report_on_an_unpriced_provider_is_still_unknown",
        CAPS_TESTS,
    ),
    (
        # The state this adapter was in before this phase: the provider reports what
        # it charged and the adapter throws it away, pricing from a table instead.
        "embedding: discard the provider's reported cost again",
        EMBED,
        '                reported_cost_usd=_reported_cost_usd(body),\n',
        '',
        "test_the_provider_reported_cost_beats_the_price_table",
        EMBED_TESTS,
    ),
    (
        # Not "stop metering" - meter under the wrong kind. Dropping the call is
        # obvious in a report that suddenly has no embedding row; folding it into
        # chat is invisible, because chat is the number everyone already reads and
        # it is supposed to be the large one.
        "embedding: meter the call as chat",
        EMBED,
        '                undx_capabilities.CALL_KIND_EMBEDDING,\n',
        '                undx_capabilities.CALL_KIND_CHAT,\n',
        "test_a_successful_batch_is_recorded_as_embedding_not_chat",
        EMBED_TESTS,
    ),
    (
        # Meter a different number from the one the budget restrains on. Both
        # figures exist at this line and they agree today only because the same
        # variable is passed to both, which is the point.
        "embedding: meter the batch size instead of the billed tokens",
        EMBED,
        '                input_tokens=billed,\n',
        '                input_tokens=len(indices),\n',
        "test_the_metered_token_count_is_the_one_the_budget_restrains_on",
        EMBED_TESTS,
    ),
    (
        # The state this call site was in before this phase: a paid image provider
        # that billed without leaving a trace. The mutation is deletion rather than
        # corruption because that is the real regression risk here - the site is
        # behind `AUTOMATED_IMAGES_ENABLED = False`, so nothing in production
        # notices if a future edit drops it.
        "image: stop metering image generations entirely",
        IMAGE,
        '        undx_capabilities.record_spend(\n'
        '            undx_capabilities.CALL_KIND_IMAGE, self.name, units=1, model=self.model,\n'
        '        )\n',
        '',
        "test_a_generated_image_is_metered_under_its_own_kind",
        IMAGE_TESTS,
    ),
    (
        # Same laundering as the embedding case. An image folded into chat is
        # invisible; an image missing from the report is at least a hole with a
        # shape.
        "image: meter the generation as chat",
        IMAGE,
        'undx_capabilities.CALL_KIND_IMAGE, self.name',
        'undx_capabilities.CALL_KIND_CHAT, self.name',
        "test_a_generated_image_is_metered_under_its_own_kind",
        IMAGE_TESTS,
    ),
    (
        # Move the metering above the base64 validation. Reads like an improvement
        # - "count the request, we were billed for it either way" - and it is a
        # defensible position, but it silently redefines the image count from
        # "pictures we received" to "requests we sent" without renaming anything.
        # The census records the billing edge as a known gap instead.
        "image: count an attempt that decoded to nothing as an image received",
        IMAGE,
        '        try:\n'
        '            content = base64.b64decode(encoded, validate=True)\n'
        '        except Exception as exc:\n'
        '            raise ImagePipelineError("image_provider_invalid_base64") from exc\n',
        '        undx_capabilities.record_spend(\n'
        '            undx_capabilities.CALL_KIND_IMAGE, self.name, units=1, model=self.model,\n'
        '        )\n'
        '        try:\n'
        '            content = base64.b64decode(encoded, validate=True)\n'
        '        except Exception as exc:\n'
        '            raise ImagePipelineError("image_provider_invalid_base64") from exc\n',
        "test_a_failed_generation_is_not_recorded_as_an_image_received",
        IMAGE_TESTS,
    ),
    (
        # Price the default model regardless of what the deploy is pointed at. Reads
        # as a simplification and is invisible today, because OpenAI Images has an
        # empty price table so every model is equally unpriced. It stops being
        # invisible the day one image model is priced and another is not.
        "image: price the default model instead of the configured one",
        IMAGE,
        'units=1, model=self.model,',
        'units=1, model="gpt-image-1",',
        "test_the_model_priced_is_the_effective_model_not_the_default",
        IMAGE_TESTS,
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
