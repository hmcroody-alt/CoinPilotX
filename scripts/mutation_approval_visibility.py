#!/usr/bin/env python3
"""Mutation battery for approval visibility (gap 7, residual).

Gap 7 fixed the half where Approve *refused* the listings `drafts.publish`
produces. The residual was the other half: Approve accepting, returning 200,
writing an audit entry, and leaving the listing invisible to every buyer -- with
the merchant's own dashboard reading "Live" and nothing anywhere naming the
missing condition.

The fix derives that condition once, in
`marketplace_listing_lifecycle.PUBLICATION_RULES`, and projects it three ways:
a buyer code, a merchant chip, and a moderator sentence. Three consumers reading
one table is exactly the shape that rots quietly -- each mutation below breaks
one projection, or collapses the gate and the description back into one reading,
and then runs the suite that is supposed to notice.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv/bin/python"

NATIVE = ROOT / "mobile-native"

LIFECYCLE = ROOT / "services/marketplace_listing_lifecycle.py"
BOT = ROOT / "bot.py"
SELLER_STORE = NATIVE / "src/screens/SellerStoreScreen.tsx"

VISIBILITY = "tests/marketplace/test_marketplace_approval_visibility.py"
REACHABILITY = "tests/marketplace/test_marketplace_moderation_reachability.py"
PILL = "src/screens/__tests__/SellerStorePublicationPill.test.tsx"


def run_suite(suite: str) -> subprocess.CompletedProcess:
    """Dispatch on the suite's own path; the battery spans both runtimes."""
    if suite.endswith(".tsx"):
        return subprocess.run(["npx", "jest", "--runTestsByPath", suite, "--silent"],
                              cwd=NATIVE, capture_output=True, text=True)
    # One file per process: importing `bot` binds DATABASE_URL process-wide.
    return subprocess.run([str(PY), "-m", "pytest", suite, "-q", "-x"],
                          cwd=ROOT, capture_output=True, text=True)


