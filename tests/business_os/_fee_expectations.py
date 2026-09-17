"""Fee expectations derived from the policy, not hardcoded.

Why this file exists
--------------------
Seven suites in this directory asserted a seller keeps 90% of an order, pinning
the 1000 bps ``DEFAULT_FEE_BPS`` fallback that the Business OS marketplace lane
used whenever the ``MARKETPLACE_STANDARD_V1`` owner gates were shut. That rate
was never disclosed to a seller and production never charged it — both
marketplace lanes have zero orders and zero settlements — so the commission now
comes from the versioned policy and nothing else.

A hardcoded 3600 would have to be edited in seven places the day the owner opens
the gates, and each one that got missed would read as a real regression. These
helpers ask the same authority checkout asks, so they stay true on both sides of
the gates, and a suite that wants to pin the 5% arithmetic itself opens the gates
explicitly with ``policy_gates_open``.
"""

from __future__ import annotations

import contextlib
import os

from services.business_os.marketplace import policy
from services.business_os.marketplace.orders import _fee_split

_GATE_ENV = (
    "MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
    "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",
    "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT",
)


def platform_fee(gross_cents: int) -> int:
    """The commission the policy takes from this gross, at today's rate."""
    return _fee_split(int(gross_cents), policy.platform_fee_bps())[0]


def seller_net(gross_cents: int) -> int:
    """What the seller keeps from this gross, at today's rate."""
    return _fee_split(int(gross_cents), policy.platform_fee_bps())[1]


@contextlib.contextmanager
def policy_gates_open():
    """Run a block with the fee policy in force, restoring the environment after.

    For suites that need to prove the 5% arithmetic rather than merely track it.
    """
    previous = {name: os.environ.get(name) for name in _GATE_ENV}
    os.environ.update({
        "MARKETPLACE_STANDARD_V1_OWNER_APPROVED": "1",
        "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY": "1",
        "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT": "2000-01-01T00:00:00Z",
    })
    try:
        assert policy.platform_fee_bps() == policy.PROPOSED_PLATFORM_FEE_BPS
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
