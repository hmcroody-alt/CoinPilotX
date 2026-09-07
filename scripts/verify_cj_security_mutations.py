#!/usr/bin/env python3
"""Reproducible CJ anti-vacuity probes: real tests, isolated runtime mutants.

Uses the invoking Python/pytest runtime and zero network calls. Each case first
runs unchanged target tests in a fresh subprocess. Only a passing baseline plus
pytest assertion failure under the mutant counts as killed; collection/runtime
errors never count. No production/test source file is modified. Root may append
additional named cases to MUTATIONS for fulfillment/webhook safety oracles.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    {
        "name": "remove_canonical_business_ownership_guard",
        "tests": ["tests/business_os/test_cj_connections.py::test_authorization_before_provider_or_secret_lookup"],
        "patch": "from services.business_os.suppliers import connections as c\nc.store_service._require_biz_permission = lambda *a, **k: 'owner'",
    },
    {
        "name": "return_access_token_from_connection_projection",
        "tests": ["tests/business_os/test_cj_connections.py::test_connect_verifies_settings_and_explicit_shop_before_secure_store"],
        "patch": "from services.business_os.suppliers import connections as c\noriginal = c._public\nc._public = lambda row: dict(original(row), access_token='fixture-access-A')",
    },
    {
        "name": "remove_vault_cryptographic_tenant_binding",
        "tests": ["tests/business_os/test_cj_vault.py::test_aad_each_tenant_dimension_is_authenticated"],
        "patch": "from services.business_os.suppliers import vault\nvault._aad = lambda **scope: b'no tenant binding'",
    },
    {
        "name": "remove_adapter_sandbox_guard",
        "tests": ["tests/business_os/test_cj_adapter.py::test_sandbox_mutation_and_money_autopay_are_rejected_before_network"],
        "patch": "from services.business_os.suppliers import policy\npolicy.require_sandbox = lambda payload: None",
    },
    {
        "name": "convert_unknown_inventory_to_in_stock",
        "tests": ["tests/business_os/test_cj_adapter.py::test_inventory_never_fabricates_verified_stock"],
        "patch": """from services.business_os.suppliers.cj import CJAdapter
original = CJAdapter._warehouse_stock
def mutation(data, *, variant):
    result = original(data, variant=variant)
    if result['state'] == 'UNKNOWN':
        result['state'] = 'IN_STOCK'
    return result
CJAdapter._warehouse_stock = staticmethod(mutation)
""",
    },
    {
        "name": "retry_provider_429_without_backoff",
        "tests": ["tests/business_os/test_cj_adapter.py::test_provider_failure_suspension_429_no_retries_or_inventory_rewrite"],
        "patch": """from services.business_os.suppliers.cj import CJAdapter
from services.business_os.suppliers.errors import SupplierError
original = CJAdapter._request
def mutation(self, *args, **kwargs):
    try:
        return original(self, *args, **kwargs)
    except SupplierError as failure:
        if failure.code == 'RATE_LIMITED':
            return original(self, *args, **kwargs)
        raise
CJAdapter._request = mutation
""",
    },
    {
        "name": "accept_invalid_webhook_signature",
        "tests": ["tests/business_os/test_cj_webhooks.py::test_spoof_rejected_before_persistence"],
        "patch": "from services.business_os.suppliers import webhooks\nwebhooks.verify = lambda *args, **kwargs: None",
    },
    {
        "name": "blindly_retry_ambiguous_supplier_create",
        "tests": ["tests/business_os/test_cj_fulfillment.py::test_timeout_after_provider_success_never_retries_post"],
        "patch": """from services.business_os.suppliers import fulfillment
original = fulfillment.settle
def mutation(intent, state, **kwargs):
    if state == 'UNKNOWN':
        state = 'READY'
    return original(intent, state, **kwargs)
fulfillment.settle = mutation
""",
    },
]


def probe(tests, patch=""):
    child = """import contextlib, io, json
import pytest
exec(PATCH)
class Evidence:
    assertions = 0
    errors = 0
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if report.failed:
            if call.when == 'call' and call.excinfo and call.excinfo.typename in ('AssertionError', 'Failed'):
                self.assertions += 1
            else:
                self.errors += 1
evidence = Evidence()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    result = pytest.main(['-q', *TESTS], plugins=[evidence])
print(json.dumps({'pytest_exit': int(result), 'assertion_failures': evidence.assertions, 'other_errors': evidence.errors}))
""".replace("PATCH", repr(patch)).replace("TESTS", repr(tests))
    process = subprocess.run([sys.executable, "-c", child], cwd=ROOT,
                             capture_output=True, text=True, timeout=45)
    if process.returncode != 0:
        return None
    try:
        return json.loads(process.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, KeyError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Run one named case (repeatable).")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    names = {case["name"] for case in MUTATIONS}
    if args.list:
        print("\n".join(case["name"] for case in MUTATIONS))
        return 0
    if args.case and set(args.case) - names:
        parser.error("Unknown mutation case.")
    results = []
    for case in MUTATIONS:
        if args.case and case["name"] not in args.case:
            continue
        baseline = probe(case["tests"])
        baseline_ok = baseline is not None and baseline["pytest_exit"] == 0
        mutant = probe(case["tests"], case["patch"]) if baseline_ok else None
        result = {"name": case["name"], "baseline": baseline, "mutant": mutant,
                  "killed": bool(baseline_ok and mutant and mutant["pytest_exit"] == 1
                                 and mutant["assertion_failures"] > 0 and mutant["other_errors"] == 0)}
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
    print(json.dumps({"cases": len(results), "killed": sum(row["killed"] for row in results)}, sort_keys=True))
    return 0 if results and all(row["killed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
