"""Fail-closed rollout policy. These switches never authorize real funding.

CJ Developer Support confirmed the standard API model in writing on 2026-09-09
(recorded in `docs/cj/CJ_PROVIDER_POLICY_CONFIRMATION.md`). A merchant generates
an API key in their own CJ account, hands it to PulseSoc, and PulseSoc may hold
it server-side and call CJ for that merchant. No separate provider approval
gates that, and the same model repeats independently per merchant.

Those are provider facts, so they are constants here rather than environment
switches. A flag implies a deployment may choose otherwise; a merchant's right
to use their own credential is not a thing a deployment gets to toggle, and a
switch would only invite someone to answer the question wrongly later. What
stays an environment switch is what genuinely varies per deployment: whether
this environment may reach CJ at all, and whether it may spend money.

What is NOT confirmed, and what the constants below deliberately do not claim:
any partner, platform, white-label or OEM agreement; a master credential
reaching other merchants' accounts; an IP whitelist exemption. The real limit on
multi-merchant scale is no longer a policy question at all -- it is CJ's
infrastructure ceiling of three accounts and ten business calls per second per
outbound IP, enforced in `quota.py` and attested by `egress_ip_attested()`.
"""

import os

from services.business_os.suppliers.errors import SupplierError

# Confirmed by the provider; not deployment-tunable.
STANDARD_API_MODEL_CONFIRMED = True
MERCHANT_OWNED_CREDENTIAL_CUSTODY_CONFIRMED = True
MULTI_MERCHANT_STANDARD_MODEL_CONFIRMED = True
PARTNER_AGREEMENT_REQUIRED = False
PARTNER_AGREEMENT_HELD = False  # No such agreement exists. Never claim one in UI.

# CJ's hard infrastructure ceilings, per outbound IP. No whitelist exemption.
MAX_CJ_ACCOUNTS_PER_EGRESS_IP = 3
MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP = 10


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "on", "yes"}


def require_enabled() -> None:
    if not enabled("BUSINESS_OS_SUPPLIERS_CJ"):
        raise SupplierError("disabled", http_status=404)


def _sandbox_mode() -> bool:
    return os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() == "SANDBOX"


def egress_ip_attested() -> bool:
    """Has an operator attested that `CJ_EGRESS_GROUP` really is one stable IP?

    `quota.py` enforces CJ's three-accounts-per-IP ceiling by counting rows per
    egress group -- but an egress group is a label a human types, and CJ counts
    actual outbound IPs. Those agree only if someone checked, and on Railway
    they have been checked and they do not: under one label, the backend and the
    supplier worker were observed egressing from two different addresses at the
    same moment, and the backend's address changed across a redeploy. The
    measurement is in `docs/cj/CJ_EGRESS_ARCHITECTURE.md`.

    Today that gap is conservative by accident -- one label spanning several
    addresses puts *fewer* accounts on each than the counter believes -- but a
    topology we did not design is not a control we can rely on, and it says
    nothing about whether the address is shared with other tenants who also use
    CJ. This flag is the difference between "we enforce the limit" and "we
    enforce the limit against something we have verified".

    It gates multi-merchant scale, not the single-merchant case, because one
    account cannot exceed a three-account ceiling however the addresses fall.
    """
    return enabled("CJ_EGRESS_IP_ATTESTED")


def multi_merchant_scale_blocker() -> str | None:
    """Why hosted multi-merchant CJ is not yet safe to scale, or None.

    Deliberately no longer a policy answer. CJ confirmed the multi-merchant
    standard model needs no approval, so the honest blocker is the one that
    survives that confirmation: we cannot yet prove which outbound IP our calls
    leave from, and CJ's ceiling is expressed per IP.
    """
    if not egress_ip_attested():
        return "BLOCKED_BY_EGRESS_ARCHITECTURE"
    return None


def content_usage_authorized() -> bool:
    """CJ confirmed: store and display CJ media/copy to sell those products.

    Confirmed for merchant-facing tooling and for selling the CJ products the
    content belongs to -- no attribution, no per-supplier permission, no fee.
    Scoped on purpose: this is not a licence to redistribute CJ media for
    anything unrelated to selling the product it came from, so the name says
    "usage" rather than "redisplay approved" and callers stay honest about it.

    Confirmation of rights is not a reason to trust the bytes. Provider content
    remains untrusted input and must still be sanitized.
    """
    return True


