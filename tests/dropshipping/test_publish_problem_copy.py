"""Every publish refusal reaches the merchant in words, and can be answered.

What this file is defending
---------------------------
The publish gate's problem codes exist twice: once in
``services/business_os/suppliers/drafts.py``, and once in
``mobile-native/src/api/dropshipping.ts`` as ``PUBLISH_PROBLEMS``. One is
Python, the other TypeScript, so no compiler spans them — and they drifted. The
backend grew ``VARIANT_PRICE_SPREAD``, ``PRICE_ABOVE_CHECKOUT_LIMIT`` and
``SUPPLIER_VARIANT_UNBOUND``; the mobile list did not.

``ReviewImportedProductScreen`` renders an unknown code verbatim, on purpose —
a blank line would be worse. So the drift did not crash, throw, or fail a test.
It put the string ``SUPPLIER_VARIANT_UNBOUND`` on the screen of every merchant
who imported a product with more than one in-stock variant, which is what the
supplier screen pre-selects. An enumeration cannot notice what was never on it.

Inside TypeScript the drift is now a build error: ``PROBLEM_COPY`` is a total
``Record<PublishProblem, …>``, so adding a code to the union without copy fails
the typecheck. That closes the half TypeScript can see. This file closes the
other half — a code added on the *Python* side, which the union has no way to
learn about.

How the backend's list is read
------------------------------
Not by grepping the source. ``_validate.__code__.co_names`` is the set of global
names that function actually reads, so the list measured here is the list the
evaluator can really emit — a code declared and never appended does not count,
and a code appended without being declared would be a ``NameError`` long before
this test. That is a measurement of the function, not of the text above it.

The TypeScript side *is* read as text, because there is no other way across the
boundary. What is read is a literal array and the keys of a literal object —
data, not logic. A test that read a TS *function* to decide what it does would be
satisfied by whatever that function said; a test that reads two lists to check
they name the same things is the only thing that can check exactly that.

Needs no database, but ``services.db`` binds ``DATABASE_URL`` at import, so this
sets one rather than inheriting whichever file ran first.
"""

import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="publish-copy-", suffix=".db")
os.close(_HANDLE)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH}")
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services.business_os.suppliers import drafts, importer  # noqa: E402

MOBILE_API = os.path.join(REPO, "mobile-native", "src", "api", "dropshipping.ts")
MOBILE_SCREEN = os.path.join(
    REPO, "mobile-native", "src", "screens", "dropshipping", "ReviewImportedProductScreen.tsx")
#: The copy table moved out of the screen when a second surface started rendering
#: publish problems: auto-publish reports them on an *import result*, in the
#: cart's result sheet, so the draft screen is no longer the only place a refusal
#: is read. Two screens with two tables is the drift this file exists to catch,
#: one level up, so there is one table and both import it.
MOBILE_COPY = os.path.join(
    REPO, "mobile-native", "src", "screens", "dropshipping", "publishProblems.ts")
IMPORT_CART_SCREEN = os.path.join(
    REPO, "mobile-native", "src", "screens", "dropshipping", "ImportCartScreen.tsx")


#: The functions that put a problem code in front of a merchant.
#:
#: ``_validate`` was the only one while publication was a button the merchant
#: pressed. Auto-publish added a second: ``verify_published`` is the §35 read-back,
#: and the importer reports *its* codes per item in exactly the same field, which
#: ``ReviewImportedProductScreen`` renders through exactly the same table. So a
#: code it can emit is a code that reaches a screen, and measuring only
#: ``_validate`` would leave the new ones outside the enumeration -- which is the
#: hole this whole file exists to close, reopened one function along.
EMITTERS = (drafts._validate, drafts.verify_published)


