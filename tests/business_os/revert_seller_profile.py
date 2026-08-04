"""Revert validation for ``tests/business_os/test_seller_profile.py``.

A suite that passes tells you nothing on its own — it has to be shown failing for
the right reason. This script reintroduces each defect the brief named, one at a
time, and records which tests notice.

Every mutation below is a *faithful* restoration of the behaviour the screens
actually shipped, not an arbitrary breakage. If a mutation produces 19/19 passes,
the corresponding test is decorative and should be rewritten.

    python3 tests/business_os/revert_seller_profile.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TARGET = os.path.join(ROOT, "services", "business_os", "profile", "service.py")
SUITE = os.path.join(ROOT, "tests", "business_os", "test_seller_profile.py")


#: (name, defect restored, old, new). ``old`` must appear exactly once — an
#: ambiguous anchor would silently mutate the wrong site and the run would look
#: like a passing validation.
MUTATIONS = [
    (
        "badge_first_precedence",
        'the personal blue badge outranking the business track, which is how one '
        'screen printed "in review" and another "Approved" for the same business',
        '    if business_request:\n'
        '        state = normalize_verification_status(business_request.get("status"))',
        '    if _truthy(verified_badge):\n'
        '        return {"state": "approved", "source": "verified_badge", "request_id": 0,\n'
        '                "decided_at": None, "note": None}\n'
        '    if business_request:\n'
        '        state = normalize_verification_status(business_request.get("status"))',
    ),
    (
        "freeze_whole_profile",
        "an approved business having its entire profile locked rather than the two "
        "identity fields a reviewer actually signed off on",
        '            "requires_review": sorted(IDENTITY_SENSITIVE_FIELDS),\n'
        '            "blocked": [],',
        '            "requires_review": [],\n'
        '            "blocked": sorted(WRITABLE_FIELDS),',
    ),
    (
        "all_or_nothing_save",
        "one bad URL discarding the five fields that were correct",
        '            except ProfileError as exc:\n'
        '                rejected[field] = str(exc)\n'
        '                continue',
        '            except ProfileError:\n'
        '                raise',
    ),
    (
        "double_at_handle",
        'the ``@@Pilot-8919`` handle — an "@" prefixed onto a value that already '
        "carried one",
        '    text = re.sub(r"^@+", "", text)\n',
        '',
    ),
    (
        "seller_type_as_category",
        '"Individual" — a reviewer\'s classification of the account — offered to '
        "buyers as though it described what is on sale",
        '    "art_collectibles", "digital_goods", "other",\n)',
        '    "art_collectibles", "digital_goods", "individual", "other",\n)',
    ),
    (
        "hours_unset_reads_as_closed",
        "a brand-new seller who has never set hours appearing permanently shut",
        '            out.append({"weekday": day, "label": WEEKDAY_LABELS[day], "state": "unset",',
        '            out.append({"weekday": day, "label": WEEKDAY_LABELS[day], "state": "closed",',
    ),
    (
        "preview_ignores_visibility",
        "a support phone number the seller had marked private appearing in the "
        "buyer-facing profile",
        '        floor = "after_purchase" if viewer_has_purchased else "public"',
        '        floor = "private"',
    ),
]


def _run():
    """Run the suite and return (passed, failed, failing test names)."""
    proc = subprocess.run(
        [sys.executable, SUITE], cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    out = proc.stdout + proc.stderr
    failed = re.findall(r"^FAIL\s+(\S+)", out, re.MULTILINE)
    passed = len(re.findall(r"^PASS\s+\S+", out, re.MULTILINE))
    # A mutation that stops the module importing at all is not evidence about the
    # tests; it has to be reported as its own outcome rather than counted as 19
    # honest failures.
    if not passed and not failed:
        return 0, 0, ["<suite did not run: %s>" % out.strip().splitlines()[-1][:200]]
    return passed, len(failed), failed


def main() -> int:
    backup = os.path.join(tempfile.mkdtemp(prefix="profile_revert_"), "service.py")
    shutil.copy2(TARGET, backup)
    original = open(TARGET, encoding="utf-8").read()

    print("=" * 78)
    print("BASELINE — unmutated source")
    print("=" * 78)
    passed, failed, names = _run()
    print("  %d passed, %d failed" % (passed, failed))
    if failed or passed == 0:
        print("  Baseline is not green. Fix that before reading anything below.")
        shutil.copy2(backup, TARGET)
        return 1
    baseline = passed

    inert = []
    try:
        for name, defect, old, new in MUTATIONS:
            print()
            print("=" * 78)
            print("MUTATION  %s" % name)
            print("restores  %s" % defect)
            print("=" * 78)
            count = original.count(old)
            if count != 1:
                print("  ANCHOR MATCHED %d TIMES — mutation skipped, not applied." % count)
                inert.append(name + " (anchor)")
                continue
            open(TARGET, "w", encoding="utf-8").write(original.replace(old, new, 1))
            passed, failed, names = _run()
            print("  %d/%d passed, %d failed" % (passed, baseline, failed))
            if failed == 0:
                print("  NO TEST NOTICED. The defect is not covered.")
                inert.append(name)
            else:
                for test in names:
                    print("    caught by  %s" % test)
            shutil.copy2(backup, TARGET)
    finally:
        shutil.copy2(backup, TARGET)

    print()
    print("=" * 78)
    passed, failed, _ = _run()
    print("RESTORED — %d passed, %d failed" % (passed, failed))
    if failed or passed != baseline:
        print("  Restore did not return the file to its original behaviour.")
        return 1
    if inert:
        print("UNCOVERED DEFECTS: %s" % ", ".join(inert))
        return 1
    print("Every mutation was caught. The suite earns its passes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