def require_network() -> None:
    # There used to be a provider-approval gate here, and CJ has now answered
    # the question it was standing in for: a merchant's own API key, used from
    # our hosted backend with their consent, is the standard model and needs no
    # separate approval. Keeping the gate would have meant asserting a
    # provider-wide approval nobody has in order to do the ordinary thing.
    #
    # What remains is the rollout switch, which is a genuine per-deployment
    # fact: production leaves CJ_NETWORK_ENABLED unset and therefore stays dark
    # regardless of anything above.
    if not enabled("CJ_NETWORK_ENABLED"):
        raise SupplierError("provider_network_disabled", http_status=503)


def require_sandbox(payload: dict) -> None:
    if os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() != "SANDBOX":
        raise SupplierError("production_fulfillment_disabled", http_status=409)
    if enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"):
        raise SupplierError("production_fulfillment_disabled", http_status=409)
    if not isinstance(payload, dict) or type(payload.get("isSandbox")) is not int or payload["isSandbox"] != 1:
        raise SupplierError("sandbox_flag_required", http_status=400)


def require_live(payload: dict) -> None:
    """Refuse unless this deployment may place a real, payable CJ order.

    The mirror of :func:`require_sandbox`, and deliberately not its inverse: a
    sandbox order is refused by two conditions, a live one by four, and the
    first of them is not configuration at all. :func:`live_fulfillment_path_exists`
    is checked before anything an operator can set, so no combination of Railway
    variables reaches a live order without a code change first.

    ``isSandbox`` must be ``0`` and must be present *as an int*. CJ's default is
    not ours to assume and an absent field is indistinguishable from a
    serialisation bug -- the whole difference between a test order and a real
    shipment is one key, so it is stated rather than implied. ``type(...) is
    int`` rather than ``isinstance`` because ``False`` is an ``int`` to
    ``isinstance``, and ``False`` is not an answer to this question.
    """
    if not live_fulfillment_path_exists():
        raise SupplierError("live_fulfillment_not_available", http_status=409)
    if os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() not in {"LIVE", "PRODUCTION"}:
        raise SupplierError("live_fulfillment_not_available", http_status=409)
    if not enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"):
        raise SupplierError("live_fulfillment_not_available", http_status=409)
    if not isinstance(payload, dict) or type(payload.get("isSandbox")) is not int or payload["isSandbox"] != 0:
        raise SupplierError("live_flag_required", http_status=400)


def require_funding_disabled() -> None:
    raise SupplierError("funding_not_ready", http_status=409)


#: Sandbox orders are accepted and nothing is charged or shipped.
ENVIRONMENT_SANDBOX = "SANDBOX"
#: Real CJ orders are accepted and real money moves. Unreachable today by
#: construction, and named here anyway so the value has one definition rather
#: than being invented at a call site the day someone builds the live path.
ENVIRONMENT_LIVE = "LIVE"
#: Neither. The configuration does not describe any environment in which a
#: supplier order can be created, so no order of any kind will be accepted.
ENVIRONMENT_DISABLED = "DISABLED"

#: Every value ``fulfillment_environment`` can return, as a set.
#:
#: Declared because these names are *not* supplier-order states, and the test
#: that checks every backend-written state has merchant-facing copy works by
#: collecting upper-case literals and subtracting the vocabularies that are
#: something else. That subtraction reads this set rather than retyping the
#: names, so an environment added here cannot be mistaken for an order state,
#: and — the direction that actually bites — a *state* cannot be hidden from
#: that test by being spelled like an environment.
ENVIRONMENTS = frozenset({ENVIRONMENT_SANDBOX, ENVIRONMENT_LIVE, ENVIRONMENT_DISABLED})


