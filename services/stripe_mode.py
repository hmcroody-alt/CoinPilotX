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

The same asymmetry applies to a *pair* of keys that disagree, which is the
failure a half-finished test-mode rollout actually produces. A deployment
holding ``sk_test_`` on the server and ``pk_live_`` in the browser is not in
test mode in any useful sense — the page tokenises a real card — yet reading
the secret key alone reports ``test``, ``may_move_real_money: false`` and
``test_mode_ready: true``. That is the one combination that says "safe to run
the card suite" while the card is real, so a provable disagreement is its own
mode (:data:`MIXED`) and it ranks with live.

Which publishable key to read is not obvious either. ``bot.py`` resolves it as
``NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`` *first* and falls back to
``STRIPE_PUBLISHABLE_KEY``, so that is the precedence used here. Classifying
the variable that is set rather than the variable that wins would describe a
value no browser ever receives.

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

#: Checked before :data:`PUBLISHABLE_KEY_ENV_VAR` because ``bot.py`` checks it
#: first. Residue of a Next.js front end that no longer exists, still wired.
PUBLISHABLE_KEY_OVERRIDE_ENV_VAR = "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY"

TEST = "test"
LIVE = "live"
UNCONFIGURED = "unconfigured"
UNRECOGNIZED = "unrecognized"
#: The server and the browser are provably talking to different Stripes.
MIXED = "mixed"

#: Prefixes Stripe issues. ``rk_`` is a restricted key, which carries the same
#: environment marker as the standard one and is otherwise easy to miss.
_TEST_PREFIXES = ("sk_test_", "rk_test_")
_LIVE_PREFIXES = ("sk_live_", "rk_live_")

#: Publishable keys have no restricted form, so there is only one spelling each.
_PUBLISHABLE_TEST_PREFIXES = ("pk_test_",)
_PUBLISHABLE_LIVE_PREFIXES = ("pk_live_",)

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


def _classify(value: str, test_prefixes: tuple, live_prefixes: tuple) -> str:
    if not value:
        return UNCONFIGURED
    if value.startswith(test_prefixes):
        return TEST
    if value.startswith(live_prefixes):
        return LIVE
    return UNRECOGNIZED


def publishable_key_env_var() -> str:
    """Which variable actually supplies the browser's key.

    Named rather than assumed so a credential handoff points the owner at the
    variable that wins. Telling someone to fix ``STRIPE_PUBLISHABLE_KEY`` while
    ``NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`` overrides it is a wasted round trip
    and leaves the live key in place.
    """
    if _value(PUBLISHABLE_KEY_OVERRIDE_ENV_VAR):
        return PUBLISHABLE_KEY_OVERRIDE_ENV_VAR
    return PUBLISHABLE_KEY_ENV_VAR


def secret_mode() -> str:
    """What the server-side key says. ``test``/``live``/``unrecognized``/absent."""
    return _classify(_value(SECRET_KEY_ENV_VAR), _TEST_PREFIXES, _LIVE_PREFIXES)


def publishable_mode() -> str:
    """What the key handed to browsers says, resolved at ``bot.py``'s precedence."""
    return _classify(
        _value(publishable_key_env_var()),
        _PUBLISHABLE_TEST_PREFIXES,
        _PUBLISHABLE_LIVE_PREFIXES,
    )


def is_mixed() -> bool:
    """Do the two keys provably name different Stripes?

    Only a *provable* disagreement counts: both sides classified, and
    classified differently. An unreadable publishable key is not a
    contradiction, it is an unknown — it blocks :func:`test_mode_ready`
    without claiming to know which environment it belongs to.
    """
    secret, publishable = secret_mode(), publishable_mode()
    if secret not in (TEST, LIVE) or publishable not in (TEST, LIVE):
        return False
    return secret != publishable


def mode() -> str:
    """``test``, ``live``, ``mixed``, ``unrecognized`` or ``unconfigured``.

    Read per call rather than at import, so a process acts on the value it has
    rather than the value it booted with, and so a test can set it without
    reloading the module.

    ``mixed`` is checked before the secret key is reported on its own, because
    the dangerous direction of a mismatch (``sk_test_`` with ``pk_live_``) is
    the one where the secret key alone would answer ``test``.
    """
    key = _value(SECRET_KEY_ENV_VAR)
    if not key:
        return UNCONFIGURED
    if is_mixed():
        return MIXED
    return _classify(key, _TEST_PREFIXES, _LIVE_PREFIXES)


def is_test_mode() -> bool:
    """True only for a key that proves itself a test key."""
    return mode() == TEST


def may_move_real_money() -> bool:
    """Could a Stripe call from this process touch real funds?

    ``unrecognized`` answers yes. It is the honest answer to a key nobody can
    classify, and it is the answer that fails in the survivable direction: a
    test-mode run refused because of an unreadable key wastes an afternoon,
    while a live transfer made under the belief it was a test does not.

    ``mixed`` answers yes for the same reason and a more concrete one: one of
    the two keys *is* a live key, so one half of the checkout is reaching live
    Stripe no matter which half it is.
    """
    return mode() in (LIVE, UNRECOGNIZED, MIXED)


def missing_test_mode_variables() -> list[str]:
    """Exactly which variables a Stripe test-mode run still needs.

    Returned as names, in a fixed order, so a handoff to the owner is a list of
    variables to set rather than a paragraph to interpret. A variable that is
    present but holds a live key counts as missing: it is set to the wrong
    thing, which is a different problem from being unset but the same amount of
    not-ready, and saying "present" about it would be the more misleading half.
    """
    publishable_var = publishable_key_env_var()
    missing = [
        publishable_var if name == PUBLISHABLE_KEY_ENV_VAR else name
        for name in TEST_MODE_REQUIRED_ENV_VARS
        if not _value(publishable_var if name == PUBLISHABLE_KEY_ENV_VAR else name)
    ]
    key = _value(SECRET_KEY_ENV_VAR)
    if key and not key.startswith(_TEST_PREFIXES) and SECRET_KEY_ENV_VAR not in missing:
        missing.insert(0, SECRET_KEY_ENV_VAR)
    # The same rule, applied to the key the browser gets. A `pk_live_` sitting
    # in front of a `sk_test_` server is the whole reason `mixed` exists; a
    # publishable key nobody can classify is not ready either, because the
    # module's one doctrine is that a deployment which cannot prove it is in
    # test mode is not in test mode.
    if publishable_mode() in (LIVE, UNRECOGNIZED) and publishable_var not in missing:
        missing.append(publishable_var)
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
    publishable_var = publishable_key_env_var()
    configured = {name: bool(_value(name)) for name in TEST_MODE_REQUIRED_ENV_VARS}
    configured[PUBLISHABLE_KEY_OVERRIDE_ENV_VAR] = bool(
        _value(PUBLISHABLE_KEY_OVERRIDE_ENV_VAR))
    return {
        "mode": current,
        "is_test_mode": current == TEST,
        "may_move_real_money": may_move_real_money(),
        # Both halves, separately. A single `mode` cannot say *which* side of a
        # mismatch is the live one, and that is the first thing an operator
        # looking at `mixed` needs to know.
        "secret_mode": secret_mode(),
        "publishable_mode": publishable_mode(),
        "publishable_key_env_var": publishable_var,
        "keys_disagree": is_mixed(),
        "configured": configured,
        "missing_for_test_mode": missing_test_mode_variables(),
        "test_mode_ready": test_mode_ready(),
    }
