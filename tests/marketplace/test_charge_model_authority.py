"""The charge model is 'separate charges and transfers', and it must stay that way.

A destination charge (`transfer_data.destination` + `application_fee_amount`)
settles the seller's cut **at charge time**. That is not a stylistic difference
from what PulseSoc does — it removes the protection window entirely. Money that
Stripe has already split cannot be frozen by `blocker_code`, cannot be held for a
chargeback, and cannot be reversed by the allocator, because by the time the
dispute arrives the seller's share is in the seller's balance.

Every freeze/hold/reverse path built for this marketplace assumes the platform is
holding the money. A destination charge silently exempts whichever lane adopts it
from all of them, and nothing else in the system would report that.

`tests/test_marketplace_cart_lifecycle.py` already asserts the absence of those
two keys in three named files by substring. This module exists because that shape
has three holes:

1. **It is a hardcoded file list.** `services/payment_provider.py` also creates a
   Checkout Session and a PaymentIntent and was never in it.
2. **It matches double-quoted literals.** `'transfer_data'` in single quotes, or
   a key built any other way, reads as clean.
3. **It guards the call sites, not the dict.** `bot.py` spreads
   ``**{k: v for k, v in payment_intent_data.items() if k != "metadata"}`` into
   `PaymentIntent.create`. The destination charge is therefore selected by
   *writing a key into that dict*, which can happen in a different file from the
   call.

So this walks the AST of the whole repository instead: quoting, whitespace and
comments stop mattering, and a charge site added tomorrow in a file nobody
thought to list is covered the day it is written.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# `application_fee` is Stripe's older spelling and is accepted on some resources;
# `on_behalf_of` makes the connected account the settlement merchant, which is a
# different way to reach the same place.
FORBIDDEN_CHARGE_KEYS = {
    "transfer_data",
    "application_fee_amount",
    "application_fee",
    "on_behalf_of",
}

# Resources whose `.create` charges a buyer. A Transfer and a Payout are money
# *leaving* on PulseSoc's initiative and are deliberately not in this set.
CHARGE_RESOURCES = {"PaymentIntent", "Charge", "Session"}

# Dict variables that are splatted into a charge call. A write into one of these
# is indistinguishable from passing the key at the call site.
INTENT_DICT_NAMES = {"payment_intent_data", "intent_data", "intent_kwargs",
                     "charge_kwargs", "session_params", "params"}

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "mobile", "mobile-native",
             "__pycache__", ".claude", "build", "dist", "migrations"}


def _python_files():
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        # Tests are allowed to name these keys - asserting their absence requires
        # writing them down, and the audit scripts describe them in prose.
        if rel.parts[0] in {"tests", "scripts"}:
            continue
        yield rel, path


def _parsed():
    out = []
    for rel, path in _python_files():
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            out.append((rel, ast.parse(source)))
        except SyntaxError:
            # A file that does not parse cannot be constructing a charge.
            continue
    return out


REPO_MODULES = _parsed()


def _dotted(node):
    """Return 'stripe.checkout.Session.create' for the func of a Call."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _charge_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if not name.endswith(".create"):
            continue
        if any(part in CHARGE_RESOURCES for part in name.split(".")):
            yield name, node


def _dict_literal_keys(node):
    if not isinstance(node, ast.Dict):
        return set()
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def test_the_repository_still_has_charge_sites_to_check():
    """A scanner that silently matches nothing is the failure mode this whole
    module is trying to prevent, so prove it found the known ones first."""
    found = {str(rel) for rel, tree in REPO_MODULES for _ in _charge_calls(tree)}
    for expected in ("bot.py",
                     "services/payment_provider.py",
                     "services/marketplace_cart_routes.py",
                     "services/marketplace_offers_routes.py"):
        assert expected in found, f"scanner lost sight of {expected}: {sorted(found)}"


def test_no_charge_site_passes_a_destination_charge_argument():
    """The direct form: a forbidden key named at the call."""
    offences = []
    for rel, tree in REPO_MODULES:
        for name, call in _charge_calls(tree):
            for kw in call.keywords:
                if kw.arg in FORBIDDEN_CHARGE_KEYS:
                    offences.append(f"{rel}:{call.lineno} {name}({kw.arg}=...)")
                # payment_intent_data={"transfer_data": ...} written inline.
                for key in _dict_literal_keys(kw.value) & FORBIDDEN_CHARGE_KEYS:
                    offences.append(f"{rel}:{call.lineno} {name}({kw.arg}={{{key!r}: ...}})")
    assert offences == [], (
        "a destination charge settles the seller's cut at charge time, which "
        "makes the protection window unenforceable:\n" + "\n".join(offences))