def backend_codes():
    """Every problem code the gate and its read-back can append.

    A global name one of :data:`EMITTERS` reads, which ``drafts`` declares as an
    upper-case string equal to its own name. ``MAX_CHECKOUT_PRICE_CENTS`` is read
    by the same function and is an int, so it falls out without being named here.
    """
    return {
        name
        for function in EMITTERS
        for name in function.__code__.co_names
        if name.isupper() and isinstance(getattr(drafts, name, None), str)
        and getattr(drafts, name) == name
    }


def _literal_array(source, name):
    block = re.search(
        r"export const " + re.escape(name) + r" = \[(.*?)\] as const;", source, re.S)
    assert block, f"{name} is no longer a literal array — this test can no longer read it"
    return re.findall(r'"([A-Z_]+)"', block.group(1))


def mobile_codes():
    return _literal_array(open(MOBILE_API).read(), "PUBLISH_PROBLEMS")


def mobile_copy_keys():
    source = open(MOBILE_COPY).read()
    block = re.search(
        r"export const PROBLEM_COPY: Record<PublishProblem, PublishProblemCopy> = \{(.*?)\n\};",
        source, re.S)
    assert block, ("PROBLEM_COPY is no longer a total Record over PublishProblem. "
                   "That annotation is what makes the TypeScript half self-checking; "
                   "widening it back to Record<string, …> is how the drift happened.")
    return set(re.findall(r"^  ([A-Z_]+):", block.group(1), re.M))


def test_both_surfaces_read_the_one_copy_table():
    """Neither screen may grow a second copy of it.

    Asserted because the extraction is only worth anything while it holds. A
    screen that re-declared ``PROBLEM_COPY`` locally would typecheck, render, and
    quietly fall behind — which is precisely the history of this table.
    """
    for path in (MOBILE_SCREEN, IMPORT_CART_SCREEN):
        source = open(path).read()
        assert "publishProblems" in source, f"{os.path.basename(path)} does not use the shared copy"
        assert "const PROBLEM_COPY" not in source, (
            f"{os.path.basename(path)} declares its own PROBLEM_COPY again")


def test_the_mobile_list_names_every_code_the_gate_can_emit():
    # The direction that matters. A code the backend emits and mobile has never
    # heard of is rendered as a raw identifier to a merchant who cannot act on it.
    missing = backend_codes() - set(mobile_codes())
    assert not missing, (
        "These publish problems have no entry in PUBLISH_PROBLEMS, so the draft "
        f"screen renders them as raw codes: {sorted(missing)}")


def test_the_mobile_list_invents_no_code_the_gate_cannot_emit():
    # The other direction is worth pinning too, but for a different reason: a
    # code only mobile knows about is copy for a state that cannot happen, and it
    # will be maintained forever by people who assume it can.
    invented = set(mobile_codes()) - backend_codes()
    assert not invented, (
        "PUBLISH_PROBLEMS names codes the publish gate cannot produce: "
        f"{sorted(invented)}")


def test_every_code_has_words_a_merchant_can_read():
    # PROBLEM_COPY being total over PublishProblem is a typecheck, and this is
    # the same claim measured from outside the typechecker — because the
    # annotation could be widened back to Record<string, …> in one edit and the
    # build would go green with the copy missing.
    missing = backend_codes() - mobile_copy_keys()
    assert not missing, f"No merchant-readable copy for: {sorted(missing)}"


def test_the_list_has_no_duplicates():
    codes = mobile_codes()
    assert len(codes) == len(set(codes)), "PUBLISH_PROBLEMS lists a code twice"


def test_the_unbound_variant_refusal_is_one_of_them():
    # Named explicitly rather than left to the set arithmetic above. This is the
    # code the drift hid, and the one whose absence was not merely cosmetic: it
    # was the only problem in the table with no remedy anywhere in the app, so a
    # merchant reading it had nothing to do next even if they decoded it.
    assert drafts.SUPPLIER_VARIANT_UNBOUND in backend_codes()
    assert drafts.SUPPLIER_VARIANT_UNBOUND in mobile_codes()
    assert drafts.SUPPLIER_VARIANT_UNBOUND in mobile_copy_keys()


