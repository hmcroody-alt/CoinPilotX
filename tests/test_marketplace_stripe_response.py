from services.marketplace_payment_errors import stripe_response_value


class StripeResource:
    def __init__(self):
        self.id = "pi_live_safe_id"
        self.client_secret = "pi_live_safe_id_secret_redacted"

    def __getattr__(self, name):
        # Mirrors generated Stripe resources: asking for the nonexistent
        # ``get`` method raises an attribute error whose text is just "get".
        raise AttributeError(name)


def test_stripe_response_value_supports_generated_resource_objects():
    intent = StripeResource()

    assert stripe_response_value(intent, "id") == "pi_live_safe_id"
    assert stripe_response_value(intent, "client_secret") == "pi_live_safe_id_secret_redacted"
    assert stripe_response_value(intent, "missing") == ""


def test_stripe_response_value_keeps_mapping_test_doubles_supported():
    intent = {"id": "pi_test_safe_id", "client_secret": "pi_test_safe_id_secret_redacted"}

    assert stripe_response_value(intent, "id") == "pi_test_safe_id"
    assert stripe_response_value(intent, "client_secret") == "pi_test_safe_id_secret_redacted"


# ---------------------------------------------------------------------------
# The call sites, not just the helper
# ---------------------------------------------------------------------------
#
# The two tests above prove the helper absorbs a generated resource. They passed
# for the whole time that `services/marketplace_cart_routes.py` was reading its
# Checkout Session with `session_obj.get("id")` -- because a helper nobody calls
# is a helper that is correct and useless.
#
# That is not hypothetical. The helper's own docstring says it exists "so Buy
# Now, cart, and accepted-offer checkout cannot drift again", and the drift
# happened anyway: the PaymentIntent branches were converted and the Checkout
# Session branches beside them were not. On stripe 15 a `checkout.Session` is a
# generated resource, `hasattr(Session, "get")` is False, and
# `session_obj.get("id")` raises `AttributeError: get` *after* `Session.create`
# has already succeeded -- so a real, payable Stripe page exists that nothing on
# this side recorded, the request 500s, and the buyer reads a generic "payment
# unavailable" because `classify_provider_exception` maps a non-Stripe exception
# to PAYMENT_UNAVAILABLE. Production carried 39 `seller_transactions`, 14 with a
# PaymentIntent id and *zero* with a Checkout Session id.
#
# So this test does not name the call sites. It discovers them: anything
# assigned from a Stripe provider call, in any of the three money lanes, read
# with `.get(...)`. A fourth lane added tomorrow is covered the day it is
# written, which is the only way this stops being a bug that comes back.

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]

#: The three lanes that create Stripe objects for a Marketplace purchase. Named
#: as files because that is the unit a `.get` can hide in; the subjects *within*
#: them are discovered.
_MONEY_LANES = (
    "bot.py",
    "services/marketplace_cart_routes.py",
    "services/marketplace_offers_routes.py",
)

#: Methods that return a Stripe object rather than a dict. `list` is excluded:
#: it returns a `ListObject`, which really is iterable and mapping-ish, and
#: including it would flag reads that are correct.
_PROVIDER_METHODS = frozenset({"create", "retrieve", "modify", "confirm",
                               "cancel", "capture"})


