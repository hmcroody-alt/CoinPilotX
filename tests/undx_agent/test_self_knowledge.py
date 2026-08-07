"""Server-authoritative UNDX self-knowledge: identity + real capability status.

Locks in that clients get company facts and capability availability from the
server, derived from the capability registry (the executable allowlist), not from
hard-coded client metadata. Deterministic — asserts on composed data, not model
output.
"""

from __future__ import annotations

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore[assignment]

from services import undx_self_knowledge as sk
from services import undx_capability_registry as registry
from services.undx_agent_contracts import ConfirmationPolicy


def test_reports_company_and_founder():
    d = sk.self_knowledge()
    assert d["company"]["legal_name"] == "CoinPlotXAI Inc."
    assert d["company"]["founder"]["name"] == "Roody Cherie"
    assert d["company"]["founder"]["title"] == "Founder & CEO"
    assert d["assistant"]["name"] == "UNDX"


def test_inventory_matches_registry_size():
    inv = sk.capability_inventory()
    assert len(inv) == len(registry.REGISTRY)
    assert len(inv) > 0


def test_every_listed_capability_is_available_and_coherent():
    for view in sk.capability_inventory():
        assert view["status"] == "AVAILABLE"  # registry only holds executable actions
        assert view["executionMode"] in ("READ", "EXECUTE")
        if view["executionMode"] == "READ":
            assert view["requiresVerification"] is False
            assert view["receiptRequired"] is False
        else:
            # Writes are verified and receipted.
            assert view["requiresVerification"] is True
            assert view["receiptRequired"] is True


def test_execution_mode_and_confirmation_track_the_registry():
    inv = {v["capability_id"]: v for v in sk.capability_inventory()}
    for cid, spec in registry.REGISTRY.items():
        view = inv[cid]
        assert view["executionMode"] == ("EXECUTE" if spec.is_write else "READ")
        assert view["requiresConfirmation"] == (spec.confirmation != ConfirmationPolicy.NEVER)


def test_counts_are_internally_consistent():
    counts = sk.self_knowledge()["capabilities"]["counts"]
    assert counts["read_only"] + counts["write"] == counts["total"]
    assert counts["requires_confirmation"] <= counts["total"]
    assert sum(counts["by_domain"].values()) == counts["total"]


def test_honesty_block_lists_unfabricated_facts():
    honesty = sk.self_knowledge()["honesty"]
    assert "revenue" in honesty["never_fabricates"]
    assert "valuation" in honesty["never_fabricates"]
    assert "not executable yet" in honesty["capability_rule"]


def test_payload_leaks_no_execution_internals():
    # The client payload must not expose per-capability execution internals: no
    # executor/verifier/permission KEYS, and none of their registry VALUES.
    internal_keys = {"executor", "verifier", "permission", "native_route",
                     "audit_category", "undo_capability_id"}
    for view in sk.capability_inventory():
        assert internal_keys.isdisjoint(view.keys())

    import json
    blob = json.dumps(sk.self_knowledge())
    internal_values = set()
    for spec in registry.REGISTRY.values():
        for val in (spec.executor, spec.verifier, spec.permission):
            if val:
                internal_values.add(str(val))
    for val in internal_values:
        assert val not in blob, f"internal value leaked: {val!r}"


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-q"]))
    _fns = sorted((n, f) for n, f in list(globals().items())
                  if n.startswith("test_") and callable(f))
    _passed = _failed = 0
    for _name, _fn in _fns:
        try:
            _fn()
            _passed += 1
        except Exception as exc:  # noqa: BLE001
            _failed += 1
            print(f"FAIL {_name}: {exc}")
    print(f"RESULT pass={_passed} fail={_failed}")
    raise SystemExit(1 if _failed else 0)
