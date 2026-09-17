"""`aps-environment` must come from the build configuration, never from a literal.

The entitlement decides which APNs host will ever know the tokens a build mints.
A literal cannot be right for both configurations at once, and the direction it
was wrong in matters: `development` in a Release build means every App Store and
TestFlight install registers a *sandbox* token, while the server — with
`APNS_USE_SANDBOX` unset — addresses the production host. APNs answers
`BadDeviceToken`, which is indistinguishable from a dead token, and the sender's
one-shot replay is then paying an extra request for every push to every user
forever rather than for a handful of development handsets.

It is also silent. Nothing fails to build, no test goes red, and the symptom on a
real phone ("it rang once and then stopped") reads like a CallKit bug.

So this pins the wiring rather than the value: the entitlements file must
reference the build setting, and both configurations of the app target must
define it, with Release on `production` and Debug on `development`. A local
device build is Release *and* development-signed, which is a real combination
this does not forbid — it is expressed by overriding the setting on the xcodebuild
command line, which is what `scripts/install_pulsesoc_native_dev_iphone.sh` does.
"""

import plistlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTITLEMENTS = ROOT / "mobile-native/ios/PulseSoc/PulseSoc.entitlements"
PBXPROJ = ROOT / "mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj"
INSTALL_SCRIPT = ROOT / "scripts/install_pulsesoc_native_dev_iphone.sh"

SETTING = "PULSESOC_APS_ENVIRONMENT"


def _pbxproj() -> str:
    return PBXPROJ.read_text(encoding="utf-8")


def _app_target_configurations() -> dict[str, str]:
    """The two `XCBuildConfiguration` blocks that carry the app's entitlements.

    Matched by `CODE_SIGN_ENTITLEMENTS` rather than by object id, so renaming or
    regenerating the project does not quietly make this test inspect nothing.
    """
    blocks: dict[str, str] = {}
    for body, name in re.findall(
        r"buildSettings = \{(.*?)\};\s*name = (\w+);", _pbxproj(), re.DOTALL
    ):
        if "CODE_SIGN_ENTITLEMENTS" in body:
            blocks[name] = body
    return blocks


class ApsEnvironmentContractTest(unittest.TestCase):
    def test_the_entitlement_is_not_a_literal(self):
        """MUTATION: hardcode `development` (or `production`) back into the plist.

        A literal is wrong for one of the two configurations no matter which one is
        chosen, and it cannot be overridden per build.
        """
        value = plistlib.loads(ENTITLEMENTS.read_bytes())["aps-environment"]
        self.assertEqual(
            value,
            f"$({SETTING})",
            "aps-environment must expand a build setting, not name an environment. "
            f"Found {value!r}.",
        )

    def test_both_app_configurations_declare_the_setting(self):
        """MUTATION: define the setting in one configuration only.

        An undefined build setting expands to the empty string rather than failing,
        so a missing declaration ships an *empty* aps-environment entitlement. That
        is not a build error — it is a build whose PushKit registration never
        produces a token, i.e. a phone that simply never rings.
        """
        blocks = _app_target_configurations()
        self.assertEqual(
            set(blocks), {"Debug", "Release"}, f"Unexpected app configurations: {sorted(blocks)}"
        )
        for name, body in blocks.items():
            self.assertIn(
                SETTING, body, f"{name} does not declare {SETTING}; it would expand to empty."
            )

    def test_release_requests_production_and_debug_requests_development(self):
        """MUTATION: set Release to `development`.

        This is the defect this file was written for, and it is the direction that
        costs every store user rather than every developer.
        """
        expected = {"Debug": "development", "Release": "production"}
        for name, body in _app_target_configurations().items():
            found = re.search(rf"{SETTING} = (\w+);", body)
            self.assertIsNotNone(found, f"{name} declares {SETTING} in an unreadable form.")
            self.assertEqual(
                found.group(1),
                expected[name],
                f"{name} builds would request aps-environment={found.group(1)!r}.",
            )

    def test_the_development_install_script_overrides_the_setting(self):
        """MUTATION: drop the override from the local device install script.

        That script builds `-configuration Release` and signs with an Apple
        Development identity. Without the override it would inherit `production`,
        which a development provisioning profile does not grant, so codesign fails.
        The override is the only reason a development-signed Release build is still
        possible, and it was dead code until the entitlement started reading it.
        """
        script = INSTALL_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("-configuration Release", script)
        self.assertRegex(
            script,
            rf"{SETTING}=development\b",
            f"{INSTALL_SCRIPT.name} builds Release without overriding {SETTING}.",
        )

    def test_the_install_script_targets_the_project_that_exists(self):
        """MUTATION: restore the `PulseSocNative` workspace and scheme names.

        The script referenced a workspace and scheme that are not in the repository,
        so it failed before reaching xcodebuild — which is how its
        `PULSESOC_APS_ENVIRONMENT` argument stayed dangling and unnoticed for as
        long as it did.
        """
        script = INSTALL_SCRIPT.read_text(encoding="utf-8")
        workspace = re.search(r"-workspace (\S+)", script)
        scheme = re.search(r"-scheme (\S+)", script)
        self.assertIsNotNone(workspace)
        self.assertIsNotNone(scheme)
        self.assertTrue(
            (ROOT / "mobile-native" / workspace.group(1)).exists(),
            f"No such workspace: {workspace.group(1)}",
        )
        self.assertIn(
            f"/{scheme.group(1)}.xcodeproj/", str(PBXPROJ), f"No such scheme: {scheme.group(1)}"
        )


if __name__ == "__main__":
    unittest.main()
