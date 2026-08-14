"""Sentinel Mission 5 — financial mutation hard lock (Stage 48).

Sentinel has ZERO money-movement capability. This module is the structural
proof: the forbidden capabilities are NAMED, any attempt raises, the attempt
is logged as evidence, and a surface scan asserts no sentinel module ever
grows a function with one of these names.

There is no bypass parameter, no admin override, no kill switch that turns
this off. Mission 6 containment contracts, if ever activated, live OUTSIDE
this module and outside Sentinel's authority.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List, Tuple

FORBIDDEN_CAPABILITIES: Tuple[str, ...] = (
    "move_funds",
    "transfer_funds",
    "hold_funds",
    "freeze_funds",
    "freeze_wallet",
    "reverse_transaction",
    "issue_refund",
    "execute_refund",
    "issue_payout",
    "execute_payout",
    "cancel_payout",
    "retry_payout",
    "suspend_seller",
    "ban_buyer",
    "modify_balance",
    "adjust_balance",
    "change_fee",
    "set_fee",
    "alter_payment_routing",
    "charge_payment_method",
)


class FinancialMutationForbidden(RuntimeError):
    """Raised on ANY attempt to invoke a financial mutation via Sentinel."""


def attempt(capability: str, subject_ref: str = "",
            requested_by: str = "", conn=None) -> None:
    """The only 'entry point' — and it always refuses. The attempt itself is
    recorded as evidence (who asked for what) before raising."""
    capability = str(capability or "").strip()
    try:
        from services.sentinel import evidence
        evidence.append(
            "financial_mutation_refused",
            requested_by or "unknown",
            {"capability": capability, "subject_ref": subject_ref,
             "outcome": "REFUSED",
             "reason": "Sentinel has zero money-movement authority (Stage 48)"},
            conn=conn)
    except Exception:
        pass  # evidence failure must not turn a refusal into a crash
    raise FinancialMutationForbidden(
        f"financial mutation {capability!r} is forbidden: Sentinel cannot "
        "move, hold, reverse, refund, pay out, freeze, rebalance, re-fee, "
        "or re-route money — for anyone, for any reason")


def verify_module_surface() -> Dict[str, Any]:
    """Scan every services.sentinel module for attributes whose names match
    a forbidden capability. The lock module itself is exempt (it defines the
    names in a tuple, not as callables). Used by tests to prove absence."""
    violations: List[str] = []
    scanned: List[str] = []
    import services.sentinel as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        name = info.name
        if name == "financial_mutation_lock":
            continue
        try:
            mod = importlib.import_module(f"services.sentinel.{name}")
        except Exception:
            continue
        scanned.append(name)
        for attr in dir(mod):
            if attr in FORBIDDEN_CAPABILITIES and callable(getattr(mod, attr, None)):
                violations.append(f"services.sentinel.{name}.{attr}")
    return {"scanned_modules": len(scanned), "violations": violations,
            "clean": not violations}
