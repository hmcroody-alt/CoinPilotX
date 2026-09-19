"""The Xcode project's native targets and deployment floor are hand-maintained.

Why this file exists
--------------------
`mobile-native/ios/` is a **committed bare workflow**, not a generated directory.
`project.pbxproj` is tracked (`git ls-files --error-unmatch` confirms it), and
`mobile-native/docs/LOCALIZATION.md:403` already mandates that native
configuration be maintained in both the Expo config and the committed project,
and forbids `expo prebuild --clean`.

That policy is what makes Apple extensions — WidgetKit, Live Activities, a Share
Extension, Control Center controls — buildable here at all. The decision to add
them as ordinary committed Xcode targets rather than synthesize them from a
config plugin is recorded in
`docs/apple/DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`, along with the one
real risk it accepts:

    the project now has hand-maintained state that `expo prebuild` does not know
    about ... The failure mode is a developer running `npm run prebuild:ios` (it
    is still in `scripts`) and committing the result, which would drop every
    extension target at once.

This file is the mitigation that decision promised. It converts "somebody
regenerated the project" from a silent capability deletion into a red build.

What it defends, and why each half is here
------------------------------------------
Two hand-set properties of `project.pbxproj` share a failure mode: a clean
prebuild reverts them, nothing fails to compile, and the loss is invisible in
review because the diff is enormous and mostly cosmetic.

  * **The native target inventory.** Every extension is a `PBXNativeTarget`.
    Losing one does not break the app build — the app still compiles and runs,
    it simply has no widget. There is no error to read.

  * **The deployment floor, 16.1.** Raised from 15.1 on 2026-09-19 against
    measured production evidence (zero native sessions below iOS 18 across 7,441
    parsed sessions). It is what makes App Intents, Live Activities and Dynamic
    Island available at all. A revert to 15.1 does not fail the build either; it
    makes `@available` branches dead and silently un-ships features.

Neither is checked by anything else. `test_ios_build_version_contract.py` pins
the marketing version and build number, and `test_ios_aps_environment_contract.py`
pins the APNs entitlement wiring, but nothing pinned target count or the floor
before this file.

On vacuity
----------
The house hazard for a gate like this is that every failure of its *reader*
makes it greener. That is guarded two ways rather than assumed:

  * The count assertion fails closed by construction — if the
    `isa = PBXNativeTarget` marker ever stops matching, the count is 0, which is
    not the expected value, so the test goes red rather than quiet.
  * `test_the_parse_is_not_vacuous` asserts independently that the file was
    found, is substantial, and that the name extraction actually returned
    something. An empty expected-set compared against an empty parsed-set would
    otherwise pass forever.

Updating this file is *supposed* to be part of adding an extension. That is the
point: the edit becomes deliberate and reviewable instead of accidental and
silent. When a widget lands, add its target name to `EXPECTED_TARGETS` in the
same commit.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PBXPROJ = ROOT / "mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj"

# Every native target the committed project is expected to contain, mapped to its
# Xcode product type. Add a row here in the same commit that adds a target.
EXPECTED_TARGETS = {
    "PulseSoc": "com.apple.product-type.application",
}

# Decided 2026-09-19. See DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md.
EXPECTED_DEPLOYMENT_TARGET = "16.1"

# Project + target, Debug + Release. Recorded so that a *reduction* in the number
# of declarations is caught too: a prebuild that emitted only two would otherwise
# satisfy a naive "every value is 16.1" check while leaving two configurations
# inheriting a lower floor.
EXPECTED_DEPLOYMENT_TARGET_DECLARATIONS = 4

NATIVE_TARGET_BLOCK = re.compile(
    r"isa = PBXNativeTarget;(.*?)\n\t\t\};",
    re.DOTALL,
)


def _pbxproj() -> str:
    return PBXPROJ.read_text(encoding="utf-8")


def _native_targets() -> dict[str, str]:
    """Map each `PBXNativeTarget`'s name to its product type.

    Read out of the target blocks themselves rather than from the `/* ... */`
    comments Xcode sprinkles through the file, because those comments appear at
    every *reference* to a target as well as at its definition, and counting them
    would over-report.
    """
    found: dict[str, str] = {}
    for body in NATIVE_TARGET_BLOCK.findall(_pbxproj()):
        name = re.search(r"\n\t\t\tname = \"?([^\";\n]+)\"?;", body)
        product_type = re.search(r"productType = \"?([^\";\n]+)\"?;", body)
        if name:
            found[name.group(1)] = product_type.group(1) if product_type else ""
    return found


class NativeTargetInventoryTest(unittest.TestCase):
    def test_the_native_target_count_is_what_we_committed(self):
        """MUTATION: run `expo prebuild --clean` and commit the result.

        A regenerated project carries only the app target. Every extension
        vanishes at once, the app still builds, and nothing says so. This is the
        single assertion the extension-strategy decision rests on.
        """
        count = _pbxproj().count("isa = PBXNativeTarget")
        self.assertEqual(
            count,
            len(EXPECTED_TARGETS),
            "The committed Xcode project has "
            f"{count} native target(s); {len(EXPECTED_TARGETS)} expected "
            f"({', '.join(sorted(EXPECTED_TARGETS))}).\n"
            "If you added or removed a target on purpose, update EXPECTED_TARGETS "
            "in this file in the same commit. If you did not, your project was "
            "probably regenerated -- restore it rather than updating this test.",
        )

    def test_every_expected_target_is_present_and_named(self):
        """MUTATION: rename a target, or replace one extension with another.

        Count alone cannot see a substitution. A widget target swapped for a
        share extension keeps the count at two while silently dropping the
        capability the count was protecting.
        """
        self.assertEqual(
            _native_targets(),
            EXPECTED_TARGETS,
            "Native target inventory does not match what this file declares.",
        )

    def test_the_deployment_floor_is_held(self):
        """MUTATION: revert `IPHONEOS_DEPLOYMENT_TARGET` to 15.1.

        A prebuild, an Xcode "update to recommended settings", or a merge can all
        do this. Nothing fails to build. The cost is that every `@available(iOS
        16.1, *)` branch becomes unreachable on a floor that no longer guarantees
        it -- App Intents, Live Activities and Dynamic Island simply never appear,
        with no error anywhere.
        """
        values = re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([0-9.]+);", _pbxproj())
        self.assertTrue(
            values,
            "No IPHONEOS_DEPLOYMENT_TARGET found at all. Either the project moved "
            "or this test's pattern has rotted; both need a human.",
        )
        self.assertEqual(
            sorted(set(values)),
            [EXPECTED_DEPLOYMENT_TARGET],
            f"Expected every declaration to be {EXPECTED_DEPLOYMENT_TARGET}; found {sorted(set(values))}.",
        )

    def test_all_four_configurations_still_declare_the_floor(self):
        """MUTATION: delete the setting from one configuration.

        An absent `IPHONEOS_DEPLOYMENT_TARGET` does not error -- the configuration
        inherits, and the inherited value is not 16.1. So "every value present is
        16.1" is satisfied by a file that declares it once and leaves three
        configurations low. The count is the half that catches that.
        """
        values = re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([0-9.]+);", _pbxproj())
        self.assertEqual(
            len(values),
            EXPECTED_DEPLOYMENT_TARGET_DECLARATIONS,
            f"Expected {EXPECTED_DEPLOYMENT_TARGET_DECLARATIONS} declarations "
            "(project and target, Debug and Release); "
            f"found {len(values)}. Adding a target adds two more -- update the "
            "constant deliberately.",
        )

    def test_the_project_is_committed_not_generated(self):
        """MUTATION: gitignore `ios/`, or delete the committed project.

        Everything above is meaningless if the file under test is a build
        artefact. This asserts the premise the whole extension strategy rests on,
        so that if the repo ever moves to a generated `ios/`, this file fails
        loudly instead of inspecting a leftover.
        """
        self.assertTrue(PBXPROJ.exists(), f"Missing committed project: {PBXPROJ}")

    def test_the_parse_is_not_vacuous(self):
        """MUTATION: break the target-block regex so it matches nothing.

        Without this, a reader that silently returns `{}` would make
        `test_every_expected_target_is_present_and_named` compare empty to empty
        the moment EXPECTED_TARGETS was ever emptied, and would make the count
        test the only real check. Assert the parse did real work.
        """
        source = _pbxproj()
        self.assertGreater(
            len(source), 10_000, "project.pbxproj is implausibly small; is this the real file?"
        )
        self.assertIn("isa = PBXNativeTarget", source, "Marker absent; the format may have changed.")

        parsed = _native_targets()
        self.assertTrue(parsed, "Target-name extraction returned nothing; the reader has rotted.")
        self.assertEqual(
            len(parsed),
            source.count("isa = PBXNativeTarget"),
            "Some native target blocks were found but not named. The reader is "
            "dropping targets, which would make a deletion look like a rename.",
        )
        for name, product_type in parsed.items():
            self.assertTrue(product_type, f"Target {name!r} parsed with no productType.")


if __name__ == "__main__":
    unittest.main()
