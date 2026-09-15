#!/usr/bin/env python3
"""Anti-vacuity proof for the Phase 1 Private Office invariants.

Every assertion added or repointed in Phase 1 claims to protect something. This
script removes the protection and demands the assertion notice. A test that
stays green under its own mutation is not protecting anything, and the whole
point of the retirement work was that a *silently* green check is worse than no
check — it occupies the slot a real check would have filled.

Safety, because a previous harness in this repo wrote the real repo:

* Files are backed up as raw bytes to a temp dir and restored unconditionally in
  a ``finally``, then verified by SHA-256 against the backup.
* Only files named in ``MUTATIONS`` are ever opened for writing.
* Every mutation is an exact string replacement that must match exactly once;
  a miss aborts before anything is written, so a stale mutation cannot silently
  test nothing (which would be the same vacuity failure one level up).
* Nothing here touches git.

Run: .venv/bin/python scripts/private_office_phase1_mutations.py
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

ACCESS = "services/private_office/access.py"
MATRIX = "services/private_office/feature_matrix.py"
STATUS = "services/private_office/status.py"
OFFICE = "services/private_office/office.py"
TELEMETRY = "services/private_office/telemetry.py"
MODEL = "services/private_office/model.py"
PO_ROUTES = "services/private_office_routes.py"

OWNER_SUITE = "tests/private_office/test_owner_office_membership.py"
TIER_SUITE = "tests/private_office/test_tier_resolver.py"
ROUTE_SUITE = "tests/private_office/test_private_office_routes.py"
SURFACE_SUITE = "tests/private_office/test_private_office_surface.py"
OBSERV_SUITE = "tests/private_office/test_private_observability.py"

#: The single pytest function each file-style suite exposes. These suites are
#: one test that runs many ``check(...)`` calls, so the mutation harness can
#: only ask "did the file notice", never "which check noticed" — which is fine,
#: because a check that nothing reaches is exactly what it is hunting.
ROUTE_TEST = "test_private_office_member_routes"
SURFACE_TEST = "test_private_office_surface"
OBSERV_TEST = "test_observability_suite"

#: The two halves of the pricing invariant, named here because they are each
#: other's anti-vacuity guard and a mutation that trips only one of them is
#: reporting something real about which half is unprotected.
RETIRED_ROWS_TEST = \
    "test_retired_rows_keep_their_matrix_state_and_are_refused_at_the_surface"
PRICED_TEST = "test_a_live_feature_below_its_tier_is_priced_rather_than_hidden"


class Mutation:
    def __init__(self, name, rationale, edits, expect_fail):
        self.name = name
        self.rationale = rationale
        self.edits = edits          # [(relpath, old, new)]
        self.expect_fail = expect_fail  # [(suite, test_name_substring)]


MUTATIONS = [
    Mutation(
        "retirement_verdict_removed",
        "decide() stops recognising retirement at all, so a withdrawn feature "
        "falls through to the ordinary tier ladder. This is the failure the "
        "whole overlay exists to prevent: the row still says IMPLEMENTED, so a "
        "retired feature would read ENTITLED to anyone holding the tier.",
        [(ACCESS,
          '    if _matrix.is_retired(feature_id):',
          '    if False and _matrix.is_retired(feature_id):')],
        [(OWNER_SUITE, "test_16b_a_retired_feature_offers_the_owner_no_upgrade_path"),
         (OWNER_SUITE, "test_16c_retirement_beats_the_top_tier_and_the_resolver_both"),
         (TIER_SUITE, "test_unbuilt_features_are_never_entitled_at_any_tier"),
         (TIER_SUITE, RETIRED_ROWS_TEST)],
    ),
    Mutation(
        "retirement_loses_to_a_degraded_resolver",
        "Retirement is checked AFTER the resolver gate instead of before it. "
        "The verdict is still implemented, so the obvious tests stay green — "
        "only the degraded-resolver case flips, and it flips to UNAVAILABLE, "
        "which means 'try again'. Retrying will not bring the feature back, so "
        "this is a client stuck in a retry loop against a decision.",
        [(ACCESS,
          '    if _matrix.is_retired(feature_id):',
          '    if (resolved or {}).get("resolver_state") != _tiers.RESOLVER_OK:\n'
          '        record["decision"] = UNAVAILABLE\n'
          '        return record\n\n'
          '    if _matrix.is_retired(feature_id):')],
        [(OWNER_SUITE, "test_16c_retirement_beats_the_top_tier_and_the_resolver_both")],
    ),
    Mutation(
        "retired_feature_carries_a_price",
        "RETIRED keeps the row's minimum_tier instead of dropping it. Nothing "
        "is granted, so every access assertion still holds — but the surface "
        "now has a tier name to render, and the member is shown an upgrade "
        "button for a capability no amount of money restores.",
        [(ACCESS,
          '        record["implementation"] = spec.implementation if spec else ""\n'
          '        # No minimum_tier: nothing to sell.\n'
          '        return record',
          '        record["implementation"] = spec.implementation if spec else ""\n'
          '        record["minimum_tier"] = spec.minimum_tier if spec else ""\n'
          '        return record')],
        [(OWNER_SUITE, "test_16b_a_retired_feature_offers_the_owner_no_upgrade_path"),
         (TIER_SUITE, "test_unbuilt_features_are_never_entitled_at_any_tier"),
         (TIER_SUITE, RETIRED_ROWS_TEST)],
    ),
    Mutation(
        "an_upgradeable_refusal_loses_its_price_too",
        "The mirror image of the mutation above, and the one the retirement "
        "work makes easy to introduce: NOT_ENTITLED is added to the list of "
        "verdicts whose minimum_tier is stripped. Every 'nothing withdrawn "
        "carries a price' assertion in the suite gets *stronger*, nothing is "
        "granted that should not be, and no refusal changes — a member below "
        "the line is simply never told what to buy, and the only signal is a "
        "revenue number nobody attributes to a diff. Stripping the price is "
        "safe exactly once, for verdicts money cannot fix.",
        [(ACCESS,
          '    if record["decision"] in (NOT_IMPLEMENTED, FEATURE_DISABLED):',
          '    if record["decision"] in (NOT_IMPLEMENTED, FEATURE_DISABLED,\n'
          '                              NOT_ENTITLED):')],
        [(TIER_SUITE, PRICED_TEST)],
    ),
    Mutation(
        "provider_required_becomes_entitled",
        "is_entitled() stops consulting the implementation column, so a feature "
        "with no provider behind it reads ENTITLED at the top tier. This is the "
        "exact state status.py's provider_required_never_entitled probe claims "
        "to watch for — and the state it silently stopped watching for when it "
        "was pinned to a feature that later retired.",
        [(MATRIX,
          '    if spec.implementation in (IMPL_NOT_IMPLEMENTED, IMPL_PROVIDER_REQUIRED):\n'
          '        avail = AVAIL_NOT_IMPLEMENTED',
          '    if spec.implementation in (IMPL_NOT_IMPLEMENTED,):\n'
          '        avail = AVAIL_NOT_IMPLEMENTED')],
        [(TIER_SUITE, "test_provider_required_probe_has_a_subject")],
    ),
    Mutation(
        "provider_probe_loses_its_last_subject",
        "Every remaining PROVIDER_REQUIRED row is retired, which is what would "
        "happen naturally if the last one were withdrawn. The health probe goes "
        "green-forever because `all` over an empty set is True. CI must be the "
        "thing that notices, because production reporting healthy is exactly "
        "what the vacuous state looks like from the outside.",
        [(MATRIX,
          'def is_retired(feature_id: str) -> bool:',
          'def is_retired(feature_id: str) -> bool:\n'
          '    spec = FEATURES.get(feature_id)\n'
          '    if spec is not None and spec.implementation == IMPL_PROVIDER_REQUIRED:\n'
          '        return True')],
        [(TIER_SUITE, "test_provider_required_probe_has_a_subject")],
    ),
    Mutation(
        "status_stops_naming_retirements",
        "The census drops retired_feature_ids. by_implementation still counts "
        "retired rows as IMPLEMENTED — their engines are still there — so an "
        "operator diffing it against live_feature_ids sees an unexplained gap "
        "and no way to tell a deliberate retirement from a regression.",
        [(STATUS,
          '        "retired_feature_ids": sorted(_fm.RETIRED_FEATURE_IDS),',
          '')],
        [(TIER_SUITE, "test_status_surface_names_retirements_beside_the_census")],
    ),
    Mutation(
        "second_lock_opens_for_everyone",
        "_office_lock_gate stops refusing. The tier gate is untouched, so the "
        "owner still resolves PRIVATE_OFFICE and every entitlement assertion "
        "stays green — only the second lock is gone. This is the mission's "
        "named invariant, and the reason the owner suite had to be repointed "
        "off a retired path: a retired route answers 410 before the lock is "
        "ever consulted, so these five would have passed with the lock removed.",
        [(PO_ROUTES,
          'def _office_lock_gate(user):',
          'def _office_lock_gate(user):\n    return None')],
        [(OWNER_SUITE, "test_08_an_owner_with_no_passcode_is_locked_out_of_the_data"),
         (OWNER_SUITE, "test_09_an_owner_with_a_passcode_but_no_grant_stays_out"),
         (OWNER_SUITE, "test_12_a_locked_refusal_carries_no_office_data"),
         (ROUTE_SUITE, ROUTE_TEST)],
    ),
    Mutation(
        "overview_lock_gated_on_a_capability_instead_of_the_room",
        "The landing route's lock check is repointed off the room onto a "
        "capability that happens to sit inside it. This is not a hypothetical: "
        "it gated on `private_facts` until that feature retired, and the only "
        "reason the lock kept running is that `is_entitled` is not "
        "retirement-aware and went on answering True. Point it at a row that "
        "answers False and the gate stops being consulted — no error, no log, "
        "no failing assertion anywhere else. The landing screen simply serves "
        "an entitled member their Office without ever asking for the passcode, "
        "which is the mission's named invariant inverted.",
        [(PO_ROUTES,
          '    entitled = trustworthy and po_matrix.is_entitled(\n'
          '        OFFICE_FEATURE_ID, resolved.get("effective_tier")\n'
          '    )',
          '    entitled = trustworthy and po_matrix.is_entitled(\n'
          '        "private_shield.breach_monitoring", resolved.get("effective_tier")\n'
          '    )')],
        [(ROUTE_SUITE, ROUTE_TEST)],
    ),
    Mutation(
        "a_blocker_money_cannot_move_is_offered_as_an_upgrade",
        "_child_state stops giving PROVIDER_REQUIRED its own word, so it falls "
        "through to UPGRADE_REQUIRED. Nothing is granted and no gate opens — "
        "every access assertion in the suite stays green. What changes is only "
        "what the member is *told*: the entry starts advertising an upgrade "
        "tier, and a member who pays it finds the capability exactly as absent "
        "as before, because no provider has been connected. Taking money for a "
        "state money does not change is the failure, and it is invisible from "
        "the entitlement side.",
        [(OFFICE,
          '    elif implementation == _fm.IMPL_PROVIDER_REQUIRED:\n'
          '        reason = "PROVIDER_REQUIRED"\n',
          '')],
        [(SURFACE_SUITE, SURFACE_TEST)],
    ),
    Mutation(
        "retired_route_comes_back",
        "A handler is re-registered at the Private Facts path. Nothing else "
        "changes, so the retirement overlay, the capability registry and the "
        "census all still report it gone — only the HTTP surface disagrees "
        "with them, which is the one place a member could still reach it.",
        [(PO_ROUTES,
          'def register(app) -> None:',
          'def register(app) -> None:\n'
          '    @app.route("/api/private-office/facts", methods=["GET"])\n'
          '    def _resurrected_facts():\n'
          '        return {"ok": True}, 200\n')],
        [(OWNER_SUITE, "test_16d_a_retired_path_is_gone_rather_than_forbidden"),
         (ROUTE_SUITE, ROUTE_TEST)],
    ),
    Mutation(
        "telemetry_enriches_its_metrics_with_whatever_it_was_handed",
        "sanitize() keeps the undeclared fields instead of dropping them — the "
        "single most plausible edit in this file, because it is what somebody "
        "does to make a dashboard readable. Nothing fails: the declared fields "
        "are still correct, the counts still count, and the extra keys look "
        "like helpful context. What rides in on them is a meeting title, a "
        "fact value, a bank name — member data, in the log stream, at whatever "
        "retention the log stream has.\n\n"
        "        Listed last because it is also the proof that the "
        "observability suite is running at all. That suite crashed partway "
        "through this exact stage for long enough that the entire leak "
        "inspection below the crash had stopped executing; if this mutation "
        "survives, the stage is dark again.",
        [(TELEMETRY,
          '        else:\n'
          '            out[name] = _coerce_enum(raw, vocab)\n'
          '    return out',
          '        else:\n'
          '            out[name] = _coerce_enum(raw, vocab)\n'
          '    for _k, _v in supplied.items():\n'
          '        if _k not in out:\n'
          '            out[_k] = _v\n'
          '    return out')],
        [(OBSERV_SUITE, OBSERV_TEST)],
    ),

    # -----------------------------------------------------------------------
    # Phase 2. The vocabulary round-trip, ported from
    # `claude/nostalgic-neumann-d9391f` because main had no equivalent. These
    # three mutations are the reason it was worth porting rather than trusting:
    # a check whose subject is "every constant equals itself" is exactly the
    # shape that passes forever without touching anything.
    # -----------------------------------------------------------------------
    Mutation(
        "a_vocabulary_member_cannot_match_itself",
        "One constant is spelled in lower case. `_canonical` upper-cases the "
        "input before comparing, so the member can never equal itself and "
        "`normalize_relation('DESCRIBES')` returns None for a relation the "
        "module itself declares legal. Every caller treats None as "
        "'unrecognised, skip it' — correct for a typo from outside, silent "
        "data loss for one of our own constants. Nothing raises, nothing logs, "
        "the column simply arrives empty for every row that uses it. This is "
        "the bug the branch reported shipping to production.",
        [(MODEL,
          'RELATION_DESCRIBES = "DESCRIBES"',
          'RELATION_DESCRIBES = "describes"')],
        [(SURFACE_SUITE, SURFACE_TEST)],
    ),
    Mutation(
        "a_new_vocabulary_arrives_with_no_normalizer",
        "A closed vocabulary is added to model.py and no normalizer is written "
        "for it — the ordinary way a vocabulary gets added under time "
        "pressure. Round-tripping the nine that already have normalizers still "
        "passes, so only the auto-discovery half notices. Without that half "
        "this stage would protect exactly the vocabularies that existed the "
        "day it was written, which for a module that has gained 295 lines of "
        "them since the merge-base is close to protecting nothing.",
        [(MODEL,
          'RELATION_TYPES: tuple[str, ...] = (',
          'ESCROW_STATES: tuple[str, ...] = ("PENDING", "RELEASED")\n\n'
          'RELATION_TYPES: tuple[str, ...] = (')],
        [(SURFACE_SUITE, SURFACE_TEST)],
    ),
    Mutation(
        "a_vocabulary_gains_a_duplicate_member",
        "One member is repeated in its tuple. The normalizer still matches it, "
        "so the round-trip check alone stays green — the duplicate only shows "
        "up where the tuple is *iterated* rather than searched, which is how "
        "the domain summary and the rank tables are built. A repeated member "
        "double-counts there, quietly, in a number the member reads as fact.",
        [(MODEL,
          '    RELATION_GOVERNED_BY,\n'
          '    RELATION_DESCRIBES,\n'
          ')',
          '    RELATION_GOVERNED_BY,\n'
          '    RELATION_DESCRIBES,\n'
          '    RELATION_OWNS,\n'
          ')')],
        [(SURFACE_SUITE, SURFACE_TEST)],
    ),
]


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(suite: str, test: str) -> bool:
    """True when the named test PASSES."""
    proc = subprocess.run(
        [".venv/bin/python", "-m", "pytest", f"{suite}::{test}", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0


def main() -> int:
    touched = sorted({rel for m in MUTATIONS for rel, _, _ in m.edits})
    backup_dir = pathlib.Path(tempfile.mkdtemp(prefix="po-mutation-backup-"))
    originals = {}
    for rel in touched:
        src = ROOT / rel
        data = src.read_bytes()
        originals[rel] = data
        (backup_dir / rel.replace("/", "__")).write_bytes(data)

    print(f"backed up {len(touched)} files to {backup_dir}\n")

    caught = 0
    total = 0
    failures = []
    restore_failed = []
    try:
        for mut in MUTATIONS:
            # Verify every edit applies exactly once BEFORE writing anything.
            staged = {}
            for rel, old, new in mut.edits:
                text = originals[rel].decode()
                n = text.count(old)
                if n != 1:
                    print(f"  !! {mut.name}: anchor matched {n}x in {rel} "
                          f"— mutation not applied, cannot prove anything")
                    failures.append(f"{mut.name}: stale anchor in {rel}")
                    staged = None
                    break
                staged[rel] = text.replace(old, new, 1)
            if staged is None:
                continue

            for rel, text in staged.items():
                (ROOT / rel).write_text(text)

            print(f"[{mut.name}]")
            for suite, test in mut.expect_fail:
                total += 1
                passed = _run(suite, test)
                if passed:
                    print(f"  SURVIVED  {test}  <-- VACUOUS")
                    failures.append(f"{mut.name} survived by {test}")
                else:
                    caught += 1
                    print(f"  caught    {test}")

            for rel in staged:
                (ROOT / rel).write_bytes(originals[rel])
            print()
    finally:
        for rel, data in originals.items():
            (ROOT / rel).write_bytes(data)
        restore_failed = [rel for rel in originals
                          if _sha(ROOT / rel)
                          != hashlib.sha256(originals[rel]).hexdigest()]
        if restore_failed:
            print(f"!! RESTORE FAILED for {restore_failed}; "
                  f"originals are in {backup_dir}")
        else:
            print(f"restored {len(originals)} files, sha-verified")

    if restore_failed:
        return 2

    print(f"\nMUTATIONS: {caught}/{total} CAUGHT")
    for f in failures:
        print(f"  - {f}")
    return 0 if caught == total and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
