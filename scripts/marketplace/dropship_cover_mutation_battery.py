#!/usr/bin/env python3
"""Anti-vacuity battery for "does this product have a picture".

Why this battery exists
-----------------------
One fact, three stores, and readers that disagreed about which one to believe.

* ``listing_metadata_json.media`` — the merchant's ordered list. Import, edit
  and publish all write it, and ``_validate`` reads only this one when it
  decides a draft has media at all.
* ``marketplace_listings.cover_image_url`` — the column every *buyer* surface
  renders, and the one ``bot.py``'s seller listing-update route writes from
  ``marketplace_product_media`` rows, independently of the metadata blob.
* ``marketplace_product_media`` rows with ``is_cover=1`` — the ordinary seller
  submit route's own gate.

The defect that reached production was the second reader disagreeing with the
first: ``drafts.get_draft`` derived the cover from the metadata while
``drafts.list_drafts`` selected ``l.cover_image_url`` raw. Production listing 14
carried five ``cf.cjdropshipping.com`` URLs in its metadata with a NULL column,
so the merchant's Dropshipping list drew a blank tile for a product that opened
with five photos. Nothing was broken, nothing logged, and both readers were
individually "correct".

That is the same shape as every other defect in this subsystem: a value that is
*asserted* by one copy of a derivation rather than measured against the other
copies. There were four independent spellings of ``media[0] if media else None``
in this package, and nothing asserted that any two of them agreed.

So the invariants are:

* the list tile and the detail screen answer the cover question with the same
  function, so they cannot disagree,
* the fallback runs in both directions — metadata-only and column-only are both
  real states, produced by different writers,
* it ends in ``None``, because a chain that always finds *something* reports
  every product as having a picture, which is the same lie as reporting none,
* selecting the metadata blob to answer the question must not ship it: the row
  dict is serialised verbatim by the route, and ``listing_metadata_json`` is a
  merchant-scoped internal store.

Each mutation below is a plausible tidy-up that puts one of those back. A
mutation that survives is reported as a hole in the tests, not as a pass.

Usage::

    .venv/bin/python scripts/marketplace/dropship_cover_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay of
symlinks with the one mutated file materialised as a real copy — the mechanism
is imported from ``supplier_variant_mutation_battery`` rather than restated, so
the batteries cannot drift into different definitions of "applied".
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supplier_variant_mutation_battery import _apply, _overlay  # noqa: E402

#: Each mutation is paired with the suite that is supposed to defend the
#: invariant. Pairing is the stricter claim: a mutation caught only by some
#: unrelated file's incidental breakage is still a hole in the named suite.
PUBLISH_SUITE = "tests/dropshipping/test_dropship_draft_publish.py"
IMPORT_SUITE = "tests/dropshipping/test_dropship_import_pipeline.py"

DRAFTS = "services/business_os/suppliers/drafts.py"
IMPORTER = "services/business_os/suppliers/importer.py"


#: (name, file, old, new[, suite]). ``old`` must appear exactly once, or be a
#: list of (old, new) pairs applied all-or-nothing.
READER_MUTATIONS: list[tuple] = [
    (
        "the products list goes back to reading the column raw",
        DRAFTS,
        "            row[\"cover_image_url\"] = _cover_of(row)",
        "            row[\"cover_image_url\"] = row.get(\"cover_image_url\")",
    ),
    (
        "the detail screen goes back to deriving the cover on its own",
        DRAFTS,
        "        \"cover_image_url\": _cover_of(listing),",
        "        \"cover_image_url\": media[0] if media else None,",
    ),
    (
        "the list stops selecting the metadata, so the fallback has nothing to read",
        DRAFTS,
        "            \"l.cover_image_url, l.listing_metadata_json, l.updated_at, \"",
        "            \"l.cover_image_url, l.updated_at, \"",
    ),
]

FALLBACK_MUTATIONS: list[tuple] = [
    (
        "the fallback becomes one-way: metadata only, so a column-only cover vanishes",
        DRAFTS,
        "    column = listing.get(\"cover_image_url\")\n"
        "    column = column.strip() if isinstance(column, str) else \"\"\n"
        "    return column or None",
        "    return None",
    ),
    (
        # The name this once carried -- "a merchant reorder stops moving the
        # tile" -- was the wrong claim, and the mutation duly survived. Every
        # writer in this package keeps both stores equal, so after a reorder the
        # two orderings of the fallback return the same value and no reorder
        # test can separate them. The case that separates them is an outside
        # writer moving one store only, which is what the pairing now names.
        "the preference inverts, so an outside writer's column overrules the merchant's media",
        DRAFTS,
        "    media = _media_of(listing)\n"
        "    if media:\n"
        "        return media[0]\n",
        "    column_first = listing.get(\"cover_image_url\")\n"
        "    if isinstance(column_first, str) and column_first.strip():\n"
        "        return column_first.strip()\n"
        "    media = _media_of(listing)\n"
        "    if media:\n"
        "        return media[0]\n",
    ),
    (
        "an empty-string column counts as a picture",
        DRAFTS,
        "    return column or None",
        "    return column",
    ),
    (
        # Not "a non-string column is handed to the renderer": the column is
        # TEXT, so the reader is only ever handed a string or NULL, and
        # `return column` for NULL is the same None. That would be a
        # behaviour-preserving edit dressed as a mutation -- it would SURVIVE
        # and prove nothing. Whitespace is the reachable half of the same guard.
        "whitespace passes as a URL, drawing a broken image on a product with none",
        DRAFTS,
        "    column = column.strip() if isinstance(column, str) else \"\"",
        "    column = column if isinstance(column, str) else \"\"",
    ),
    (
        "unparseable metadata stops meaning \"no media\" and raises instead",
        DRAFTS,
        "    except (ValueError, TypeError):\n        return []",
        "    except (ValueError, TypeError):\n        raise",
    ),
    (
        "non-string entries in the media list are taken at face value",
        DRAFTS,
        "    return [m for m in (media or []) if isinstance(m, str)]",
        "    return list(media or [])",
    ),
]

LEAK_MUTATIONS: list[tuple] = [
    (
        "the raw metadata blob rides along on every list row",
        DRAFTS,
        "            row.pop(\"listing_metadata_json\", None)\n",
        "",
    ),
]

WRITER_MUTATIONS: list[tuple] = [
    (
        # Not `cover = _cover_of(listing)`: with media non-empty — which
        # `_validate` has just guaranteed — that is the same value, so it would
        # be a behaviour-preserving edit rather than a mutation, and would
        # SURVIVE while proving nothing. Reconciling with the column is the
        # failure that shape actually produces, so that is what is written here.
        "publish reconciles with the column it is about to overwrite, so NULL stays NULL",
        DRAFTS,
        "        cover = media[0]\n",
        "        cover = listing.get(\"cover_image_url\")\n",
    ),
    (
        "import stops writing the column, leaving the fallback to hide it",
        IMPORTER,
        "        media[0] if media else None,",
        "        None,",
        IMPORT_SUITE,
    ),
]


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    # These suites set their own temp sqlite DATABASE_URL in their module header.
    # An inherited one would be overwritten anyway, but dropping it keeps every
    # battery's environment identical and stops a stray PostgreSQL URL from
    # failing all mutations for the same irrelevant reason — which reads as a
    # clean sweep and proves nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (label, default suite, mutations). Order is presentation only.
GROUPS: list[tuple[str, str, list[tuple]]] = [
    ("Both merchant surfaces ask the same question", PUBLISH_SUITE, READER_MUTATIONS),
    ("The fallback is two-way and ends in None", PUBLISH_SUITE, FALLBACK_MUTATIONS),
    ("Reading the blob is not shipping it", PUBLISH_SUITE, LEAK_MUTATIONS),
    ("Readers fall back; writers do not", PUBLISH_SUITE, WRITER_MUTATIONS),
]


def main() -> int:
    suites: list[str] = []
    for _, default, mutations in GROUPS:
        for suite in [default] + [m[4] for m in mutations if len(m) > 4]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="cover_mut_base_"))
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
        print(f"{label} ({len(mutations)} mutations, judged by {default}):")
        for mutation in mutations:
            name, rel_path, old, new = mutation[:4]
            suite = mutation[4] if len(mutation) > 4 else default
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="cover_mut_"))
            try:
                if not _apply(root, rel_path, old, new):
                    unapplied.append(f"{label}: {name}")
                    print(f"  ERROR     {name} — anchor not found exactly once")
                    continue
                if _run(root, suite) == 0:
                    survived.append(f"{label}: {name}")
                    print(f"  SURVIVED  {name}")
                else:
                    print(f"  CAUGHT    {name}")
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
