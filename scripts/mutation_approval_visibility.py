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
#: A git worktree has no `.venv` of its own -- it lives in the main checkout --
#: and the battery has to mutate the tree it was launched from, not that one. So
#: fall back to whichever interpreter is running this, which is the venv's own
#: when invoked the documented way.
_VENV = ROOT / ".venv/bin/python"
PY = _VENV if _VENV.exists() else Path(sys.executable)

NATIVE = ROOT / "mobile-native"

LIFECYCLE = ROOT / "services/marketplace_listing_lifecycle.py"
BOT = ROOT / "bot.py"
SELLER_STORE = NATIVE / "src/screens/SellerStoreScreen.tsx"

VISIBILITY = "tests/marketplace/test_marketplace_approval_visibility.py"
REACHABILITY = "tests/marketplace/test_marketplace_moderation_reachability.py"
POLICY = "tests/test_marketplace_listing_lifecycle.py"
PILL = "src/screens/__tests__/SellerStorePublicationPill.test.tsx"


class SuiteUnavailable(RuntimeError):
    """The suite could not be executed, so it proved nothing either way."""


def run_suite(suite: str) -> subprocess.CompletedProcess:
    """Dispatch on the suite's own path; the battery spans both runtimes."""
    if suite.endswith(".tsx"):
        # A mutation is "caught" when the suite exits non-zero, and a missing
        # toolchain exits non-zero too. `mobile-native/node_modules` is not
        # installed in a fresh worktree, which silently turned all three pill
        # mutations into passes -- the battery reporting a clean sweep while
        # running no JavaScript at all. Checked rather than inferred from the
        # output, because that is the one failure this script cannot self-detect.
        if not (NATIVE / "node_modules/.bin/jest").exists():
            raise SuiteUnavailable("mobile-native/node_modules is not installed")
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
        "the price rule is removed from the table",
        LIFECYCLE,
        '    PublicationRule(\n'
        '        key="priced",\n'
        '        # Not a new code. A buyer can do nothing about an unpriced listing and it\n'
        '        # may well come back once the merchant prices it, which is exactly what\n'
        '        # ``ITEM_UNAVAILABLE`` already means; native clients branch on these\n'
        '        # strings and a fourth would reach them as the default "unavailable".\n'
        '        denial_code="ITEM_UNAVAILABLE",\n'
        '        seller_label="Price needed",\n'
        '        moderator_note="the listing has no price",\n'
        '        satisfied=_is_priced,\n'
        '        passes_when_unknown=False,\n'
        '    ),\n',
        '',
        POLICY,
        "the state production was found in: approved, well-stocked listings "
        "offered to buyers with no price on them, and no query anywhere wrong",
    ),
    (
        "the price rule is stated in Python but not in SQL",
        LIFECYCLE,
        '        # The price invariant, twin of `_is_priced`. A supplier import writes a\n'
        '        # blank label on purpose and moderation does not look at the price, so\n'
        '        # without this clause an approved, well-stocked, unpriced listing reached\n'
        '        # buyer discovery and every surface rendered the amount slot empty.\n'
        '        f"AND NULLIF(TRIM({alias}.price_label),\'\') IS NOT NULL "\n',
        '',
        POLICY,
        "the whole failure mode of this module in one line: no buyer surface "
        "calls is_public, they run this string, so a rule that lives only in "
        "Python leaves the bug live under a green unit suite",
    ),
    (
        "a deliberate zero-price label is read as no price",
        LIFECYCLE,
        '    return bool(str(listing.get("price_label") or "").strip())',
        '    return str(listing.get("price_label") or "").strip().lower() not in {\n'
        '        "", "free", "request access", "paid later", "premium later"}',
        POLICY,
        "every free and request-access listing on the platform goes off sale -- "
        "a far larger outage than the bug, and why the rule is blank-vs-named "
        "rather than the parser's cents",
    ),
    (
        "an unprojected price column is read as no price",
        LIFECYCLE,
        '    if "price_label" not in listing:\n'
        '        return None\n'
        '    return bool(str(listing.get("price_label") or "").strip())',
        '    return bool(str(listing.get("price_label") or "").strip())',
        POLICY,
        "the merchant is told to fix a price nobody looked at; the gate may fail "
        "closed on silence but the description must never accuse from it",
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
        except SuiteUnavailable as exc:
            print(f"{index:2}. ERROR    {name}\n        {suite} could not run: {exc}")
            survivors.append((name, f"suite could not run: {exc}"))
            continue
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
