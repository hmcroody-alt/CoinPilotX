#!/usr/bin/env python3
"""Mutation battery for the dropship variant binding (gap 13).

What this is aimed at
---------------------
`SupplierProductScreen.defaultSelection` pre-selects every in-stock variant, so
the ordinary import of any multi-variant CJ product sends more than one. The
importer then writes `provider_variant_id = NULL` on the source row -- correctly,
it refuses to guess which variant a buyer would receive -- and `_validate`
refuses publication with `SUPPLIER_VARIANT_UNBOUND`, also correctly, because the
buyer's checkout has no variant picker and charges one listing price.

Measured, not inferred: `scripts/probe_dropship_multivariant_publish.py` imports
one, two and four variants through the real importer and the real evaluator. The
one-variant case publishes; the two- and four-variant cases come back
`publishable: False, problems: ['SUPPLIER_VARIANT_UNBOUND']`.

Three root causes, and the mutations below are aimed at all three:

*   A SECOND COPY OF AN ENUMERATION WITH NO COMPILER SPANNING IT.
    `PUBLISH_PROBLEMS` in `dropshipping.ts` is the mobile copy of the codes
    declared in `drafts.py`. The backend grew three; the mobile list grew none;
    and the screen renders an unknown code verbatim on purpose, so nothing
    crashed and nothing failed. The merchant read the string
    "SUPPLIER_VARIANT_UNBOUND". Mutations 1-3 and 14-15 attack the two
    mechanisms that now close this: `PROBLEM_COPY` being total over the union
    (which the compiler can walk) and `test_publish_problem_copy.py` (which
    crosses the language boundary the compiler cannot).
*   A GUARD WHOSE ONLY REMEDY HAD NO CALLER. `bind-product` existed on the
    server and was called from nothing in `mobile-native/src`, `templates/` or
    `static/`. A guard nothing reachable can satisfy stops the merchant exactly
    as hard as no guard; they just get a code instead of a broken order.
    Mutations 4-13 attack the remedy: the two ids the draft now serves, the
    chooser that offers the choice, and the bind call itself.
*   A FIXTURE DESCRIBING A ROW THE DATABASE CANNOT PRODUCE. The mobile draft
    fixture said `fulfillmentMode: "SANDBOX"` -- an environment mode in a
    fulfilment-mode field, a value `marketplace_product_sources` cannot hold.
    Every test built on it was exercising a listing that is neither dropshipped
    nor stocked, which is why none of them noticed the binding. Mutation 16
    restores it.

Four checks, because the defences are in four places
----------------------------------------------------
Two of the new defences are typecheck-only and two are cross-language, so a
battery that ran one suite could not tell them apart:

*   `tests/dropshipping/test_publish_problem_copy.py` -- the two lists name the
    same codes, and every code has words. Reads the TS side as *data*.
*   `tests/dropshipping/test_dropship_draft_publish.py` -- the draft serves the
    binding, and binding through the route clears the refusal.
*   `DropshippingScreens.test.tsx` -- the merchant can actually see and answer
    the refusal. A field the server serves and a backend test pins is not
    delivered until something renders it.
*   `npx tsc --noEmit` -- `PROBLEM_COPY` is total over `PublishProblem`, so the
    next code added to the union fails the build. Proved by experiment when the
    fix landed: injecting a code produced TS2741.

The two Python files run in separate processes. They each bind `DATABASE_URL` at
import against their own temp database, and the `tests/dropshipping/` directory
cannot share a pytest process at all.

The checks run in order and stop at the first one that fails, so the report says
*which* defence caught each mutation -- useful, because several of these are
caught by only one of the four.

One mutation is inverted: a pure rename changes the source text and nothing else,
and nothing should notice. One is a no-op control.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

DRAFTS = ROOT / "services/business_os/suppliers/drafts.py"
IMPORTER = ROOT / "services/business_os/suppliers/importer.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/ReviewImportedProductScreen.tsx"
SPEC = NATIVE / "src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _copy_pin() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_publish_problem_copy.py")


def _publish_suite() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_dropship_draft_publish.py")


def _jest() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "jest", "--runTestsByPath",
         "src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx", "--silent"],
        cwd=NATIVE, capture_output=True, text=True,
    )


def _typecheck() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"],
        cwd=NATIVE, capture_output=True, text=True,
    )


# Cheapest and most specific first; the report names whichever one fires.
CHECKS = [
    ("copy-pin", _copy_pin),
    ("publish", _publish_suite),
    ("jest", _jest),
    ("tsc", _typecheck),
]


def first_failing() -> str | None:
    for label, run in CHECKS:
        if run().returncode != 0:
            return label
    return None


# (name, file, old, new, what a surviving mutant would mean)
MUTATIONS = [
    # ------------------------- the enumeration with no compiler spanning it
    (
        "PROBLEM_COPY is widened back to Record<string, …>",
        SCREEN,
        "const PROBLEM_COPY: Record<PublishProblem, { text: string; fixable: boolean }> = {",
        "const PROBLEM_COPY: Record<string, { text: string; fixable: boolean }> = {",
        "the drift is re-opened in one edit: the table can fall behind "
        "PUBLISH_PROBLEMS again with a green build, which is exactly how three "
        "codes came to have no copy",
    ),
    (
        "the unbound code is dropped from the mobile list",
        API,
        '  "PRICE_ABOVE_CHECKOUT_LIMIT",\n  "SUPPLIER_VARIANT_UNBOUND"\n] as const;',
        '  "PRICE_ABOVE_CHECKOUT_LIMIT"\n] as const;',
        "the defect verbatim: the backend's most common refusal is not in the "
        "mobile union, so the screen prints the identifier "
        "SUPPLIER_VARIANT_UNBOUND to a merchant who cannot act on it",
    ),
    (
        "the unbound code has copy again but no words",
        SCREEN,
        "  SUPPLIER_VARIANT_UNBOUND: {\n"
        "    text: \"Choose which variant you're selling, below. A product sells one variant, and orders go to your supplier for that one.\",\n"
        "    fixable: true\n"
        "  }\n};",
        "  RESTRICTED_PRODUCT_DUPLICATE_PLACEHOLDER: { text: \"\", fixable: true }\n};",
        "the table is total over a union it no longer covers correctly -- the "
        "typecheck is the only thing standing between this and a blank line on "
        "the merchant's screen",
    ),
    (
        "the price-spread refusal loses its words",
        SCREEN,
        "  VARIANT_PRICE_SPREAD: {\n"
        '    text: "Your variants have different prices. Checkout charges one price per product, so set them all to the same amount.",\n'
        "    fixable: true\n"
        "  },\n",
        "",
        "one of the other two codes that drifted goes back to being rendered as "
        "an identifier, so the fix covered only the code that was noticed",
    ),
    # ------------------------------------- the ids the remedy is built from
    (
        "the draft stops saying which variant it sells",
        DRAFTS,
        '            "provider_variant_id": source.get("provider_variant_id"),\n'
        '            "merchant_owned_fields"',
        '            "merchant_owned_fields"',
        "the screen cannot tell a bound listing from an unbound one, so it "
        "offers the chooser forever and the server answers binding_conflict to "
        "the second choice",
    ),
    (
        "the draft stops saying what to bind against",
        DRAFTS,
        '            "provider_product_id": source.get("provider_product_id"),\n',
        "",
        "`bind-product` takes (canonical_product_id, pid, vid) and the screen "
        "has no pid, so `confirmVariant` returns early on every press -- a "
        "button that does nothing, which is the guard-with-no-remedy defect "
        "moved one layer out",
    ),
    (
        "the source row's binding is served from the per-variant column",
        DRAFTS,
        '            "provider_variant_id": source.get("provider_variant_id"),\n'
        '            "merchant_owned_fields": source.get("overridden_fields") or [],',
        '            "provider_variant_id": (priced[0].get("provider_variant_id")\n'
        "                                    if priced else None),\n"
        '            "merchant_owned_fields": source.get("overridden_fields") or [],',
        "the two `provider_variant_id` columns are confused: "
        "`marketplace_listing_variants` has one per chosen variant and is "
        "always populated, so every listing looks bound and the chooser never "
        "appears -- while `create_intent` still refuses on the NULL in "
        "`marketplace_product_sources`",
    ),
    # --------------------------------------------- the chooser on the screen
    (
        "the chooser is offered for an already-bound listing",
        SCREEN,
        "  const showVariantChooser = isDropship && !boundVariantId && bindableVariants.length > 0;",
        "  const showVariantChooser = isDropship && bindableVariants.length > 0;",
        "a control that cannot change anything is presented as one: the server "
        "refuses variant A -> variant B with binding_conflict, so every press "
        "is an error the merchant caused by believing the screen",
    ),
    (
        "the chooser is offered to a stocked listing",
        SCREEN,
        "  const showVariantChooser = isDropship && !boundVariantId && bindableVariants.length > 0;",
        "  const showVariantChooser = !boundVariantId && bindableVariants.length > 0;",
        "a merchant selling off their own shelves is asked to name a supplier "
        "variant for an order that will never be placed with a supplier -- a "
        "decision invented by the screen",
    ),
    (
        "the bind names the first variant instead of the chosen one",
        SCREEN,
        "        providerVariantId: pendingVariantId\n",
        "        providerVariantId: draft.variants[0].providerVariantId as string\n",
        "the merchant picks Black and the listing is bound to White, "
        "permanently -- `link_source` will not re-point it -- so every order "
        "ships the wrong variant and the screen agrees with itself about it",
    ),
    (
        "choosing a variant resets the merchant's unsaved typing",
        SCREEN,
        "      const fresh = await getImportedProduct(scope, connectionId, listingId);\n"
        "      setDraft((current) =>\n"
        "        current\n"
        "          ? { ...current, supplier: fresh.supplier, variants: fresh.variants, validation: fresh.validation }\n"
        "          : fresh\n"
        "      );",
        "      adopt(await getImportedProduct(scope, connectionId, listingId));",
        "`adopt` resets `form`, `prices` and `dirty` from the response, so a "
        "merchant who retitled the product and then answered the refusal "
        "watches their words vanish with no error and nothing to undo",
    ),
    (
        "the cleared verdict is assumed rather than re-read",
        SCREEN,
        "      const fresh = await getImportedProduct(scope, connectionId, listingId);\n"
        "      setDraft((current) =>\n"
        "        current\n"
        "          ? { ...current, supplier: fresh.supplier, variants: fresh.variants, validation: fresh.validation }\n"
        "          : fresh\n"
        "      );",
        "      setDraft((current) =>\n"
        "        current\n"
        "          ? {\n"
        "              ...current,\n"
        "              supplier: { ...current.supplier, providerVariantId: pendingVariantId },\n"
        "              validation: { publishable: true, problems: [] }\n"
        "            }\n"
        "          : current\n"
        "      );",
        "the screen writes its own verdict: binding succeeding is not the same "
        "claim as the draft being publishable, and every other refusal -- an "
        "unpriced variant, a delisted product -- disappears from the screen "
        "while publish still fails",
    ),
    (
        "the confirm button reports itself ready before a variant is picked",
        SCREEN,
        "                    onPress={() => confirmVariant().catch(() => undefined)}\n"
        "                    disabled={binding || !pendingVariantId}",
        "                    onPress={() => confirmVariant().catch(() => undefined)}\n"
        "                    disabled={binding}",
        "the button announces itself enabled and does nothing when pressed, "
        "which reads as a broken screen rather than as a step not yet taken -- "
        "and to a screen reader `accessibilityState.disabled` is the only signal "
        "that there was a step. Aimed at `disabled` and not at an explicit "
        "`accessibilityState`, because `Pressable` derives the latter from the "
        "former and overrides it: the first version of this mutation edited the "
        "copy that loses, survived, and was right to",
    ),
    (
        "a refused bind hides the control that would retry it",
        SCREEN,
        "          : \"That variant couldn't be set. Nothing was changed — try again, or pick a different one.\"\n"
        "      );",
        "          : \"That variant couldn't be set. Nothing was changed — try again, or pick a different one.\"\n"
        "      );\n"
        "      setPendingVariantId(null);",
        "a failed write clears the merchant's pick, so the error says 'try "
        "again' next to a button that is disabled again -- error and no way "
        "back to the action, which is the same shape as error-and-empty",
    ),
    # --------------------------------- the guard and the import that feeds it
    (
        "the publish gate stops refusing an unbound dropship listing",
        DRAFTS,
        "            and priced and sold is None:\n"
        "        problems.append(SUPPLIER_VARIANT_UNBOUND)",
        "            and priced and sold is None:\n"
        "        pass",
        "a listing a buyer can pay for and nobody can ship goes live: "
        "`create_intent` refuses on the NULL binding at order time, so the "
        "failure moves from a refusal the merchant can answer to a paid order "
        "that cannot be fulfilled",
    ),
    (
        "the guard is aimed at stocked sources instead",
        DRAFTS,
        'if str(source.get("fulfillment_mode") or "").upper() == supplier_schema.MODE_DROPSHIP \\',
        'if str(source.get("fulfillment_mode") or "").upper() == supplier_schema.MODE_STOCKED \\',
        "exactly inverted: the listings that place supplier orders publish "
        "unbound, and the ones that place none are blocked on a binding they "
        "have no use for",
    ),
    (
        "the importer guesses a binding from a multi-variant selection",
        IMPORTER,
        '        provider_variant_id=(chosen[0].get("external_variant_id")\n'
        "                             if len(chosen) == 1 else None),",
        '        provider_variant_id=chosen[0].get("external_variant_id"),',
        "the import answers a question the merchant was never asked: a t-shirt "
        "imported in four sizes is silently bound to whichever the provider "
        "listed first, and `link_source` will not let anyone change it",
    ),
    (
        "the fixture describes a row the database cannot hold, again",
        SPEC,
        '      fulfillmentMode: "DROPSHIP",',
        '      fulfillmentMode: "SANDBOX",',
        "an environment mode in a fulfilment-mode field: every test built on "
        "the fixture exercises a listing that is neither dropshipped nor "
        "stocked, which is precisely why eighty-five of them never noticed the "
        "binding",
    ),
    # ------------------------------------------------------------- inverted
    (
        "the product id local is renamed",
        SCREEN,
        "    const pid = draft?.supplier.providerProductId;\n"
        "    if (!scope || !draft || !pendingVariantId || !pid) return;",
        "    const productId = draft?.supplier.providerProductId;\n"
        "    if (!scope || !draft || !pendingVariantId || !productId) return;",
        "a pure rename was reported as a behaviour change, which means some "
        "assertion is reading the source text rather than the behaviour",
    ),
    # -------------------------------------------------------------- control
    (
        "a comment is reworded",
        SCREEN,
        "/** Merchant-editable keys, mirroring `DraftEdits`. Nothing else is a field. */",
        "/** Merchant-editable keys, mirroring `DraftEdits`. Nothing else is one. */",
        "",
    ),
]

# Indices (1-based) that must NOT be caught.
INVERTED = {len(MUTATIONS) - 1}
NO_OP_INDEX = len(MUTATIONS)


def main(argv: list[str]) -> int:
    # `--only 13,14` re-runs named mutations after a fix, without paying for the
    # whole battery. The summary line still counts the whole list, so a filtered
    # run cannot be mistaken for a clean full one.
    only: set[int] = set()
    if "--only" in argv:
        only = {int(n) for n in argv[argv.index("--only") + 1].split(",")}

    survivors = []
    control_failed = False

    # The rename is inverted only if it is *complete*; a half-done rename would
    # not compile and would be "caught" for the wrong reason. So the third use
    # is rewritten alongside it.
    rename_companion = ("        providerProductId: pid,", "        providerProductId: productId,")

    for index, (name, path, old, new, meaning) in enumerate(MUTATIONS, 1):
        if only and index not in only:
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR    {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        mutated = original.replace(old, new, 1)
        if index in INVERTED:
            before, after = rename_companion
            if mutated.count(before) != 1:
                print(f"{index:2}. ERROR    {name}\n        rename companion anchor missing")
                survivors.append((name, "rename companion anchor missing"))
                continue
            mutated = mutated.replace(before, after, 1)
        path.write_text(mutated)
        try:
            caught_by = first_failing()
        finally:
            path.write_text(original)

        caught = caught_by is not None

        if index == NO_OP_INDEX:
            if caught:
                print(f"{index:2}. CONTROL FAILED {name} (reported by {caught_by})")
                control_failed = True
            else:
                print(f"{index:2}. control  {name} (no-op, correctly survived)")
            continue

        if index in INVERTED:
            if caught:
                print(f"{index:2}. PINNED   {name} (by {caught_by})")
                survivors.append((name, meaning))
            else:
                print(f"{index:2}. survived {name} (inverted, correctly ignored)")
            continue

        if caught:
            print(f"{index:2}. caught   {name}  [{caught_by}]")
        else:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))

    ran = only or set(range(1, len(MUTATIONS) + 1))
    real = len(ran - {NO_OP_INDEX})
    print()
    if only:
        print(f"Filtered run: {sorted(only)} of {len(MUTATIONS)}. "
              "Not a full battery.")
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing below can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {real} mutations gave the wrong answer:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    inverted_ran = len(ran & INVERTED)
    print(f"All {real - inverted_ran} real mutations caught, "
          f"{inverted_ran} inverted mutation correctly ignored, "
          f"{'no-op control survived' if NO_OP_INDEX in ran else 'control not run'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
