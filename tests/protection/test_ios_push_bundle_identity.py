"""One bundle identifier receives VoIP pushes, and it is the one the server addresses.

The decision this pins: development and production builds both ship as
`com.pulsesoc.app`, with only `aps-environment` varying by configuration. There is
no second push-capable App ID.

Why it is worth a gate rather than a comment. A PushKit token is minted *for a
bundle id*. The sender addresses a device with `apns-topic = <bundle>.voip`. If a
build ships under a bundle the server does not address, APNs answers
`DeviceTokenNotForTopic` — and `services/pulsesoc_voip_push.py` classifies that as
`invalid_device` and revokes the token without replaying it, because unlike
`BadDeviceToken` it cannot be a host mismatch. So the two failures are not
comparable:

  * wrong *host*  → one wasted request, then the replay succeeds. Self-healing.
  * wrong *topic* → the row is deleted. That handset never rings again, and no
    amount of reinstalling fixes it until it re-registers.

A second bundle id is therefore not a small change. It is safe only once that App ID
exists with Push enabled *and* it is listed in `APNS_ALLOWED_BUNDLE_IDS`, and the
ordering matters: configuration must precede the build, or the first ring revokes
the token it was meant to reach.

`scripts/install_pulsesoc_native_dev_iphone.sh` is a deliberate, known exception.
It overrides the bundle id so a development build can sit beside the App Store app
instead of overwriting it — that is a real capability, and this file does not forbid
it. What it forbids is doing that *silently*, because a build under an unaddressed
bundle cannot receive a VoIP push at all, and the symptom ("it never rings") is
indistinguishable from the CallKit and Agora bugs someone would go hunting for
first.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PBXPROJ = ROOT / "mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj"
INSTALL_SCRIPT = ROOT / "scripts/install_pulsesoc_native_dev_iphone.sh"

DEPLOYMENT_BUNDLE_ID = "com.pulsesoc.app"


def _app_target_configurations() -> dict[str, str]:
    """The `XCBuildConfiguration` blocks carrying the app's entitlements.

    Matched by `CODE_SIGN_ENTITLEMENTS` rather than by object id, so regenerating
    the project cannot quietly leave this test inspecting nothing.
    """
    blocks: dict[str, str] = {}
    for body, name in re.findall(
        r"buildSettings = \{(.*?)\};\s*name = (\w+);",
        PBXPROJ.read_text(encoding="utf-8"),
        re.DOTALL,
    ):
        if "CODE_SIGN_ENTITLEMENTS" in body:
            blocks[name] = body
    return blocks


class PushBundleIdentityTest(unittest.TestCase):
    def test_the_native_project_ships_one_bundle_identifier(self):
        """MUTATION: point either configuration at a second bundle id.

        `expo prebuild` would do exactly this on its own: `app.config.js` selects
        `com.pulsesoc.nativeapp.dev` for the `development` and
        `development-simulator` EAS profiles. That file is inert only because
        `mobile-native/ios/` is committed and therefore bypasses prebuild — which is
        a property of the repository layout, not a decision anyone re-affirms. If a
        regenerated project ever lands, this is the check that notices.
        """
        blocks = _app_target_configurations()
        self.assertEqual(
            set(blocks), {"Debug", "Release"}, f"Unexpected app configurations: {sorted(blocks)}"
        )
        for name, body in blocks.items():
            found = re.search(r"PRODUCT_BUNDLE_IDENTIFIER = ([\w.]+);", body)
            self.assertIsNotNone(found, f"{name} declares no readable PRODUCT_BUNDLE_IDENTIFIER.")
            self.assertEqual(
                found.group(1),
                DEPLOYMENT_BUNDLE_ID,
                f"{name} builds would register PushKit tokens under "
                f"{found.group(1)!r}, which the sender does not address. Those tokens "
                "are revoked on first use, not retried.",
            )

    def test_the_install_script_declares_the_consequence_it_carries(self):
        """MUTATION: drop whichever warning matches the bundle the script builds.

        Branching rather than skipping, because either choice has a consequence the
        operator cannot see from the command line and would misdiagnose:

        * deployment bundle → the install *replaces* an App Store or TestFlight
          PulseSoc on that device, since iOS matches on bundle id and treats it as an
          upgrade. Silent, and only noticed later.
        * any other bundle → the build cannot receive a VoIP push at all, because its
          token draws `DeviceTokenNotForTopic` and is revoked rather than retried.

        An earlier version skipped the branch it was not on. That is how a guard ends
        up green while guarding nothing, so both branches assert here.
        """
        script = INSTALL_SCRIPT.read_text(encoding="utf-8")
        override = re.search(r'PRODUCT_BUNDLE_IDENTIFIER="\$\{?(\w+)\}?"', script)
        self.assertIsNotNone(
            override, f"{INSTALL_SCRIPT.name} no longer sets PRODUCT_BUNDLE_IDENTIFIER."
        )
        assigned = re.search(rf'{override.group(1)}="([\w.]+)"', script)
        self.assertIsNotNone(assigned, f"{override.group(1)} is never assigned a literal.")
        built = assigned.group(1)

        if built == DEPLOYMENT_BUNDLE_ID:
            self.assertRegex(
                script,
                r"(?i)echo[^\n]*(replaces|overwrit|WARNING)",
                f"{INSTALL_SCRIPT.name} installs {built!r} over any App Store build on "
                "the device without warning that it does so.",
            )
        else:
            self.assertRegex(
                script,
                r"echo[^\n]*VoIP",
                f"{INSTALL_SCRIPT.name} installs {built!r}, which cannot receive a VoIP "
                "push, without saying so. The operator is left to debug CallKit for a "
                "phone that was never addressable.",
            )

    def test_the_display_name_is_defined_rather_than_inherited_empty(self):
        """MUTATION: remove `PULSESOC_DISPLAY_NAME` from either configuration.

        `Info.plist` sets `CFBundleDisplayName` to `$(PULSESOC_DISPLAY_NAME)`, and for
        as long as this repository has had that line no configuration defined the
        setting — so it expanded to the empty string, which is not a build error. iOS
        then falls back to `CFBundleName`, the home screen reads "PulseSoc", and the
        defect is invisible.

        It stopped being cosmetic when the development install script moved onto the
        deployment bundle id: two builds now share an identifier, and the display name
        is the only thing on the device that distinguishes a locally-signed build from
        the App Store one. A silently empty value collapses that distinction back to
        nothing.
        """
        for name, body in _app_target_configurations().items():
            self.assertRegex(
                body,
                r"PULSESOC_DISPLAY_NAME = [^;]+;",
                f"{name} does not define PULSESOC_DISPLAY_NAME, so CFBundleDisplayName "
                "expands to empty and falls back to CFBundleName.",
            )

    def test_the_sender_addresses_the_bundle_the_project_builds(self):
        """Positive control, and the tie that makes the other two mean something.

        Each half is pinned above and in `test_ios_aps_environment_contract.py`, but
        nothing asserted they agree. They are edited in different files by different
        people for different reasons, and the failure when they disagree is silent on
        both sides: the project builds fine, the server sends fine, and the push is
        addressed to a topic no installed build answers to.
        """
        server = (ROOT / "services/pulsesoc_voip_push.py").read_text(encoding="utf-8")
        self.assertIn(
            "APNS_BUNDLE_ID",
            server,
            "The sender no longer derives its topic from APNS_BUNDLE_ID; this contract "
            "is pinned against a mechanism that has moved.",
        )
        for body in _app_target_configurations().values():
            self.assertIn(f"PRODUCT_BUNDLE_IDENTIFIER = {DEPLOYMENT_BUNDLE_ID};", body)


if __name__ == "__main__":
    unittest.main()