def test_nothing_writes_a_destination_charge_key_into_a_splatted_dict():
    """The indirect form, and the one the substring guard cannot see.

    `bot.py` spreads `payment_intent_data` into `PaymentIntent.create`, so
    `payment_intent_data["transfer_data"] = {...}` in any file is a destination
    charge even though the call site stays clean.
    """
    offences = []
    for rel, tree in REPO_MODULES:
        for node in ast.walk(tree):
            # payment_intent_data["transfer_data"] = ...
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Name)
                            and target.value.id in INTENT_DICT_NAMES
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value in FORBIDDEN_CHARGE_KEYS):
                        offences.append(
                            f"{rel}:{node.lineno} {target.value.id}[{target.slice.value!r}] = ...")
                    # payment_intent_data = {"transfer_data": ...}
                    if (isinstance(target, ast.Name)
                            and target.id in INTENT_DICT_NAMES):
                        for key in _dict_literal_keys(node.value) & FORBIDDEN_CHARGE_KEYS:
                            offences.append(f"{rel}:{node.lineno} {target.id} = {{{key!r}: ...}}")
            # payment_intent_data.update({...}) / .setdefault("transfer_data", ...)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if (isinstance(owner, ast.Name) and owner.id in INTENT_DICT_NAMES
                        and node.func.attr in {"update", "setdefault"}):
                    for arg in node.args:
                        if (isinstance(arg, ast.Constant)
                                and arg.value in FORBIDDEN_CHARGE_KEYS):
                            offences.append(
                                f"{rel}:{node.lineno} {owner.id}.{node.func.attr}({arg.value!r}, ...)")
                        for key in _dict_literal_keys(arg) & FORBIDDEN_CHARGE_KEYS:
                            offences.append(
                                f"{rel}:{node.lineno} {owner.id}.{node.func.attr}({{{key!r}: ...}})")
    assert offences == [], (
        "this key reaches Stripe through a splatted dict even though every "
        "charge call site looks clean:\n" + "\n".join(offences))


def test_a_charge_is_never_created_on_the_connected_account():
    """`stripe_account=` executes the call *as* the connected account, which is a
    direct charge: the money never lands in PulseSoc's balance at all, so there
    is nothing to hold and nothing to transfer.

    It is correct and required on a Payout, which is why this is scoped to charge
    resources rather than banned outright.
    """
    offences = []
    for rel, tree in REPO_MODULES:
        for name, call in _charge_calls(tree):
            for kw in call.keywords:
                if kw.arg == "stripe_account":
                    offences.append(f"{rel}:{call.lineno} {name}(stripe_account=...)")
    assert offences == [], (
        "a direct charge never reaches the platform balance:\n" + "\n".join(offences))


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CHARGE_KEYS))
def test_the_scanner_would_actually_catch_each_forbidden_key(key):
    """A positive control per key.

    Without this, a typo in `FORBIDDEN_CHARGE_KEYS` or a scanner that quietly
    stopped walking would leave every assertion above passing vacuously.
    """
    tree = ast.parse(
        "import stripe\n"
        f"stripe.PaymentIntent.create(amount=1, {key}='x')\n"
    )
    hits = [kw.arg for _, call in _charge_calls(tree) for kw in call.keywords
            if kw.arg in FORBIDDEN_CHARGE_KEYS]
    assert hits == [key]


def test_the_one_charge_call_the_ast_cannot_see_through_refuses_at_runtime():
    """`payment_provider.create_payment_intent(**kwargs)` splats an opaque dict
    straight into `PaymentIntent.create`.

    No AST walk can see what is in `**kwargs` - the keys are chosen by a caller,
    possibly built at runtime. It is the single blind spot in everything above,
    so that function refuses the keys itself.
    """
    from services import payment_provider

    for key in sorted(FORBIDDEN_CHARGE_KEYS):
        with pytest.raises(ValueError) as caught:
            payment_provider.create_payment_intent(amount=1000, currency="usd",
                                                   **{key: "acct_x"})
        assert key in str(caught.value)


def test_the_runtime_refusal_does_not_depend_on_stripe_being_configured():
    """The refusal is checked before `_stripe_ready`.

    With no key configured the function returns a soft "not configured" dict, so
    a check placed after that gate would never fire on a developer machine - the
    first time anyone saw it would be production. The control below proves the
    unconfigured path really does return rather than raise, so the raise above is
    caused by the forbidden key and not by the missing key.
    """
    from services import payment_provider

    assert payment_provider._stripe_ready() is False, (
        "this test is only meaningful with no STRIPE_SECRET_KEY set")

    clean = payment_provider.create_payment_intent(amount=1000, currency="usd")
    assert clean.get("ok") is not True

    with pytest.raises(ValueError):
        payment_provider.create_payment_intent(amount=1000, currency="usd",
                                               transfer_data={"destination": "acct_x"})


def test_the_runtime_and_static_key_sets_cannot_drift_apart():
    """Two lists of the same forbidden keys is two things to keep in step. If a
    fifth route to a destination charge is ever added to one, this fails until it
    is added to the other."""
    from services import payment_provider

    assert set(payment_provider.DESTINATION_CHARGE_KEYS) == FORBIDDEN_CHARGE_KEYS


def test_the_scanner_would_catch_a_write_into_the_splatted_dict():
    """Positive control for the indirect form, including single quotes - the
    exact shape the substring guard in test_marketplace_cart_lifecycle misses."""
    tree = ast.parse("payment_intent_data['transfer_data'] = {'destination': acct}\n")
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in INTENT_DICT_NAMES
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value in FORBIDDEN_CHARGE_KEYS):
                    found.append(target.slice.value)
    assert found == ["transfer_data"]