# (name, file, old, new, suite, what a surviving mutant would mean)
MUTATIONS = [
    (
        "seller_label answers Live from the merchant's two columns again",
        LIFECYCLE,
        '    blocker = live_blocker(listing)\n'
        '    if blocker:\n'
        '        return RULES_BY_KEY[blocker].seller_label',
        '    blocker = ""',
        VISIBILITY,
        "the exact defect this was opened for: a merchant told their listing is "
        "live while no buyer query returns it, and no other explanation for "
        "zero orders than that nobody wanted the product",
    ),
    (
        "the moderator is told only that the listing was updated",
        BOT,
        '            message = admin_marketplace_decision_message(cur, action, listing_id)',
        '            message = "Listing updated."',
        VISIBILITY,
        "an approval that produced nothing reports success; the moderator's next "
        "signal is a merchant asking why an approved listing has no orders",
    ),
    (
        "the moderation message names a problem but not which one",
        BOT,
        '    return ("Listing updated, but it is still not visible to buyers: "\n'
        '            + marketplace_listing_lifecycle.blocker_note(blocker) + ".")',
        '    return "Listing updated, but it is still not visible to buyers."',
        VISIBILITY,
        "the reason is already derived and thrown away at the last step, leaving "
        "a warning a reviewer can act on only by guessing",
    ),
    (
        "the decision message never re-reads the seller record",
        BOT,
        '        f"""SELECT l.*, COALESCE(ms.status,\'missing\') AS seller_status,\n'
        '                   {marketplace_seller_identity.store_name_select(\'ms\')}\n'
        '            FROM marketplace_listings l\n'
        '            LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id\n'
        '            WHERE l.id=? LIMIT 1""",',
        '        """SELECT * FROM marketplace_listings WHERE id=? LIMIT 1""",',
        VISIBILITY,
        "the message is computed from the two columns the route just wrote, so "
        "it can only ever report success -- the join is the whole mechanism",
    ),
    (
        "publication_blocker treats an unanswerable rule as a failure",
        LIFECYCLE,
        '        if rule.satisfied(listing, quantity) is False:\n'
        '            return rule.key',
        '        if not rule.satisfied(listing, quantity):\n'
        '            return rule.key',
        VISIBILITY,
        "every payload built without a seller join stops saying Live -- a louder "
        "and more damaging bug than the one being fixed, and the reason the "
        "three-valued predicate exists",
    ),
    (
        "the gate abstains the way the description does",
        LIFECYCLE,
        '        if verdict is None:\n'
        '            verdict = rule.passes_when_unknown',
        '        if verdict is None:\n'
        '            verdict = True',
        VISIBILITY,
        "a row that cannot prove its seller is approved is sold to buyers; the "
        "safe default for a gate is no, and only the label may abstain",
    ),
    (
        "the stock rule is reduced to quantity > 0",
        LIFECYCLE,
        '    if normalized(listing.get("product_type") or listing.get("listing_type")) in STOCKLESS_TYPES:\n'
        '        return True\n'
        '    if "quantity" not in listing:\n'
        '        return None\n'
        '    return inventory_available(listing, quantity)',
        '    if "quantity" not in listing:\n'
        '        return None\n'
        '    return inventory_available(listing, quantity)',
        VISIBILITY,
        "every course, service, event and booking on the platform goes off sale "
        "in one commit, because none of them has stock to run out of",
    ),
    (
        "an explicitly missing seller row stops counting as evidence",
        LIFECYCLE,
        '    return normalized(raw) == "approved"',
        '    return normalized(raw) != "suspended"',
        VISIBILITY,
        "COALESCE(ms.status,'missing') is selected precisely so a LEFT JOIN "
        "miss is an answer; reading only 'suspended' as bad makes that sentinel "
        "worth nothing",
    ),
    (
        "a blank store name is no longer a blocker",
        LIFECYCLE,
        '    if not seller_identity.store_identity_known(listing):\n'
        '        return None\n'
        '    return seller_identity.has_store_identity(listing)',
        '    return True',
        VISIBILITY,
        "a nameless storefront is approved into a marketplace that then has to "
        "invent an identity for it, and the only name lying around is the "
        "account holder's personal one",
    ),
    (
        "approve refuses the shapes it cannot make public",
        BOT,
        '            if action in {"approve", "reject", "request_changes"} and not marketplace_listing_lifecycle.awaiting_moderation(listing_row):',
        '            if action in {"approve", "reject", "request_changes"} and (not marketplace_listing_lifecycle.awaiting_moderation(listing_row) or marketplace_listing_lifecycle.publication_blocker(listing_row)):',
        VISIBILITY,
        "gap 7's own bug in a new place: an out-of-stock listing can never be "
        "approved, so it can never become sellable when stock returns",
    ),
    (
        "the healthy listing is quietly held back too",
        LIFECYCLE,
        '        if rule.satisfied(listing, quantity) is False:\n'
        '            return rule.key\n'
        '    return ""',
        '        if rule.satisfied(listing, quantity) is False:\n'
        '            return rule.key\n'
        '    return "in_stock"',
        VISIBILITY,
        "the control is gone: a fix that simply stopped saying Live for everyone "
        "would satisfy every negative assertion in the file",
    ),
    (
        "moderation reachability regresses while visibility still passes",
        BOT,
        '            if action in {"approve", "reject", "request_changes"} and not marketplace_listing_lifecycle.awaiting_moderation(listing_row):',
        '            if action in {"approve", "reject", "request_changes"} and previous_status not in marketplace_listing_lifecycle.AWAITING_DECISION_STATES:',
        REACHABILITY,
        "gap 7's original 409 is back on every listing drafts.publish produces, "
        "and the older suite is the only one that would say so",
    ),
    (
        "a rule is added without the strings its consumers read",
        LIFECYCLE,
        '    PublicationRule(\n'
        '        key="in_stock",\n'
        '        denial_code="OUT_OF_STOCK",\n'
        '        seller_label="Out of stock",',
        '    PublicationRule(\n'
        '        key="in_stock",\n'
        '        denial_code="OUT_OF_STOCK",\n'
        '        seller_label="",',
        VISIBILITY,
        "seller_label renders an empty chip where a state belongs, and the table "
        "stops being three complete projections of one rule",
    ),
    (
        "live_blocker judges a draft by the publication rules",
        LIFECYCLE,
        '    if (\n'
        '        normalized(listing.get("status")) in PUBLIC_STATUSES\n'
        '        and normalized(listing.get("approval_status")) in APPROVED_STATES\n'
        '    ):\n'
        '        return publication_blocker(listing, quantity)\n'
        '    return ""',
        '    return publication_blocker(listing, quantity)',
        VISIBILITY,
        "an unfinished draft is labelled 'Out of stock' instead of 'Draft', "
        "replacing the state its merchant needs to act on with a true but "
        "useless one",
    ),
    (
        "the seller store pill re-derives publication from the status column",
        SELLER_STORE,
        '  const blocker = String(listing.publication_blocker || "");\n'
        '  if (blocker) return BLOCKER_STATUS_KEYS[blocker] ?? "pending";',
        '',
        PILL,
        "the merchant's own app shows one neutral chip for a suspended seller, "
        "an unnamed store, an empty shelf and a healthy listing alike",
    ),
    (
        "published stops reaching the live pill",
        SELLER_STORE,
        '  if (["active", "approved", "live", "published"].includes(raw)) return "live";',
        '  if (["active", "approved", "live"].includes(raw)) return "live";',
        PILL,
        "the value drafts.publish actually writes falls past every branch, so "
        "the live pill never renders for a live listing",
    ),
    (
        "an absent blocker field is read as a blocker",
        SELLER_STORE,
        '  if (blocker) return BLOCKER_STATUS_KEYS[blocker] ?? "pending";',
        '  if (blocker !== "") return BLOCKER_STATUS_KEYS[blocker] ?? "pending";\n'
        '  if (listing.publication_blocker === undefined) return "pending";',
        PILL,
        "cached payloads and any endpoint not yet updated send no blocker at "
        "all; reading that silence as a blocker puts every listing on an older "
        "build into review",
    ),
    (
        "no-op control",
        LIFECYCLE,
        'RULES_BY_KEY = {rule.key: rule for rule in PUBLICATION_RULES}',
        'RULES_BY_KEY = {rule.key: rule for rule in tuple(PUBLICATION_RULES)}',
        VISIBILITY,
        "(control)",
    ),
]

#: The last entry is a deliberate no-op: `tuple()` of a tuple is the same tuple.
#: If the battery reports it caught, the battery is measuring noise -- a flaky
#: suite, a stale `__pycache__`, or an anchor that changed more than it looked
#: like it did.
NO_OP_INDEX = len(MUTATIONS)


def main() -> int:
    survivors = []
    control_failed = False

    for index, (name, path, old, new, suite, meaning) in enumerate(MUTATIONS, 1):
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR    {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            result = run_suite(suite)
        finally:
            path.write_text(original)

        caught = result.returncode != 0
        if index == NO_OP_INDEX:
            if caught:
                print(f"{index:2}. CONTROL FAILED {name}")
                control_failed = True
            else:
                print(f"{index:2}. control  {name} (no-op, correctly survived)")
            continue

        if caught:
            print(f"{index:2}. caught   {name}")
        else:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))

    print()
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing below can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS) - 1} real mutations survived:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {len(MUTATIONS) - 1} real mutations caught; the no-op control survived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