def live_fulfillment_path_exists() -> bool:
    """May this deployment place a real, payable CJ order? No -- and this line is why.

    The answer used to be ``False`` because the code did not exist. It now does:
    ``cj.CJAdapter.create_live_fulfillment`` is written, dispatch selects it
    from the intent's own frozen ``isSandbox``, and the whole path is exercised
    by tests that flip this function. What has not happened is the decision.

    So this constant has changed meaning and kept its value. It is no longer a
    statement about which functions are defined -- it is the money approval
    itself, expressed as the one line that has to be edited, reviewed and
    deployed before a real order can be placed. That is the property worth
    having: an operator with every Railway variable in the account cannot reach
    a live order, because the first thing :func:`require_live` checks is not
    configuration.

    Flipping it to ``True`` is not sufficient either, and that is deliberate.
    ``require_live`` still wants ``CJ_ENVIRONMENT_MODE`` and
    ``PRODUCTION_CJ_FULFILLMENT_ENABLED``, and ``fulfillment.dispatch`` still
    refuses to send a live intent whose ``funding_state`` is not ``FUNDED`` --
    a value nothing in this codebase writes. Turning on live ordering is
    therefore three deliberate acts by two different kinds of person, none of
    which can happen by accident.

    Still a constant rather than an inferred check, for the reason it always
    was: reflecting over the provider for a ``create_live_*`` method would have
    made the badge start claiming LIVE the moment such a method was *defined*,
    which is exactly the moment that has now arrived and exactly the claim that
    would now be wrong.
    """
    return False


def fulfillment_environment() -> str:
    """What the order path will actually do, asked of the gate that decides.

    Not a second reading of ``CJ_ENVIRONMENT_MODE``. A badge computed from the
    same environment variable the gate reads is still a parallel implementation
    of the gate, and the two drift the first time one grows a condition the
    other does not mirror -- which is exactly how a screen comes to show
    ``Live`` over a runtime that is refusing live orders. So this probes
    :func:`require_sandbox` itself, with the canonical sandbox payload, and
    reports what it answered.

    The third value is the one that earns its place. Setting
    ``CJ_ENVIRONMENT_MODE=PRODUCTION`` today does not produce a live
    integration: ``require_sandbox`` refuses, ``create_sandbox_fulfillment``
    refuses again on its own environment check, and no order is created at all.
    Labelling that ``LIVE`` would be the precise failure this function exists to
    prevent, and labelling it ``SANDBOX`` would be a different lie -- sandbox
    orders do not work there either. It is ``DISABLED``, which is what an
    operator needs to be told.

    Both gates are probed now that both exist, which keeps the rule intact
    rather than extending it: the label is whichever gate *accepts*, and
    ``DISABLED`` when neither does. Asking only ``require_sandbox`` and then
    reading ``live_fulfillment_path_exists`` would have reintroduced the
    original defect on the live side -- a badge saying ``Live`` over a runtime
    where ``require_live`` is still refusing for a reason the badge never asked
    about.
    """
    try:
        require_sandbox({"isSandbox": 1})
    except SupplierError:
        try:
            require_live({"isSandbox": 0})
        except SupplierError:
            return ENVIRONMENT_DISABLED
        return ENVIRONMENT_LIVE
    return ENVIRONMENT_SANDBOX


def safe_status() -> dict:
    return {
        "enabled": enabled("BUSINESS_OS_SUPPLIERS_CJ"),
        "environment": fulfillment_environment(),
        "configuration_valid": _sandbox_mode() and not enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"),
        "network_enabled": enabled("CJ_NETWORK_ENABLED"),
        "standard_api_model_confirmed": STANDARD_API_MODEL_CONFIRMED,
        # Reported so no dashboard can quietly grow a partner badge out of a
        # technical confirmation. CJ confirmed how the API may be used; that is
        # not an agreement, and this field exists to keep the two apart.
        "partner_agreement_held": PARTNER_AGREEMENT_HELD,
        "content_usage_authorized": content_usage_authorized(),
        "max_cj_accounts_per_egress_ip": MAX_CJ_ACCOUNTS_PER_EGRESS_IP,
        "max_cj_calls_per_second_per_egress_ip": MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP,
        "egress_ip_attested": egress_ip_attested(),
        "multi_merchant_scale_blocker": multi_merchant_scale_blocker(),
        "network_permitted": enabled("CJ_NETWORK_ENABLED"),
        "production_fulfillment_enabled": live_fulfillment_path_exists(),
        "real_funding_enabled": False,
    }
