#!/usr/bin/env python3
"""Anti-vacuity battery for the dropshipping import pipeline suites.

A green suite proves nothing on its own. These four went green quickly, which is
exactly when a suite deserves the least trust. This script breaks the pipeline in
a series of specific, plausible ways — each one an edit a reasonable engineer
might make while "simplifying" — and requires the paired suite to go red for
every one of them.

Why these particular mutations
------------------------------
The mission named a small number of distinctions that every cheaper
implementation of this pipeline would lose, and each collapses under a one-line
edit that looks like a cleanup:

* **unknown cost is not zero cost** — ``cost or 0`` prices every unreadable
  product at the markup itself, and ``(retail - cost) / retail`` without the
  ``None`` guard reports a fabricated 100% margin, which is the most attractive
  number in the merchant's list;
* **unknown stock is not out of stock** — a provider that did not answer is not
  a provider that answered "0", and storage readers gate purchasability on the
  difference;
* **a rejected media URL is dropped, not substituted** — a placeholder makes an
  SSRF refusal look like a successful import;
* **variant identity is the provider's id** — without the ``variantKey``
  fallback a 12-variant product imports as one variant, silently;
* **import creates a draft and never publishes** — status and approval are SQL
  literals precisely so that publishing requires a visible diff;
* **the merchant owns the storefront, the supplier owns the economics** — the
  price-edit path must not be able to reach ``cost_cents``, and a provider sync
  must not be able to reach a field the merchant has edited;
* **publish is a gate, not a state change** — and it does not self-approve.

One mutant in here is not hypothetical. ``a merchant price edit blanks the
supplier cost`` is the bug this suite actually found: ``_set_prices`` called
``upsert_variant`` without ``cost_cents``, and that helper writes every column it
is given, so every priced variant lost its cost and reported a 100% margin.

The count is deliberately not written down in this docstring. A number in prose
stops being true the first time somebody adds a mutation and does not notice; the
summary line at the end reports the real one.

A mutation that survives is reported as a hole in the tests, not as a pass. An
anchor that cannot be applied is reported as an error in *this* script — never as
a silent skip, because a mutation that quietly fails to apply is indistinguishable
from one the suite caught.

Usage::

    .venv/bin/python3 scripts/dropshipping/dropship_import_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay
directory of symlinks, with the one mutated file materialised as a real copy.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Each mutation is judged by the suite that owns the boundary it breaks, rather
#: than by whatever happens to go red. Pairing is the stricter claim: a mutant
#: caught only by some unrelated file's incidental breakage is still a hole.
NORM_SUITE = "tests/dropshipping/test_supplier_normalization.py"
PRICE_SUITE = "tests/dropshipping/test_dropship_pricing.py"
IMPORT_SUITE = "tests/dropshipping/test_dropship_import_pipeline.py"
DRAFT_SUITE = "tests/dropshipping/test_dropship_draft_publish.py"

NORMALIZE = "services/business_os/suppliers/normalize.py"
PRICING = "services/business_os/suppliers/pricing.py"
IMPORTER = "services/business_os/suppliers/importer.py"
DRAFTS = "services/business_os/suppliers/drafts.py"


#: (name, file, old, new). ``old`` must appear exactly once in the file.
#: ``old``/``new`` may instead be a list of ``(old, new)`` pairs applied to the
#: same file in one mutant, for simplifications that are genuinely two-site.

MONEY_MUTATIONS = [
    (
        "a negative supplier price is read as a positive cost",
        NORMALIZE,
        '_NUMBER = re.compile(r"-?\\d+(?:\\.\\d+)?")',
        '_NUMBER = re.compile(r"\\d+(?:\\.\\d+)?")',
    ),
    (
        "a negative cost is stored rather than refused",
        NORMALIZE,
        "    if amount < 0:\n        return None\n    if amount > 1_000_000_000:",
        "    if amount > 1_000_000_000:",
    ),
    (
        "an unknown cost is priced from zero",
        PRICING,
        "    if kind == MANUAL_PRICE or cost_cents is None:\n        return None",
        "    if kind == MANUAL_PRICE:\n        return None\n    cost_cents = cost_cents or 0",
    ),
    (
        "an unknown cost yields a fabricated 100% margin",
        PRICING,
        "    if retail_cents is None or cost_cents is None:\n        return None",
        "    if retail_cents is None:\n        return None\n    cost_cents = cost_cents or 0",
    ),
    (
        "margin is a percentage of cost instead of retail",
        PRICING,
        "    retail = int(retail_cents)\n    if retail <= 0:\n        return None",
        "    retail = int(cost_cents) or int(retail_cents)\n    if retail <= 0:\n        return None",
    ),
    (
        "an unknown margin reads as healthy",
        PRICING,
        "    percent = margin_percent(retail_cents, cost_cents)\n    if percent is None:\n        return UNKNOWN",
        "    percent = margin_percent(retail_cents, cost_cents)\n    if percent is None:\n        return HEALTHY",
    ),
]

STOCK_MUTATIONS = [
    (
        "an absent quantity is read as out of stock",
        NORMALIZE,
        "    count = _quantity_or_none(quantity)\n    if count is None:\n        return STOCK_UNKNOWN, None",
        "    count = _quantity_or_none(quantity)\n    if count is None:\n        return STOCK_OUT_OF_STOCK, 0",
    ),
    (
        "a provider outage is indistinguishable from zero stock",
        NORMALIZE,
        "    if provider_failed:\n        return STOCK_PROVIDER_UNAVAILABLE, None",
        "    if provider_failed:\n        return STOCK_OUT_OF_STOCK, 0",
    ),
    (
        "an unrecognised declared state is passed straight through",
        NORMALIZE,
        "        token = str(declared).strip().upper().replace(\"-\", \"_\").replace(\" \", \"_\")\n        if token in STOCK_STATES:",
        "        token = str(declared).strip().upper().replace(\"-\", \"_\").replace(\" \", \"_\")\n        if True:",
    ),
    (
        "the storage collapse sends indeterminate stock to out of stock",
        NORMALIZE,
        "    if state in (STOCK_IN_STOCK, STOCK_OUT_OF_STOCK):\n        return state\n    return STOCK_UNKNOWN",
        "    if state in (STOCK_IN_STOCK, STOCK_OUT_OF_STOCK):\n        return state\n    return STOCK_OUT_OF_STOCK",
    ),
    (
        "the importer stores the provider's raw state, bypassing the collapse",
        IMPORTER,
        "            stock_state=normalize.storage_stock_state(variant.get(\"stock_state\")),",
        "            stock_state=variant.get(\"stock_state\"),",
    ),
]

MEDIA_MUTATIONS = [
    (
        "private and link-local IP literals are accepted as media hosts",
        NORMALIZE,
        "        if (mapped.is_private or mapped.is_loopback or mapped.is_link_local\n"
        "                or mapped.is_reserved or mapped.is_multicast or mapped.is_unspecified):\n"
        "            return None",
        "        if False:\n            return None",
    ),
    (
        "plaintext http media is accepted",
        NORMALIZE,
        "    if parts.scheme != \"https\":\n        return None",
        "    if parts.scheme not in (\"https\", \"http\"):\n        return None",
    ),
    (
        "a rejected media URL is replaced with a placeholder",
        NORMALIZE,
        "        url = safe_media_url(candidate)\n        if url and url not in seen:",
        "        url = safe_media_url(candidate) or \"https://cdn.example.com/placeholder.jpg\"\n"
        "        if url and url not in seen:",
    ),
    (
        "merchant-supplied media is trusted rather than revalidated",
        DRAFTS,
        "        media = normalize.media_list(raw)",
        "        media = [m for m in raw if isinstance(m, str)]",
    ),
]

IDENTITY_MUTATIONS = [
    (
        "the variantKey fallback is dropped, collapsing every variant to one key",
        NORMALIZE,
        "    key = _first(entry, \"variantKey\", \"variantNameEn\", \"variantName\")",
        "    key = None",
    ),
]

IMPORT_MUTATIONS = [
    (
        "import publishes instead of creating a draft",
        IMPORTER,
        [
            ("'draft',?,?,'pending_review',", "'published',?,?,'approved',"),
            ("WHERE seller_user_id=? AND status='draft' ",
             "WHERE seller_user_id=? AND status='published' "),
        ],
        None,
    ),
    (
        "duplicate imports are no longer prevented",
        IMPORTER,
        "    existing = _existing_listing(cur, seller_user_id, provider, connection_id,\n"
        "                                 external_product_id)",
        "    existing = None",
    ),
    (
        "import accepts an economic value from the caller",
        IMPORTER,
        "def import_selected(business_id, store_id, actor_user_id, connection_id, *,",
        "def import_selected(business_id, store_id, actor_user_id, connection_id, *,\n"
        "                    cost_cents=None,",
    ),
    (
        "a failed item is reported to the merchant as imported",
        IMPORTER,
        "            outcome, payload = failure.outcome, ({\"detail\": failure.detail}",
        "            outcome, payload = IMPORTED, ({\"detail\": failure.detail}",
    ),
]

OWNERSHIP_MUTATIONS = [
    (
        "a merchant price edit blanks the supplier cost",
        DRAFTS,
        "        cur.execute(\n"
        "            f\"UPDATE {variants.VARIANT_TABLE} SET price_cents=?, updated_at=? \"\n"
        "            f\"WHERE id=? AND listing_id=? AND seller_user_id=?\",\n"
        "            (value, now, int(variant[\"id\"]), int(listing_id), int(seller_user_id)))",
        "        variants.upsert_variant(\n"
        "            cur, listing_id=listing_id, seller_user_id=seller_user_id,\n"
        "            options=variant.get(\"options\"), sku=variant.get(\"sku\"),\n"
        "            provider_variant_id=variant.get(\"provider_variant_id\"),\n"
        "            price_cents=value, currency=variant.get(\"currency\"),\n"
        "            stock_state=variant.get(\"stock_state\"),\n"
        "            stock_quantity=variant.get(\"stock_quantity\"),\n"
        "            position=variant.get(\"position\") or 0)",
    ),
    (
        "the merchant edit is applied but not recorded, so a sync will revert it",
        DRAFTS,
        "        if touched:\n            variants.mark_overridden(",
        "        if False:\n            variants.mark_overridden(",
    ),
    (
        "the editable-field allowlist is removed, making status merchant-writable",
        DRAFTS,
        "    unknown = set(fields) - EDITABLE\n    if unknown:\n        raise SupplierError(\"unsupported_field\", http_status=400)",
        "    if False:\n        raise SupplierError(\"unsupported_field\", http_status=400)",
    ),
    (
        "a merchant-authored listing can be edited through the dropshipping path",
        DRAFTS,
        "        if variants.source_for(cur, listing_id) is None:\n"
        "            raise SupplierError(\"not_a_supplier_product\", http_status=404)",
        "        if False:\n"
        "            raise SupplierError(\"not_a_supplier_product\", http_status=404)",
    ),
    (
        "the listing owner is not checked, so another merchant's draft is readable",
        DRAFTS,
        "    if row.get(\"seller_user_id\") is None or int(row[\"seller_user_id\"]) != int(seller_user_id):",
        "    if row.get(\"seller_user_id\") is None:",
    ),
]

PUBLISH_MUTATIONS = [
    (
        "publish self-approves, skipping moderation",
        DRAFTS,
        "            \"UPDATE marketplace_listings SET status='published', quantity=?, \"",
        "            \"UPDATE marketplace_listings SET status='published', approval_status='approved', quantity=?, \"",
    ),
    (
        "the publish gate no longer refuses an invalid draft",
        DRAFTS,
        "        if not verdict[\"publishable\"]:\n            raise SupplierError(\"publication_blocked\", http_status=422)",
        "        if False:\n            raise SupplierError(\"publication_blocked\", http_status=422)",
    ),
    (
        "published quantity copies the supplier's warehouse count",
        DRAFTS,
        "        sellable = sum(1 for v in rows if variants.availability(v) == variants.AVAILABLE)",
        "        sellable = sum(int(v.get(\"stock_quantity\") or 0) for v in rows)",
    ),
    (
        "validation reports only the first problem it found",
        DRAFTS,
        "    return {\"publishable\": not problems, \"problems\": problems}",
        "    return {\"publishable\": not problems, \"problems\": problems[:1]}",
    ),
    (
        "an all-unknown inventory no longer blocks publication",
        DRAFTS,
        "    if priced and all(v.get(\"availability\") == variants.UNKNOWN for v in priced):\n"
        "        problems.append(UNKNOWN_INVENTORY)",
        "    if False:\n        problems.append(UNKNOWN_INVENTORY)",
    ),
    (
        "a negative margin no longer blocks publication",
        DRAFTS,
        "    if any(v.get(\"margin_state\") == pricing.NEGATIVE_MARGIN for v in priced):\n"
        "        problems.append(NEGATIVE_MARGIN)",
        "    if False:\n        problems.append(NEGATIVE_MARGIN)",
    ),
    (
        "a disconnected supplier no longer blocks publication",
        DRAFTS,
        "    if sync == supplier_schema.SYNC_DISCONNECTED:\n        problems.append(SUPPLIER_DISCONNECTED)",
        "    if False:\n        problems.append(SUPPLIER_DISCONNECTED)",
    ),
]


#: (label, default suite, mutations). A mutation may override the suite with a
#: fifth element when it breaks a boundary a different file owns.
GROUPS = [
    ("Unknown cost is not zero cost", PRICE_SUITE, MONEY_MUTATIONS),
    ("Unknown stock is not out of stock", NORM_SUITE, STOCK_MUTATIONS),
    ("Media is dropped, never substituted", NORM_SUITE, MEDIA_MUTATIONS),
    ("Variant identity", NORM_SUITE, IDENTITY_MUTATIONS),
    ("Import is authoritative and never publishes", IMPORT_SUITE, IMPORT_MUTATIONS),
    ("Field ownership", DRAFT_SUITE, OWNERSHIP_MUTATIONS),
    ("Publish is a gate", DRAFT_SUITE, PUBLISH_MUTATIONS),
]

#: Mutations whose judging suite is not their group's default, keyed by name.
SUITE_OVERRIDES = {
    "a negative supplier price is read as a positive cost": NORM_SUITE,
    "a negative cost is stored rather than refused": NORM_SUITE,
    "the importer stores the provider's raw state, bypassing the collapse": IMPORT_SUITE,
    "merchant-supplied media is trusted rather than revalidated": DRAFT_SUITE,
}


def _overlay(root: str) -> str:
    """A symlink mirror of the repo, deep only where we need to write.

    Every *ancestor* of a mutable directory has to be listed: the walk prunes at
    the first directory it does not recognise, so omitting ``services/business_os``
    would leave the suppliers package unreachable and every mutation silently
    unapplied.
    """
    real_dirs = {"services", "services/business_os", "services/business_os/suppliers"}
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
    with open(os.path.join(REPO, rel_path), encoding="utf-8") as handle:
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
    # Each dropshipping suite points DATABASE_URL at its own temp SQLite file at
    # import time. An inherited value would win the race and send every mutation
    # at the same irrelevant PostgreSQL failure, which reads as a clean sweep and
    # proves nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


def main() -> int:
    suites = []
    for _, default, mutations in GROUPS:
        for suite in [default] + [SUITE_OVERRIDES.get(m[0], default) for m in mutations]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="drop_mut_base_"))
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

    survived, unapplied, total = [], [], 0
    for label, default, mutations in GROUPS:
        targets = {SUITE_OVERRIDES.get(m[0], default) for m in mutations}
        if len(targets) == 1:
            print(f"{label} ({len(mutations)} mutations, judged by "
                  f"{os.path.basename(default)}):")
        else:
            print(f"{label} ({len(mutations)} mutations, each judged by the suite "
                  f"that owns the boundary it breaks):")
        for name, rel_path, old, new in mutations:
            suite = SUITE_OVERRIDES.get(name, default)
            note = "" if len(targets) == 1 else f"  [{os.path.basename(suite)}]"
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="drop_mut_"))
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
    sys.exit(main())
