"""Which Stripe this deployment is talking to, decided once.

Three places already inferred this from the secret key's prefix — the boot log,
``payment_provider.provider_status`` and an admin helper — and all three drew the
same wrong conclusion from the same blind spot: a key that is neither
``sk_test_`` nor ``sk_live_`` was reported as "not configured". A restricted live
key (``rk_live_``) is exactly that shape, and it is a live key. Any reader that
treats "not configured" as "nothing can happen" is then wrong in the one
direction that costs real money.

So the unrecognised case is its own answer here, and it ranks with live rather
than with absent. A deployment that cannot prove it is in test mode is not in
test mode.

This module reads configuration and answers questions. It does not gate
anything by itself: the marketplace card rail is gated by
:mod:`services.marketplace_payment_pause` and money movement by
:mod:`services.marketplace_payout_worker`, and both keep their own switches.
What they gain from here is a mode they all agree on.
"""

from __future__ import annotations

import os

SECRET_KEY_ENV_VAR = "STRIPE_SECRET_KEY"
PUBLISHABLE_KEY_ENV_VAR = "STRIPE_PUBLISHABLE_KEY"
WEBHOOK_SECRET_ENV_VAR = "STRIPE_WEBHOOK_SECRET"
CONNECT_CLIENT_ID_ENV_VAR = "STRIPE_CONNECT_CLIENT_ID"

TEST = "test"
LIVE = "live"
UNCONFIGURED = "unconfigured"
UNRECOGNIZED = "unrecognized"

#: Prefixes Stripe issues. ``rk_`` is a restricted key, which carries the same
#: environment marker as the standard one and is otherwise easy to miss.
_TEST_PREFIXES = ("sk_test_", "rk_test_")
_LIVE_PREFIXES = ("sk_live_", "rk_live_")

#: Everything the marketplace Connect path needs before a test-mode run can
#: prove anything. The Connect client id is included because onboarding a test
#: seller is part of the path being tested, not an optional extra.
TEST_MODE_REQUIRED_ENV_VARS = (
    SECRET_KEY_ENV_VAR,
    PUBLISHABLE_KEY_ENV_VAR,
    WEBHOOK_SECRET_ENV_VAR,
    CONNECT_CLIENT_ID_ENV_VAR,
)


def _value(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def mode() -> str:
    """``test``, ``live``, ``unrecognized`` or ``unconfigured``.

    Read per call rather than at import, so a process acts on the value it has
    rather than the value it booted with, and so a test can set it without
    reloading the module.
    """
    key = _value(SECRET_KEY_ENV_VAR)
    if not key:
        return UNCONFIGURED
    if key.startswith(_TEST_PREFIXES):
        return TEST
    if key.startswith(_LIVE_PREFIXES):
        return LIVE
    return UNRECOGNIZED


def is_test_mode() -> bool:
    """True only for a key that proves itself a test key."""
    return mode() == TEST


def may_move_real_money() -> bool:
    """Could a Stripe call from this process touch real funds?

    ``unrecognized`` answers yes. It is the honest answer to a key nobody can
    classify, and it is the answer that fails in the survivable direction: a
    test-mode run refused because of an unreadable key wastes an afternoon,
    while a live transfer made under the belief it was a test does not.
    """
    return mode() in (LIVE, UNRECOGNIZED)


def missing_test_mode_variables() -> list[str]:
    """Exactly which variables a Stripe test-mode run still needs.

    Returned as names, in a fixed order, so a handoff to the owner is a list of
    variables to set rather than a paragraph to interpret. A variable that is
    present but holds a live key counts as missing: it is set to the wrong
    thing, which is a different problem from being unset but the same amount of
    not-ready, and saying "present" about it would be the more misleading half.
    """
    missing = [name for name in TEST_MODE_REQUIRED_ENV_VARS if not _value(name)]
    key = _value(SECRET_KEY_ENV_VAR)
    if key and not key.startswith(_TEST_PREFIXES) and SECRET_KEY_ENV_VAR not in missing:
        missing.insert(0, SECRET_KEY_ENV_VAR)
    return missing


def test_mode_ready() -> bool:
    return not missing_test_mode_variables()


def status() -> dict:
    """The whole answer in one dict, for a heartbeat or an admin page.

    No key material, no prefixes, no lengths — only whether each variable is
    present and what the secret key says about the environment. A status
    endpoint is the wrong place to make a credential partially guessable.
    """
    current = mode()
    return {
        "mode": current,
        "is_test_mode": current == TEST,
        "may_move_real_money": may_move_real_money(),
        "configured": {name: bool(_value(name)) for name in TEST_MODE_REQUIRED_ENV_VARS},
        "missing_for_test_mode": missing_test_mode_variables(),
        "test_mode_ready": test_mode_ready(),
    }