def _dotted(node):
    """`bot.stripe.checkout.Session.create` from the attribute chain, best effort.

    Spelled out rather than matched against a literal because these call sites
    reach Stripe through `stripe`, through `bot.stripe`, and through a module
    alias; matching on the receiver's name would miss two of the three.
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class _Scan(ast.NodeVisitor):
    """Names bound to a Stripe provider call, and `.get()` reads of them."""

    def __init__(self):
        self.produced = {}   # local name -> the dotted call that produced it
        self.unsafe = []     # (name, producer, field, lineno)
        self.safe = 0        # reads routed through the helper

    def visit_Assign(self, node):
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
            name = _dotted(call.func)
            if call.func.attr in _PROVIDER_METHODS and "stripe" in name.lower():
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.produced[target.id] = name
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id in self.produced):
            field = ""
            if node.args and isinstance(node.args[0], ast.Constant):
                field = str(node.args[0].value)
            self.unsafe.append((func.value.id, self.produced[func.value.id],
                                field, node.lineno))
        if (isinstance(func, ast.Attribute) and func.attr == "stripe_response_value") or (
                isinstance(func, ast.Name) and func.id == "stripe_response_value"):
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in self.produced:
                self.safe += 1
        self.generic_visit(node)




#: A function in `bot.py` is part of the Marketplace money path if it mentions
#: one of these. `bot.py` is a 111k-line monolith whose Premium, billing-portal
#: and course lanes read Stripe objects the same unsafe way; those are real and
#: are reported separately, but widening a Marketplace fix across four other
#: products is not a change this test should force. The membership test is a
#: property of the function rather than a list of function names, so a fifth
#: Marketplace lane added to `bot.py` is covered the day it is written.
_MARKETPLACE_MARKERS = ("stripe_checkout_session_id", "seller_transactions",
                        "marketplace_product")


def _is_money_path(func, relpath):
    """Only `bot.py` is filtered; the two services files *are* the lane."""
    if relpath != "bot.py":
        return True
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in _MARKETPLACE_MARKERS):
                return True
    return False


def _scan(relpath):
    """Parse, never import, and scan one function at a time.

    Parse because `import bot` runs `init_db()` at module scope and costs
    minutes cold, while this question is answerable from the syntax tree.

    Per-function because names collide: a module-wide pass saw `checkout` bound
    by one route's `stripe.checkout.Session.create` and then flagged a different
    route's `checkout.get("ok")`, where `checkout` is the plain dict that
    `payment_provider.create_checkout_session` returns. A scope that spans two
    functions is not a scope.

    Source text is never re-derived from `str.splitlines()` either:
    `splitlines` breaks on U+2028 and U+2029 and the tokenizer does not, and
    `bot.py` embeds JavaScript containing both, so any index built that way is
    shifted by the time it matters. Line numbers come from the nodes.
    """
    tree = ast.parse((_ROOT / relpath).read_text(encoding="utf-8"))
    unsafe, produced, safe = [], 0, 0
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_money_path(func, relpath):
            continue
        scan = _Scan()
        for node in func.body:
            scan.visit(node)
        unsafe.extend(scan.unsafe)
        produced += len(scan.produced)
        safe += scan.safe
    return unsafe, produced, safe


@pytest.mark.parametrize("relpath", _MONEY_LANES)
def test_no_stripe_object_in_a_money_lane_is_read_as_a_mapping(relpath):
    """Every Stripe object these lanes create is read through the helper.

    The failure this prevents is not a crash before the charge. It is a crash
    *after* the provider call succeeded, which is the one shape that leaves a
    payable object at Stripe with no local record of it.
    """
    unsafe, _produced, _safe = _scan(relpath)
    assert not unsafe, "\n".join(
        [f"{relpath} reads a Stripe object as a mapping:"] +
        [f"  line {lineno}: {name}.get({field!r})  <- {producer}(...)"
         for name, producer, field, lineno in unsafe] +
        ["Use services.marketplace_payment_errors.stripe_response_value: on "
         "stripe 15 these are generated resources with no `.get`, and the "
         "AttributeError lands after the object is already created and payable."])


def test_the_scan_finds_the_call_sites_it_is_guarding():
    """Anti-vacuity, and it has teeth: the absence above passed for years.

    An AST matcher that silently stops matching -- a renamed receiver, a walrus,
    a tuple assignment, a function-level filter that excludes everything -- turns
    the test above into a test that reads three files and asserts nothing. So the
    discovery itself is asserted: the lanes really do create Stripe objects, and
    those objects really are being read through the helper.
    """
    produced, safe = {}, 0
    for relpath in _MONEY_LANES:
        _unsafe, count, routed = _scan(relpath)
        produced[relpath] = count
        safe += routed

    empty = sorted(path for path, count in produced.items() if not count)
    assert not empty, (
        f"the scan found no Stripe provider call in {empty}. Either the lane "
        "moved or the matcher stopped matching; in both cases the test above is "
        "now vacuous")
    assert safe >= 3, (
        f"only {safe} Stripe object read(s) go through stripe_response_value "
        "across the three money lanes. The helper is how the absence above is "
        "achieved, so if nothing calls it the absence means nothing")


# --- section 2: the helper's NAME has to be bound wherever it is read --------
#
# Routing a read through `stripe_response_value` only helps if the name
# resolves. The first version of the bot.py Buy Now fix reached the helper as
# `marketplace_cart_service.stripe_response_value`, and that alias is bound
# inside `if item_type == "marketplace_product"` near the top of
# `api_pulse_payments_checkout` -- while the Checkout Session it reads is
# created unconditionally, for every item_type the route accepts. So three of
# the four lanes ("course", "lesson", "live_class", plus the empty default)
# raised `NameError` instead of `AttributeError`: the identical
# 500-after-a-payable-Session-exists outage, wearing a different exception name,
# and completely invisible to the scan in section 1, which only asks whether a
# `.get()` is present.


def _guard_chain(func, line):
    """Conditions that must hold for `line` to execute, innermost last.

    Each entry is (id(If), "body"|"orelse"). Loops and `with` are not
    conditions and are skipped; a `try` is not a condition either.
    """
    chain = []

    def rec(node, acc):
        for _field, value in ast.iter_fields(node):
            for item in (value if isinstance(value, list) else [value]):
                if not isinstance(item, ast.AST):
                    continue
                lo = getattr(item, "lineno", None)
                hi = getattr(item, "end_lineno", None)
                if lo is None or hi is None:
                    rec(item, acc)
                    continue
                if not (lo <= line <= hi):
                    continue
                if isinstance(item, ast.If):
                    in_body = any(
                        s.lineno <= line <= getattr(s, "end_lineno", s.lineno)
                        for s in item.body)
                    nxt = acc + [(id(item), "body" if in_body else "orelse")]
                else:
                    nxt = acc
                if len(nxt) > len(chain):
                    chain[:] = nxt
                rec(item, nxt)

    rec(func, [])
    return chain


def _bindings_of(func, root):
    """Lines in `func` that bind the bare name `root`."""
    out = []
    for node in ast.walk(func):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == root:
                    out.append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == root:
                    out.append(node.lineno)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            target = getattr(node, "target", None)
            if isinstance(target, ast.Name) and target.id == root:
                out.append(node.lineno)
    # a parameter is bound on every path
    args = func.args
    for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
              + [args.vararg, args.kwarg]):
        if a is not None and a.arg == root:
            out.append(func.lineno)
    return sorted(out)


def _helper_reads(func):
    """(root_name, lineno) for every read of stripe_response_value in `func`."""
    found = []
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr == "stripe_response_value":
            cur = node.value
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name):
                found.append((cur.id, node.lineno))
        elif (isinstance(node, ast.Name)
              and node.id == "stripe_response_value"
              and isinstance(node.ctx, ast.Load)):
            found.append(("stripe_response_value", node.lineno))
    return found


@pytest.mark.parametrize("relpath", _MONEY_LANES)
def test_every_helper_read_has_its_name_bound_on_every_path(relpath):
    """A binding nested under a condition the read is not nested under is a NameError."""
    path = _ROOT / relpath
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)

    unbound = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads = _helper_reads(func)
        if not reads:
            continue
        for root, line in reads:
            bindings = _bindings_of(func, root)
            if not bindings:
                continue  # module-level or imported at top: section 1's business
            read_chain = _guard_chain(func, line)
            # a binding dominates the read when every condition guarding the
            # binding also guards the read, and it runs first
            if not any(bind < line
                       and _guard_chain(func, bind) == read_chain[:len(_guard_chain(func, bind))]
                       for bind in bindings):
                unbound.append((func.name, root, line, bindings))

    assert not unbound, "\n".join(
        [f"{relpath}: a Stripe response is read through a conditionally-bound name"]
        + [f"  {fname}: `{root}` read at line {line}, but every binding "
           f"({binds}) is nested under a condition the read is not"
           for fname, root, line, binds in unbound]
        + ["This raises NameError after the provider call already succeeded -- "
           "the same orphaned-payable-object outage the helper exists to prevent. "
           "Import the helper unconditionally at the read site."])