# ---------------------------------------------------------------------------
# The second enumeration across the same boundary
#
# `importer.OUTCOMES` has exactly the problem `PUBLISH_PROBLEMS` had, for exactly
# the same reason: a Python tuple and a TypeScript array naming the same things,
# with no compiler between them. Auto-publish added `PUBLISHED` and
# `NEEDS_ATTENTION` to the Python side, and `ImportCartScreen`'s `OUTCOME_COPY`
# renders an unrecognised outcome as "This one couldn't be imported" -- so a
# merchant whose twenty products all went live would have read twenty failures.
#
# Checked here rather than in a new file because it is the same claim, measured
# the same way, over the same language boundary. A second file would duplicate
# `_literal_array` and the reasoning above it.
# ---------------------------------------------------------------------------

def mobile_outcomes():
    return _literal_array(open(MOBILE_API).read(), "IMPORT_OUTCOMES")


def test_the_mobile_list_names_every_outcome_an_import_can_return():
    missing = set(importer.OUTCOMES) - set(mobile_outcomes())
    assert not missing, (
        "These import outcomes have no entry in IMPORT_OUTCOMES, so the cart's "
        f"result sheet reports them as failures: {sorted(missing)}")


def test_the_mobile_list_invents_no_outcome_an_import_cannot_return():
    invented = set(mobile_outcomes()) - set(importer.OUTCOMES)
    assert not invented, (
        f"IMPORT_OUTCOMES names outcomes the importer cannot produce: {sorted(invented)}")


def test_every_outcome_has_words_a_merchant_can_read():
    """``OUTCOME_COPY`` must be total over ``ImportOutcome``, and cover every code.

    The annotation is checked as text for the same reason ``PROBLEM_COPY``'s is:
    ``Record<string, …>`` is one edit away and would let this table fall behind
    silently, which is how the copy for the two new outcomes would have gone
    missing on the day they were added.
    """
    source = open(IMPORT_CART_SCREEN).read()
    block = re.search(
        r"const OUTCOME_COPY: Record<ImportOutcome, \{[^}]*\}> = \{(.*?)\n\};", source, re.S)
    assert block, ("OUTCOME_COPY is no longer a total Record over ImportOutcome. "
                   "As Record<string, …> an unlisted outcome falls through to "
                   "\"couldn't be imported\", and PUBLISHED would read as a failure.")
    keys = set(re.findall(r"^  ([A-Z_]+):", block.group(1), re.M))
    missing = set(importer.OUTCOMES) - keys
    assert not missing, f"No merchant-readable copy for: {sorted(missing)}"


def test_the_two_auto_publish_outcomes_are_among_them():
    # Named explicitly, like SUPPLIER_VARIANT_UNBOUND above. These are the codes
    # the philosophy change introduced, and the ones whose absence would have
    # reported a successful one-tap import as a failed one.
    for outcome in (importer.PUBLISHED, importer.NEEDS_ATTENTION):
        assert outcome in importer.OUTCOMES
        assert outcome in mobile_outcomes()


def test_the_screen_can_actually_bind_a_variant():
    """The remedy exists and is reachable from the screen that reports the problem.

    ``bind-product`` sat on the server with no caller on any screen: a grep
    across ``mobile-native/src``, ``templates/`` and ``static/`` for it returned
    nothing. So ``SUPPLIER_VARIANT_UNBOUND`` was a guard that nothing reachable
    could satisfy, which is the same defect as not having the guard — the
    merchant is stopped either way, they just get a code instead of a broken
    order.

    Asserted here, in the file about that code, rather than left to the mobile
    suite: the claim is about the pair.
    """
    api = open(MOBILE_API).read()
    assert "bind-product" in api, "no mobile caller for the bind-product route"
    assert "export async function bindDraftVariant" in api
    screen = open(MOBILE_SCREEN).read()
    assert "bindDraftVariant" in screen, (
        "the screen that reports SUPPLIER_VARIANT_UNBOUND does not offer the fix")
