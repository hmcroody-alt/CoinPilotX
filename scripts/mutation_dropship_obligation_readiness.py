#!/usr/bin/env python3
"""Mutation battery for obligation readiness and the order a merchant can place (gap 15).

What this is aimed at
---------------------
Gap 14 made the obligation visible. Measuring what a merchant could then *do*
with it -- `scripts/probe_dropship_obligation_to_order.py`, which publishes a
dropship listing through the real importer, drives a real checkout, and then
tries to discharge the obligation -- found four blockers, none of them a
symptom of the other three:

*   THE OBLIGATION CARRIED NO DESTINATION. The buyer's frozen address was one
    `json.loads` away, on the `seller_transactions` row the query's own join
    already reached, and nothing read it.
*   THE OBLIGATION REPORTED THE WRONG SKU. `marketplace_product_sources.external_sku`
    is the *product's*; `create_intent` matches the bound variant's
    `marketplace_listing_variants.sku`. Two adjacent columns, two levels of one
    hierarchy. NULL is the normal case for the product one, so the honest
    reading was `invalid_sku` and the populated reading was
    `product_binding_mismatch` -- a refusal that reads like a broken binding.
*   `gateway.read("shipping", ...)` HAD ZERO CALLERS. Not in `mobile-native/src`,
    not in `templates/`, not in `static/` -- measured. `create_intent` demands
    one of its snapshots, so the one action that discharges an obligation was
    unreachable. The reason is in the request: origin from the inventory,
    properties from the product, weight and SKU from the bound variant, every
    address field from a record no client has ever seen. No screen could build
    it. `quote_for_order` is the server-side assembler that was missing.
*   `create_intent` TOOK ITS DESTINATION FROM THE REQUEST BODY. A
    merchant-authenticated call could name any address at all, and nothing
    compared it to the one the buyer paid for. The parameter is gone.

And a fifth defect shape, which is why the country table is attacked here too:
the ISO-code-to-name map existed only in TypeScript, under a comment asserting
"The server never sees them; it sees the ISO-3166-1 alpha-2 code, which is the
contract." True of the buyer's half of the wire, false of the supplier's --
CJ's create-order needs `shippingCountry` as a name.

Five checks, because the defences are in five places
----------------------------------------------------
*   `tests/dropshipping/test_supplier_obligations.py` -- the blockers, the
    readiness verdict, the variant SKU, `order_destination`'s refusals, and
    that the buyer's address does not travel.
*   `tests/business_os/test_cj_fulfillment.py` -- `quote_for_order` end to end,
    its five refusals, and that its `expected_supplier_cost_cents` is accepted
    by `create_intent` unchanged.
*   `tests/dropshipping/test_supplier_obligation_copy.py` -- the blocker
    vocabulary and the country table, pinned across the language boundary no
    compiler spans.
*   `dropshipping.test.ts` + `DropshippingScreens.test.tsx` -- the merchant
    reads the reasons, and none of them asks them for the buyer's address.
*   `npx tsc --noEmit` -- `SUPPLIER_OBLIGATION_BLOCKER_COPY` is total over
    `SupplierObligationBlocker`.

The two `tests/dropshipping/` files run in separate processes: each binds
`DATABASE_URL` at import against its own temp database, and that directory
cannot share a pytest process at all.

Checks run cheapest-first and stop at the first failure, so the report says
*which* defence caught each mutation.

Two mutations are inverted, and a battery is only honest if it says why:

*   a pure rename of a local in `supplier_destination`;
*   removing the `_text` pass in `order_destination`. `validate_details` has
    already cleaned, length-capped and tag-stripped every value in the frozen
    record, so for any address checkout could have written the two are
    identical. It stays because a frozen record written by an older version of
    the checkout is not something to trust on the strength of who wrote it --
    an argument about the past and the future, not a claim any test can make
    about today.

One mutation is a no-op control.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.

    .venv/bin/python3 scripts/mutation_dropship_obligation_readiness.py
    .venv/bin/python3 scripts/mutation_dropship_obligation_readiness.py --only 1,7
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

FULFILLMENT = ROOT / "services/business_os/suppliers/fulfillment.py"
MARKETPLACE = ROOT / "services/marketplace_fulfillment.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/DropshippingOrdersScreen.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _obligations() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligations.py")


def _copy_pin() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligation_copy.py")


def _quote() -> subprocess.CompletedProcess:
    return _pytest("tests/business_os/test_cj_fulfillment.py")


def _jest() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "jest", "--runTestsByPath",
         "src/api/__tests__/dropshipping.test.ts",
         "src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx",
         "--silent"],
        cwd=NATIVE, capture_output=True, text=True,
    )


def _typecheck() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"],
        cwd=NATIVE, capture_output=True, text=True,
    )


CHECKS = [
    ("copy-pin", _copy_pin),
    ("obligations", _obligations),
    ("quote", _quote),
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
    # ------------------------------------------- the SKU from the wrong level
    (
        "the obligation reports the product's SKU instead of the variant's",
        FULFILLMENT,
        '            "v.sku AS supplier_sku, "',
        '            "s.external_sku AS supplier_sku, "',
        "gap 15's measured second blocker, restored. The product-level column is "
        "NULL for every single-variant import, so the obligation reports no SKU "
        "and `create_intent` answers `invalid_sku`; when it is populated the "
        "refusal becomes `product_binding_mismatch`, which reads like a broken "
        "binding rather than a wrong field",
    ),
    (
        "a variant with no supplier code is not reported as blocked",
        FULFILLMENT,
        "        if not item.get(\"supplier_sku\"):\n            blockers.append(SUPPLIER_SKU_MISSING)\n",
        "",
        "a multi-variant import binds no `provider_variant_id`, so the SKU join "
        "matches nothing -- and the row then claims it can be ordered. The "
        "merchant presses the action and gets a supplier refusal instead of a "
        "sentence telling them the listing is not linked to a product code",
    ),
    # ------------------------------------- the destination that was not carried
    (
        "readiness is asserted rather than derived from the blockers",
        FULFILLMENT,
        '            "can_place_supplier_order": not blockers,',
        '            "can_place_supplier_order": True,',
        "every obligation claims it can be ordered, including the ones whose "
        "reasons are listed directly underneath. The screen counts unorderable "
        "sales off this field, so the number and the list contradict each other",
    ),
    (
        "an undecided delivery lane is treated as shipping",
        FULFILLMENT,
        '    if mf.order_kind(metadata) != "shipping":',
        '    if (frozen or {}).get("kind") not in ("shipping", "shipping_or_pickup"):',
        "a listing offering both lanes freezes `shipping_or_pickup` until the "
        "buyer narrows it. Reading that as shipping buys goods from a supplier "
        "against a lane the buyer never chose -- and re-deriving the lane "
        "recovers the ambiguity, not the choice",
    ),
    (
        "the country code is sent to the supplier as the country name",
        FULFILLMENT,
        '        value = (mf.country_name(details.get("address_country"))\n'
        '                 if frozen_key is None else details.get(frozen_key))',
        '        value = (details.get("address_country")\n'
        '                 if frozen_key is None else details.get(frozen_key))',
        "CJ's create-order takes `shippingCountryCode` *and* `shippingCountry`, "
        "and the second is a name. Sending `US` as the name of a country is this "
        "repo's recurring defect in one line: asserting a fact rather than "
        "admitting it is unknown",
    ),
    (
        "a country is named under a code no buyer can send",
        MARKETPLACE,
        '    "US": "United States", "VN": "Vietnam", "ZA": "South Africa",',
        '    "US": "United States", "VN": "Vietnam", "ZA": "South Africa",\n'
        '    "USA": "United States",',
        "the server names a country under a key `country_name` can never be "
        "handed -- the checkout picker drops any code that is not two "
        "characters, so the entry is a country the buyer cannot select and the "
        "supplier will never be told about. This mutation replaced a per-request "
        "`len(value) != 2` check that could not fire: `country_name` already "
        "answers \"\" for any code its table does not hold, so the next field "
        "assembled refused a misshapen code one line later",
    ),
    (
        "a half-complete address is reported as no address at all",
        FULFILLMENT,
        "    DESTINATION_INCOMPLETE: \"order_destination_incomplete\",",
        "    DESTINATION_INCOMPLETE: \"order_destination_missing\",",
        "a UK buyer's order -- valid at checkout, one `address_region` short of "
        "what CJ requires -- tells the merchant the address is missing. They go "
        "looking for something that is there, and the one field that is actually "
        "absent is never named",
    ),
    # --------------------------------------------- what must not reach the wire
    (
        "the frozen checkout blob travels to the caller",
        FULFILLMENT,
        '            metadata = json.loads(item.pop("metadata_json") or "{}")',
        '            metadata = json.loads(item.get("metadata_json") or "{}")',
        "the whole frozen checkout record -- the buyer's street, phone and the "
        "commercial quote -- rides out on a fulfilment payload that has no use "
        "for it. Read to answer \"can this be ordered?\", and the answer has to "
        "travel without the record",
    ),
    (
        "the payments ledger key travels to the caller",
        FULFILLMENT,
        '        item.pop("seller_transaction_id", None)',
        "",
        "a `seller_transactions` primary key on a supplier fulfilment screen. "
        "Nothing there reads it, and a field nothing reads is the field that "
        "leaks the first time a payload is forwarded somewhere buyer-facing",
    ),
    # ------------------------------------- the quote nothing could assemble
    (
        "freight is quoted from a hardcoded origin",
        FULFILLMENT,
        '    origin = _stocked_origin(binding["pid"], binding["vid"], connection_id=connection_id,\n'
        '                             business_id=business_id, store_id=store_id,\n'
        '                             actor_user_id=actor_user_id, context=context, adapter=adapter)',
        '    origin = "CN"',
        "the freight price is for a shipment from a country that may hold none "
        "of the stock. The merchant approves a landed cost the supplier will not "
        "honour, and only a warehouse CJ has verified reports IN_STOCK at all",
    ),
    (
        "goods of unknown routing class are quoted as ordinary",
        FULFILLMENT,
        '        raise FulfillmentError("supplier_logistics_properties_unknown")',
        '        properties = ["ORDINARY"]',
        "CJ routes batteries, liquids and magnets differently. Substituting a "
        "plausible default quotes the wrong service for exactly the goods where "
        "the routing matters most",
    ),
    (
        "an unavailable option is priced as if only the goods cost money",
        FULFILLMENT,
        "                    expected = int(items_cost + freight)",
        "                    expected = int(items_cost)",
        "an option CJ refused is shown with a landed cost that omits freight, "
        "which is lower than every option that would actually ship. It is the "
        "cheapest row on the screen and it cannot be bought",
    ),
    # ------------------------------ the enumeration with no compiler spanning it
    (
        "the client re-derives readiness from the blocker list",
        API,
        "    canPlaceSupplierOrder: raw.can_place_supplier_order === true,",
        "    canPlaceSupplierOrder: blockers.length === 0,",
        "two evaluators of one question, against section 61. A server that "
        "learns a new reason to refuse -- or that answers a blocker list the "
        "client trims -- is overruled by the client, which says the order is "
        "ready and lets the merchant press an action that cannot succeed",
    ),
    (
        "the client reads the product's SKU off the obligation",
        API,
        "    supplierSku: textOrNull(raw.supplier_sku),",
        "    supplierSku: textOrNull(raw.external_sku),",
        "the field is gone from this payload, so every obligation shows no "
        "supplier code and falls back to the variant id. The merchant matches "
        "the wrong identifier by hand against the supplier's console",
    ),
    (
        "a blocker the backend emits is dropped from the mobile list",
        API,
        '  "SUPPLIER_SKU_MISSING",\n  "SUPPLIER_COST_UNKNOWN"\n] as const;',
        '  "SUPPLIER_COST_UNKNOWN"\n] as const;',
        "gap 13's defect at a new seam. A merchant is told only that something "
        "is wrong, when the server said exactly which listing is not linked to "
        "a supplier product code",
    ),
    (
        "the blocker copy table is widened back to Record<string, …>",
        API,
        "export const SUPPLIER_OBLIGATION_BLOCKER_COPY: Record<SupplierObligationBlocker, string> = {",
        "export const SUPPLIER_OBLIGATION_BLOCKER_COPY: Record<string, string> = {",
        "the compile error that catches a blocker added without copy is gone, so "
        "the table can silently fall behind the union -- which is exactly how "
        "three publish codes came to have no copy",
    ),
    (
        "an unrecognised blocker is shown as its identifier",
        API,
        "    SUPPLIER_OBLIGATION_BLOCKER_COPY[blocker as SupplierObligationBlocker] ||\n"
        '    "Something about this order stops it being sent to your supplier"',
        "    SUPPLIER_OBLIGATION_BLOCKER_COPY[blocker as SupplierObligationBlocker] || blocker",
        "a server ahead of this build puts `DESTINATION_INCOMPLETE` on the "
        "merchant's screen. The gap 13 failure verbatim, on a surface where the "
        "identifier sits beside money owed",
    ),
    (
        "a merchant is asked to supply the buyer's delivery address",
        API,
        '  DESTINATION_MISSING: "This order has no delivery address on record",',
        '  DESTINATION_MISSING: "Enter the delivery address for this order",',
        "there is no field for it and there must not be: the address is what the "
        "buyer paid against, and a merchant who could type one could redirect a "
        "parcel. The copy invites them to look for a control that does not exist",
    ),
    # --------------------------------------------- the screen the merchant sees
    (
        "an already-placed order is listed as blocked",
        SCREEN,
        "  const blockers = row.supplierOrderPlaced ? [] : row.blockers;",
        "  const blockers = row.blockers;",
        "every placed order carries SUPPLIER_ORDER_ALREADY_PLACED, so the whole "
        "completed half of the backlog grows a warning saying it cannot be "
        "ordered -- beside a state saying it already was",
    ),
    (
        "only the first reason a sale cannot be ordered is shown",
        SCREEN,
        "      {blockers.map((blocker) => (",
        "      {blockers.slice(0, 1).map((blocker) => (",
        "a merchant fixes the one reason shown, comes back, and finds another. "
        "Which is the shape of problem this repo keeps making -- a symptom "
        "addressed while the rest of the list stays hidden",
    ),
    (
        "the unorderable count is re-derived from the blocker list",
        SCREEN,
        "  const blocked = rows.filter((row) => !row.supplierOrderPlaced && !row.canPlaceSupplierOrder).length;",
        "  const blocked = rows.filter((row) => row.blockers.length > 0).length;",
        "the header counts every placed order as blocked, so the number above "
        "the list disagrees with the rows underneath it -- and it is the number "
        "a merchant reads first",
    ),
    # -------------------------------- the table that existed in one language
    (
        "the country table drifts from the checkout picker",
        MARKETPLACE,
        '    "GR": "Greece", "HK": "Hong Kong SAR China", "HR": "Croatia",',
        '    "HK": "Hong Kong SAR China", "HR": "Croatia",',
        "a country the picker offers and the server cannot name. The buyer "
        "completes a valid checkout and the paid order can never be sent to a "
        "supplier, because `shippingCountry` comes back empty",
    ),
    # ------------------------------------------------- inverted, and the control
    (
        "the destination local is renamed",
        FULFILLMENT,
        "    destination = {}\n",
        "    assembled = {}\n",
        "a pure rename is pinned by something reading the source text rather "
        "than the behaviour",
    ),
    (
        "the outbound text pass on the destination is removed",
        FULFILLMENT,
        "    return {key: _text(value, key, 500) for key, value in destination.items()}",
        "    return dict(destination)",
        "INVERTED. `validate_details` has already cleaned, length-capped and "
        "tag-stripped every value in the frozen record, so for any address "
        "checkout could have written the two are identical. It stays as the "
        "gate this module has always had on what reaches a provider, against a "
        "record written by an older version of the checkout -- an argument about "
        "the past, which no test today can make",
    ),
    (
        "a comment is added above the blocker assembly",
        FULFILLMENT,
        "        blockers = []\n",
        "        # no-op control\n        blockers = []\n",
        "the battery is reporting something other than the mutation",
    ),
]

# Behaviour-preserving by construction, each for a reason written beside it and
# in the docstring above.
#
# Keyed by name rather than by position, from the start. Inserting a mutation
# renumbers every one after it, and a set of indices would then quietly relabel
# a real mutation as inverted -- which is to say, would stop demanding that
# anything catch it. That is this battery's own version of the defect it exists
# to find.
INVERTED_NAMES = {
    "the destination local is renamed",
    "the outbound text pass on the destination is removed",
}
# The rename is inverted only if it is *complete*; a half-done rename would be a
# NameError and would be "caught" for the wrong reason. It was, on the battery's
# first run: `supplier_destination` names the local four times and this tuple
# covered one of the three that follow the declaration, so the battery reported a
# pure rename as pinned when what it had found was its own NameError. Every
# reference is listed, and `_assert_rename_is_total` below refuses to run the
# mutation if a bare `destination` survives it.
RENAME_NAME = "the destination local is renamed"
RENAME_COMPANIONS = (
    ("            return {}, [DESTINATION_INCOMPLETE]\n"
     "        destination[supplier_key] = value\n"
     "    for supplier_key, frozen_key in _DESTINATION_OPTIONAL:",
     "            return {}, [DESTINATION_INCOMPLETE]\n"
     "        assembled[supplier_key] = value\n"
     "    for supplier_key, frozen_key in _DESTINATION_OPTIONAL:"),
    ("            destination[supplier_key] = value.strip()\n"
     "    return destination, []",
     "            assembled[supplier_key] = value.strip()\n"
     "    return assembled, []"),
)
NO_OP_NAME = "a comment is added above the blocker assembly"


def _bare_destination_references(source: str) -> int:
    """How many times `supplier_destination`'s *code* still names its old local.

    The rename mutation is only inverted if it is total. Counting the name
    inside that one function -- rather than trusting a list of anchors to be
    complete -- is the difference between measuring that a rename is
    behaviour-preserving and asserting it.

    The leading docstring is dropped first. It discusses the local by name in
    prose, and prose does not raise `NameError`; counting it reported a total
    rename as partial, which is the same error in the other direction.
    """
    body = source.split("def supplier_destination(", 1)[-1].split("\ndef ", 1)[0]
    code = body.split('"""', 2)[-1] if body.count('"""') >= 2 else body
    return len(re.findall(r"\bdestination\b(?!_)", code))


_NAMES = [name for name, *_ in MUTATIONS]
assert len(_NAMES) == len(set(_NAMES)), "two mutations share a name"
for _declared in INVERTED_NAMES | {RENAME_NAME, NO_OP_NAME}:
    assert _declared in _NAMES, f"no mutation named {_declared!r}"


def main(argv: list[str]) -> int:
    only: set[int] = set()
    if argv and argv[0] == "--only" and len(argv) > 1:
        only = {int(part) for part in argv[1].split(",") if part.strip()}

    survivors: list[tuple[str, str]] = []
    control_failed = False

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
        if name == RENAME_NAME:
            aborted = False
            for before, after in RENAME_COMPANIONS:
                if mutated.count(before) != 1:
                    print(f"{index:2}. ERROR    {name}\n        rename companion anchor missing")
                    survivors.append((name, "rename companion anchor missing"))
                    aborted = True
                    break
                mutated = mutated.replace(before, after, 1)
            if aborted:
                continue
            leftover = _bare_destination_references(mutated)
            if leftover:
                print(f"{index:2}. ERROR    {name}\n        rename is partial: "
                      f"{leftover} reference(s) to `destination` survive inside "
                      "`supplier_destination`, so this would be a NameError "
                      "rather than a behaviour-preserving rename")
                survivors.append((name, "rename is partial"))
                continue
        path.write_text(mutated)
        try:
            caught_by = first_failing()
        finally:
            path.write_text(original)

        caught = caught_by is not None

        if name == NO_OP_NAME:
            if caught:
                print(f"{index:2}. CONTROL FAILED {name} (reported by {caught_by})")
                control_failed = True
            else:
                print(f"{index:2}. control  {name} (no-op, correctly survived)")
            continue

        if name in INVERTED_NAMES:
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
    ran_names = {_NAMES[index - 1] for index in ran}
    real = len(ran_names - INVERTED_NAMES - {NO_OP_NAME})
    ran_inverted = len(ran_names & INVERTED_NAMES)
    print()
    if only:
        print(f"Filtered run: {sorted(only)} of {len(MUTATIONS)}. Not a full battery.")
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing above can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {real} mutations gave the wrong answer:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {real} real mutations caught, "
          f"{ran_inverted} inverted mutations correctly ignored, "
          f"{'no-op control survived' if NO_OP_NAME in ran_names else 'control not run'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
