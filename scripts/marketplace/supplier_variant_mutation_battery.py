#!/usr/bin/env python3
"""Anti-vacuity battery for the supplier-variant suite.

A green suite proves nothing on its own. A suite that asserts the wrong thing, or
asserts nothing at all, is also green — and this one went green on its very first
run, which is exactly when a suite deserves the least trust. This script breaks
``services/marketplace_variants.py`` and ``services/marketplace_supplier_schema.py``
in a series of specific, plausible ways — each one a change a reasonable engineer
might make while "simplifying" or "tidying" — and requires the paired suite to go
red for every one of them.

Why these particular mutations
------------------------------
``COMMERCE_DROPSHIPPING_FOUNDATION_MAP.md`` identified three distinctions that the
existing product row cannot express and that every cheaper implementation of this
one would lose:

* unknown stock is not out of stock,
* unknown cost is not zero cost,
* "not yours" and "not there" are the same refusal.

Each collapses under a one-line edit that looks like a cleanup and that no
obvious test notices. Those edits are in here. So are the writer-side ones:
an order-dependent variant key (duplicates the catalogue on every sync), a
non-additive override list (destroys merchant edits on the second field), and a
``None`` supplier source read as "unrestricted" (lets a stray sync overwrite a
hand-authored product).

The reconciliation group is different in kind. Those mutants do not break a
distinction inside one module — they put the *two ledgers* back. Each is a
relapse to the shape this repo had before ``marketplace_listings`` was made the
single canonical product authority: the CJ gateway reading the empty
``business_os_mkt_products`` again, a supplier mapping with no listing behind it,
``supplier_product_links`` reappearing beside ``marketplace_product_sources``, a
provider sync reaching across into the merchant's retail price. Those live in
three different modules and are judged by three different suites, because a
boundary is only defended if the tests on *that* boundary notice.

The count is deliberately not written down in this docstring. A number in prose
is a fact that stops being true the first time somebody adds a mutation and does
not notice; the summary line at the end reports the real one.

A mutation that survives is reported as a hole in the tests, not as a pass. An
anchor that cannot be applied is reported as an error in *this* script — never as
a silent skip, because a mutation that quietly fails to apply is indistinguishable
from one the suite caught.

Usage::

    .venv/bin/python scripts/marketplace/supplier_variant_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay
directory of symlinks, with the one mutated file materialised as a real copy.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Each mutation is paired with the suite that is supposed to defend against it,
#: rather than being run against everything. Pairing is the stricter claim: it
#: says *this* suite covers *this* invariant, so a mutation caught only by some
#: unrelated file's incidental breakage would still be reported as a hole.
SUITE = "tests/marketplace/test_supplier_variants.py"

VARIANTS = "services/marketplace_variants.py"
SCHEMA = "services/marketplace_supplier_schema.py"

#: The reconciliation mutants are judged by the suites that own the boundary each
#: one breaks, which is not the supplier-variant suite. A mutant that reaches into
#: the CJ gateway has to be answered by the CJ gateway's own tests; catching it
#: with a marketplace unit test would prove the wrong thing.
GATEWAY_SUITE = "tests/business_os/test_cj_gateway.py"
FULFILLMENT_SUITE = "tests/business_os/test_cj_fulfillment.py"
LEDGER_SUITE = "tests/marketplace/test_supplier_ledger_authority.py"

GATEWAY = "services/business_os/suppliers/gateway.py"
FULFILLMENT = "services/business_os/suppliers/fulfillment.py"


#: (name, file, old, new). ``old`` must appear exactly once in the file.
#:
#: ``old``/``new`` may instead be a list of ``(old, new)`` pairs, applied to the
#: same file in one mutant. Some simplifications are genuinely two-site and a
#: one-site version of them proves the opposite of what it looks like: dropping a
#: column from the CREATE alone is *survived on purpose*, because the defensive
#: ALTER list adds it straight back — which is the design working, not a hole. To
#: actually remove the column, the mutation has to remove it from both places, so
#: the battery has to be able to express that.
READ_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "unknown stock reads as out of stock",
        VARIANTS,
        "        except (TypeError, ValueError):\n            return UNKNOWN\n    return UNKNOWN",
        "        except (TypeError, ValueError):\n            return UNAVAILABLE\n    return UNAVAILABLE",
    ),
    (
        "an unrecognised stock state is read as buyable",
        VARIANTS,
        "    if state == STOCK_IN_STOCK:\n        quantity = variant.get(\"stock_quantity\")",
        "    if state != STOCK_OUT_OF_STOCK:\n        quantity = variant.get(\"stock_quantity\")",
    ),
    (
        "a zero count still counts as in stock",
        VARIANTS,
        "            return AVAILABLE if int(quantity) > 0 else UNAVAILABLE",
        "            return AVAILABLE if int(quantity) >= 0 else UNAVAILABLE",
    ),
    (
        "in stock with no count is treated as unknown",
        VARIANTS,
        "            # count would make every such variant permanently unbuyable.\n            return AVAILABLE",
        "            # count would make every such variant permanently unbuyable.\n            return UNKNOWN",
    ),
    (
        "an archived variant is still available",
        VARIANTS,
        "    if str(variant.get(\"status\") or \"active\").strip().lower() != \"active\":\n        return UNAVAILABLE",
        "    if False:\n        return UNAVAILABLE",
    ),
    (
        "the aggregate forgets that anything was unknown",
        VARIANTS,
        "    return UNKNOWN if seen_unknown else UNAVAILABLE",
        "    return UNAVAILABLE",
    ),
    (
        "a listing with no variants is reported sold out",
        VARIANTS,
        "    if not seen_any:\n        return UNKNOWN",
        "    if not seen_any:\n        return UNAVAILABLE",
    ),
]

MONEY_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "unknown cost is treated as zero cost",
        VARIANTS,
        "    cost = variant.get(\"cost_cents\")\n    if cost is None or retail_cents is None:\n        return None",
        "    cost = variant.get(\"cost_cents\") or 0\n    if retail_cents is None:\n        return None",
    ),
    (
        "a known zero cost is treated as unknown",
        VARIANTS,
        "    if cost is None or retail_cents is None:\n        return None",
        "    if not cost or retail_cents is None:\n        return None",
    ),
    (
        "a negative margin is clamped to zero",
        VARIANTS,
        "        return int(retail_cents) - int(cost)",
        "        return max(0, int(retail_cents) - int(cost))",
    ),
    (
        "a null cost is coerced to zero on the way into the row",
        VARIANTS,
        "    if value is None:\n        return None\n    try:\n        amount = int(value)",
        "    if value is None:\n        return 0\n    try:\n        amount = int(value)",
    ),
    (
        "negative money is accepted",
        VARIANTS,
        "    if amount < 0:\n        raise VariantRejected(f\"{field} cannot be negative\")",
        "    if False:\n        raise VariantRejected(f\"{field} cannot be negative\")",
    ),
    (
        "non-numeric money is silently dropped instead of refused",
        VARIANTS,
        "    except (TypeError, ValueError):\n        raise VariantRejected(f\"{field} must be an integer number of minor units\")",
        "    except (TypeError, ValueError):\n        return None",
    ),
]

KEY_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the variant key depends on option order",
        VARIANTS,
        "    pairs = sorted(",
        "    pairs = list(",
    ),
    (
        "the variant key is case sensitive",
        VARIANTS,
        "        (str(o.get(\"name\") or \"\").strip().casefold(),\n         str(o.get(\"value\") or \"\").strip().casefold())",
        "        (str(o.get(\"name\") or \"\").strip(),\n         str(o.get(\"value\") or \"\").strip())",
    ),
    (
        "the key ignores option values, so siblings collide",
        VARIANTS,
        "    return \"|\".join(f\"{name}={value}\" for name, value in pairs)",
        "    return \"|\".join(f\"{name}\" for name, value in pairs)",
    ),
    (
        "a variant with no options keys to the empty string",
        VARIANTS,
        "    if not pairs:\n        return \"-\"",
        "    if not pairs:\n        return \"\"",
    ),
    (
        "duplicate option names are silently deduplicated",
        VARIANTS,
        "            raise VariantRejected(f\"duplicate option name: {name}\")",
        "            continue",
    ),
    (
        "a malformed option is dropped instead of refused",
        VARIANTS,
        "        if not name or not value:\n            raise VariantRejected(\"each option needs a non-empty name and value\")",
        "        if not name or not value:\n            continue",
    ),
    (
        "an unbounded option list is tolerated",
        VARIANTS,
        "    if len(options) > MAX_OPTIONS_PER_VARIANT:",
        "    if False:",
    ),
]

WRITE_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "re-import inserts a second row instead of updating",
        VARIANTS,
        "    row = cur.fetchone()\n    if row is not None:\n        try:\n            existing_id = int(row[\"id\"])",
        "    row = cur.fetchone()\n    if False:\n        try:\n            existing_id = int(row[\"id\"])",
    ),
    (
        "the variant cap is not enforced",
        VARIANTS,
        "    if current >= MAX_VARIANTS_PER_LISTING:",
        "    if False:",
    ),
    (
        "the cap is also checked on update, so a full listing cannot be corrected",
        VARIANTS,
        "        return existing_id\n\n    # The cap is checked only on insert.",
        "        cur.execute(f\"SELECT COUNT(*) AS n FROM {VARIANT_TABLE} WHERE listing_id=?\",\n                    (int(listing_id),))\n        if int(cur.fetchone()[0]) >= MAX_VARIANTS_PER_LISTING:\n            raise VariantRejected(\"at the cap\")\n        return existing_id\n\n    # The cap is checked only on insert.",
    ),
    (
        "an unrecognised incoming stock state is stored as in stock",
        VARIANTS,
        "    if resolved not in _schema.STOCK_STATES:\n        resolved = STOCK_UNKNOWN",
        "    if resolved not in _schema.STOCK_STATES:\n        resolved = STOCK_IN_STOCK",
    ),
    (
        "negative stock is accepted",
        VARIANTS,
        "    if count < 0:\n        raise VariantRejected(\"stock_quantity cannot be negative\")",
        "    if False:\n        raise VariantRejected(\"stock_quantity cannot be negative\")",
    ),
    (
        "options are stored case-folded, losing what the supplier sent",
        VARIANTS,
        "        cleaned.append({\"name\": name, \"value\": value})",
        "        cleaned.append({\"name\": name.casefold(), \"value\": value.casefold()})",
    ),
]

OWNERSHIP_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the refusal reveals whether the listing exists",
        VARIANTS,
        "    if owner is None or int(owner) != int(seller_user_id):\n        raise VariantRejected(\"listing not found\")",
        "    if owner is None:\n        raise VariantRejected(\"listing not found\")\n    if int(owner) != int(seller_user_id):\n        raise VariantRejected(\"listing belongs to another seller\")",
    ),
    (
        "a variant may be written to a listing you do not own",
        VARIANTS,
        "    _assert_owned(cur, listing_id, seller_user_id)\n\n    cleaned = normalize_options(options)",
        "    cleaned = normalize_options(options)",
    ),
    (
        "archive is not scoped to the owner",
        VARIANTS,
        "        f\"WHERE id=? AND seller_user_id=?\",\n        (_now(), int(variant_id), int(seller_user_id)),",
        "        f\"WHERE id=?\",\n        (_now(), int(variant_id)),",
    ),
    (
        "link_source skips the ownership check",
        VARIANTS,
        "    _assert_owned(cur, listing_id, seller_user_id)\n    resolved = str(provider or \"\").strip().lower()",
        "    resolved = str(provider or \"\").strip().lower()",
    ),
    (
        "mark_overridden skips the ownership check",
        VARIANTS,
        "    _assert_owned(cur, listing_id, seller_user_id)\n    source = source_for(cur, listing_id)",
        "    source = source_for(cur, listing_id)",
    ),
]

SOURCE_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "a listing may be relinked to a different supplier product",
        VARIANTS,
        "            raise VariantRejected(\n                \"this listing is already linked to a different supplier product\")",
        "            pass",
    ),
    (
        "any provider string is accepted",
        VARIANTS,
        "    if resolved not in PROVIDERS:\n        raise VariantRejected(f\"unknown provider: {provider}\")",
        "    if False:\n        raise VariantRejected(f\"unknown provider: {provider}\")",
    ),
    (
        "an unknown fulfillment mode silently defaults to dropship",
        VARIANTS,
        "    if mode not in FULFILLMENT_MODES:\n        raise VariantRejected(f\"unknown fulfillment_mode: {fulfillment_mode}\")",
        "    if mode not in FULFILLMENT_MODES:\n        mode = _schema.MODE_DROPSHIP",
    ),
    (
        "an empty provider product id is accepted",
        VARIANTS,
        "    if not reference:\n        raise VariantRejected(\"provider_product_id is required\")",
        "    if False:\n        raise VariantRejected(\"provider_product_id is required\")",
    ),
    (
        "marking an override replaces the previous list",
        VARIANTS,
        "    merged = list(source.get(\"overridden_fields\") or [])",
        "    merged = []",
    ),
    (
        "marking an override on a product with no supplier is tolerated",
        VARIANTS,
        "    if source is None:\n        raise VariantRejected(\"listing has no supplier source\")",
        "    if source is None:\n        return []",
    ),
    (
        "a product with no supplier accepts every supplier update",
        VARIANTS,
        "    if source is None:\n        return {}",
        "    if source is None:\n        return dict(incoming or {})",
    ),
    (
        "the sync filter ignores merchant ownership entirely",
        VARIANTS,
        "    return {k: v for k, v in (incoming or {}).items() if k not in owned_by_merchant}",
        "    return dict(incoming or {})",
    ),
    (
        "manual is not a first-class provider",
        SCHEMA,
        "PROVIDERS = (PROVIDER_MANUAL, \"cj\", \"printful\", \"printify\")",
        "PROVIDERS = (\"cj\", \"printful\", \"printify\")",
    ),
]

SCHEMA_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the required-column list is trimmed to whatever exists",
        SCHEMA,
        "REQUIRED_VARIANT_COLUMNS = (\"listing_id\", \"seller_user_id\", \"variant_key\",\n                            \"stock_state\", \"cost_cents\")",
        "REQUIRED_VARIANT_COLUMNS = ()",
    ),
    (
        "the source required-column list is trimmed",
        SCHEMA,
        "REQUIRED_SOURCE_COLUMNS = (\"listing_id\", \"seller_user_id\", \"provider\",\n"
        "                           \"provider_product_id\", \"fulfillment_mode\",\n"
        "                           \"supplier_connection_id\", \"provider_variant_id\",\n"
        "                           \"supplier_cost_cents\", \"sync_state\")",
        "REQUIRED_SOURCE_COLUMNS = ()",
    ),
    (
        "force does not bypass the process cache",
        SCHEMA,
        "    if _SCHEMA_READY and not force:",
        "    if _SCHEMA_READY:",
    ),
    (
        "missing columns are still reported as ready",
        SCHEMA,
        "    if missing:\n        LOGGER.error(",
        "    if False:\n        LOGGER.error(",
    ),
    (
        "the cost column is dropped from both the DDL and the defensive list",
        SCHEMA,
        [
            ("    cost_cents INTEGER,\n", ""),
            ("    (\"cost_cents\", \"INTEGER\"),\n", ""),
        ],
        None,
    ),
]


#: The reconciliation mutants (§18). Each is a *relapse*: the shape the code had
#: before the two ledgers were reconciled, or the shortcut that would put it back
#: there. They are the only mutants in this file that span repositories of
#: responsibility — supplier gateway, fulfillment, canonical mapping — which is
#: why each carries its own suite as a fifth element.
RECONCILIATION_MUTATIONS: list[tuple] = [
    (
        "the CJ lookup goes back to the legacy business_os product ledger",
        GATEWAY,
        "    row = conn.execute(\"SELECT seller_user_id FROM marketplace_listings WHERE id=?\",\n"
        "                       (listing_id,)).fetchone()",
        "    row = conn.execute(\"SELECT seller_user_id FROM business_os_mkt_products WHERE product_id=?\",\n"
        "                       (str(canonical_product_id),)).fetchone()",
        GATEWAY_SUITE,
    ),
    (
        "a supplier mapping is created without a canonical listing behind it",
        GATEWAY,
        "    row = conn.execute(\"SELECT seller_user_id FROM marketplace_listings WHERE id=?\",\n"
        "                       (listing_id,)).fetchone()\n"
        "    if row is None or row[\"seller_user_id\"] is None or int(row[\"seller_user_id\"]) != owner_user_id:\n"
        "        raise SupplierError(\"not_found\", http_status=404)\n"
        "    return listing_id, owner_user_id",
        "    return listing_id, owner_user_id",
        GATEWAY_SUITE,
    ),
    (
        "the listing is found but its owner is taken from the row, not checked",
        GATEWAY,
        "    if row is None or row[\"seller_user_id\"] is None or int(row[\"seller_user_id\"]) != owner_user_id:\n"
        "        raise SupplierError(\"not_found\", http_status=404)\n"
        "    return listing_id, owner_user_id",
        "    if row is None or row[\"seller_user_id\"] is None:\n"
        "        raise SupplierError(\"not_found\", http_status=404)\n"
        "    return listing_id, int(row[\"seller_user_id\"])",
        GATEWAY_SUITE,
    ),
    (
        "the second mapping authority is recreated by the supplier DDL",
        GATEWAY,
        "            merchant_fields_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',\n"
        "            created_at DOUBLE PRECISION NOT NULL)\"\"\")",
        "            merchant_fields_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',\n"
        "            created_at DOUBLE PRECISION NOT NULL)\"\"\")\n"
        "        conn.execute(\"\"\"CREATE TABLE IF NOT EXISTS supplier_product_links (\n"
        "            connection_id TEXT NOT NULL, canonical_product_id TEXT NOT NULL,\n"
        "            pid TEXT NOT NULL, vid TEXT NOT NULL,\n"
        "            PRIMARY KEY (connection_id, canonical_product_id))\"\"\")",
        GATEWAY_SUITE,
    ),
    (
        "a provider sync writes the merchant's retail price",
        VARIANTS,
        "    cost = _coerce_minor(supplier_cost_cents, \"supplier_cost_cents\")\n    now = _now()",
        "    cost = _coerce_minor(supplier_cost_cents, \"supplier_cost_cents\")\n"
        "    if cost is not None:\n"
        "        cur.execute(\"UPDATE marketplace_listings SET price_label=? WHERE id=?\",\n"
        "                    (f\"${cost / 100:.2f}\", int(listing_id)))\n"
        "    now = _now()",
        LEDGER_SUITE,
    ),
    (
        "an unknown supplier cost is stored as zero",
        VARIANTS,
        "        \"supplier_cost_cents\": cost,",
        "        \"supplier_cost_cents\": cost or 0,",
        LEDGER_SUITE,
    ),
    (
        "unverified provider inventory counts as in stock",
        FULFILLMENT,
        "            if not any(w.get(\"state\") == \"IN_STOCK\" and w.get(\"verified\") == 1"
        " and type(w.get(\"total\")) is int and w[\"total\"] >= item[\"quantity\"] for w in warehouses):",
        "            if not any(w.get(\"state\") != \"OUT_OF_STOCK\""
        " and (w.get(\"total\") or 0) >= item[\"quantity\"] for w in warehouses):",
        FULFILLMENT_SUITE,
    ),
]


#: Directories the overlay materialises as real directories rather than symlinks.
#: Every *ancestor* of a mutable directory has to be listed: the walk prunes at the
#: first directory it does not recognise, so omitting "services/business_os" would
#: leave "services/business_os/suppliers" unreachable and every gateway mutation
#: silently unapplied.
REAL_DIRS = {"services", "services/business_os", "services/business_os/suppliers",
             "tests", "tests/marketplace", "tests/dropshipping",
             "scripts", "scripts/marketplace"}


def _overlay(root: str, real_dirs: set[str] | None = None) -> str:
    """A symlink mirror of the repo, deep only where we need to write."""
    real_dirs = REAL_DIRS if real_dirs is None else real_dirs
    for base, dirs, files in os.walk(REPO):
        rel = os.path.relpath(base, REPO)
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        if rel and rel not in real_dirs:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        target = os.path.join(root, rel) if rel else root
        os.makedirs(target, exist_ok=True)
        for name in files:
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
        for name in dirs:
            child = f"{rel}/{name}" if rel else name
            if child in real_dirs:
                continue
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
    return root


def _apply(root: str, rel_path: str, old, new) -> bool:
    """Materialise one mutated copy of ``rel_path``. False if any anchor misses.

    Every edit is checked against the *original* text before any is written, so a
    multi-site mutation is all-or-nothing. A partially applied mutant is the worst
    outcome available here: it would run, probably go red for a reason unrelated
    to the invariant under test, and be reported as CAUGHT.
    """
    edits = old if isinstance(old, list) else [(old, new)]
    source = os.path.join(REPO, rel_path)
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for anchor, _ in edits:
        if text.count(anchor) != 1:
            return False
    for anchor, replacement in edits:
        text = text.replace(anchor, replacement, 1)
    dst = os.path.join(root, rel_path)
    if os.path.islink(dst):
        os.unlink(dst)
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    # The suite builds its own in-memory SQLite. An inherited DATABASE_URL would
    # point services.db at PostgreSQL and every mutation would "fail" for the
    # same irrelevant reason, which reads as a clean sweep and proves nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (label, suite, mutations). Order is presentation only; each group is independent.
#: ``suite`` is the group default; a mutation may override it with a fifth element.
GROUPS: list[tuple[str, str, list[tuple]]] = [
    ("Three-valued availability", SUITE, READ_MUTATIONS),
    ("Unknown cost is not zero", SUITE, MONEY_MUTATIONS),
    ("The variant key", SUITE, KEY_MUTATIONS),
    ("Writes and bounds", SUITE, WRITE_MUTATIONS),
    ("Ownership and the existence oracle", SUITE, OWNERSHIP_MUTATIONS),
    ("Supplier source and field ownership", SUITE, SOURCE_MUTATIONS),
    ("Schema ownership", SUITE, SCHEMA_MUTATIONS),
    ("Ledger reconciliation", LEDGER_SUITE, RECONCILIATION_MUTATIONS),
]


def main() -> int:
    suites = []
    for _, default, mutations in GROUPS:
        for suite in [default] + [m[4] for m in mutations if len(m) > 4]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="mkt_mut_base_"))
    try:
        for suite in suites:
            if _run(base, suite) != 0:
                print(f"  FAIL — {suite} is already red; mutation results "
                      f"judged by it would be noise.")
                return 1
            print(f"  PASS  {suite}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()

    survived: list[str] = []
    unapplied: list[str] = []
    total = 0
    for label, default, mutations in GROUPS:
        targets = {m[4] if len(m) > 4 else default for m in mutations}
        if len(targets) == 1:
            print(f"{label} ({len(mutations)} mutations, judged by {default}):")
        else:
            print(f"{label} ({len(mutations)} mutations, each judged by the suite "
                  f"that owns the boundary it breaks):")
        for mutation in mutations:
            name, rel_path, old, new = mutation[:4]
            suite = mutation[4] if len(mutation) > 4 else default
            note = "" if len(targets) == 1 else f"  [{os.path.basename(suite)}]"
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="mkt_mut_"))
            try:
                if not _apply(root, rel_path, old, new):
                    unapplied.append(f"{label}: {name}")
                    print(f"  ERROR     {name} — anchor not found exactly once")
                    continue
                if _run(root, suite) == 0:
                    survived.append(f"{label}: {name}")
                    print(f"  SURVIVED  {name}{note}")
                else:
                    print(f"  CAUGHT    {name}{note}")
            finally:
                shutil.rmtree(root, ignore_errors=True)
        print()

    print("=" * 68)
    if unapplied:
        print(f"{len(unapplied)} mutation(s) could not be applied "
              f"— a bug in this script, not a pass:")
        for name in unapplied:
            print(f"  - {name}")
    if survived:
        print(f"{len(survived)} mutation(s) SURVIVED — the suite has a hole there:")
        for name in survived:
            print(f"  - {name}")
    if survived or unapplied:
        return 1
    print(f"PASS — all {total} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
