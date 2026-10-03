"""§28: break the publication control six ways and check the suite notices.

A green suite is not evidence. These tests were written against code that already
worked, which is the condition under which a test that asserts nothing looks
exactly like a test that asserts everything. So each mutation below is a specific
way the hold could be wrong -- not a syntax error, a *plausible* wrong -- applied
to the real source file, with the suite run against it and the source restored.

A mutation that no test catches is reported as a hole, named, so it can be closed
rather than discovered later by a buyer.
"""
import os
import shutil
import subprocess
import sys

REPO = "/Users/hmcherie/Desktop/cpx-catalog"
PY = "/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python"
SUITE = "tests/dropshipping/test_dropship_publication_hold.py"

TARGETS = {
    "publication": os.path.join(REPO, "services/business_os/suppliers/publication.py"),
    "lifecycle": os.path.join(REPO, "services/marketplace_listing_lifecycle.py"),
    "audit": os.path.join(REPO, "services/business_os/suppliers/audit.py"),
    "importer": os.path.join(REPO, "services/business_os/suppliers/importer.py"),
    "drafts": os.path.join(REPO, "services/business_os/suppliers/drafts.py"),
}

#: (name, target, what it breaks, old, new)
MUTATIONS = [
    (
        "hold_writes_released",
        "publication",
        "hold() sets the control to 1 instead of 0 -- a hold that publishes",
        "value=HELD, action=audit.PUBLICATION_HELD, reason=reason)",
        "value=RELEASED, action=audit.PUBLICATION_HELD, reason=reason)",
    ),
    (
        "release_skips_the_gate",
        "publication",
        "release() stops consulting the publish gate, so an unbound product "
        "-- listing 35's exact shape -- can be made buyable",
        'if not verdict.get("publishable"):',
        'if False:',
    ),
    (
        "release_also_publishes",
        "publication",
        "release() promotes a draft to published, making the control a second "
        "publisher competing with _publish_core",
        '"UPDATE marketplace_listings SET commerce_publication_enabled=? WHERE id=?",\n        (int(value), listing_id))',
        '"UPDATE marketplace_listings SET commerce_publication_enabled=?, "\n        "status=\'published\' WHERE id=?",\n        (int(value), listing_id))',
    ),
    (
        "ownership_check_dropped",
        "publication",
        "the scoped read stops checking the owner, so any merchant can hold "
        "any other merchant's product",
        "listing_id, row = drafts._owned_listing(cur, listing_id, seller_user_id)",
        'cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1",\n                (int(listing_id),))\n    row = dict(cur.fetchone())\n    listing_id = int(row["id"])',
    ),
    (
        "idempotent_write_audits",
        "publication",
        "a no-op hold writes a trail row anyway, so the timeline fills with "
        "retries and a real decision becomes unfindable",
        "    if current == value:",
        "    if False:",
    ),
    (
        "import_never_holds",
        "importer",
        "the import stops closing the latch, so an unbindable product is one "
        "moderator approval and one sync tick from being on sale -- the exact "
        "way the 152 were made",
        "    if binding is None:\n        # No orderable variant",
        "    if False:\n        # No orderable variant",
    ),
    (
        "import_holds_everything",
        "importer",
        "the import holds every product, including ones the merchant bound "
        "explicitly -- a blanket brake that overrules their own import",
        "    if binding is None:\n        # No orderable variant",
        "    if True:\n        # No orderable variant",
    ),
    (
        "import_releases_instead_of_holding",
        "publication",
        "hold_at_creation grants instead of withholding, turning the one "
        "writer that may only veto into one that publishes",
        '        "UPDATE marketplace_listings SET commerce_publication_enabled=? WHERE id=?",\n        (HELD, int(listing_id)))',
        '        "UPDATE marketplace_listings SET commerce_publication_enabled=? WHERE id=?",\n        (RELEASED, int(listing_id)))',
    ),
    (
        "publish_holds_instead_of_releasing",
        "drafts",
        "pressing Publish writes a hold, so a merchant who answers the variant "
        "question gets a published row no buyer can see",
        "         lifecycle.PUBLICATION_RELEASED, _iso(),",
        "         lifecycle.PUBLICATION_HELD, _iso(),",
    ),
    (
        "publish_leaves_the_hold_in_place",
        "drafts",
        "Publish stops writing the control at all, so the at-creation hold "
        "survives the decision that was supposed to answer it",
        '"commerce_publication_enabled=?, updated_at=? "',
        '"commerce_publication_enabled='
        'COALESCE(commerce_publication_enabled,?), updated_at=? "',
    ),
    (
        "audit_payload_unfiltered",
        "audit",
        "the publication payload bypasses the allowlist, so any key a caller "
        "attaches is disclosed into the merchant-facing timeline (§27)",
        "        before=_facts(_ALLOWED_PUBLICATION, before),\n        after=_facts(_ALLOWED_PUBLICATION, after),",
        "        before=dict(before or {}),\n        after=dict(after or {}),",
    ),
]


def run_suite():
    proc = subprocess.run(
        [PY, "-m", "pytest", SUITE, "-q", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True)
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else "(no output)"
    failed = [ln.split("::")[-1] for ln in proc.stdout.splitlines()
              if ln.startswith("FAILED")]
    return proc.returncode, summary, failed


def main():
    backups = {}
    for key, path in TARGETS.items():
        backups[key] = path + ".mutbak"
        shutil.copy2(path, backups[key])

    print("=" * 74)
    code, summary, _ = run_suite()
    print("BASELINE (unmutated): %s" % summary)
    if code != 0:
        print("ABORT: the suite is not green before mutation; nothing below "
              "would mean anything.")
        for key, path in TARGETS.items():
            shutil.move(backups[key], path)
        return 1

    caught, holes, skipped = [], [], []
    for name, target, description, old, new in MUTATIONS:
        path = TARGETS[target]
        source = open(path).read()
        if source.count(old) != 1:
            # Reported, never silently skipped: a mutation that no longer applies
            # is a mutation harness that has quietly stopped testing anything,
            # and it looks identical to a passing one from the summary line.
            skipped.append((name, "PATTERN MISSING (%d matches)" % source.count(old)))
            print("\n--- %-26s SKIPPED: pattern not found" % name)
            continue
        open(path, "w").write(source.replace(old, new, 1))
        try:
            code, summary, failed = run_suite()
        finally:
            shutil.copy2(backups[target], path)

        print("\n--- %s" % name)
        print("    breaks : %s" % description)
        print("    result : %s" % summary)
        if code != 0:
            caught.append((name, failed))
            print("    CAUGHT by: %s" % ", ".join(failed[:4]))
        else:
            holes.append((name, description))
            print("    *** HOLE: the suite passed with this break in place ***")

    for key, path in TARGETS.items():
        shutil.move(backups[key], path)

    print("\n" + "=" * 74)
    print("caught %d/%d" % (len(caught), len(MUTATIONS)))
    for name, reason in skipped:
        print("  SKIPPED %s -- %s" % (name, reason))
    for name, description in holes:
        print("  HOLE    %s -- %s" % (name, description))

    # Restoration is verified rather than assumed: a harness that leaves a
    # mutation in the tree is worse than no harness.
    code, summary, _ = run_suite()
    print("RESTORED: %s" % summary)
    return 0 if (not holes and not skipped and code == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
