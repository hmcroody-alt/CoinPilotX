"""app.json and the checked-in Xcode project must agree on the iOS version.

Why this exists
---------------

`mobile-native/ios/` is a checked-in bare-workflow Xcode project. That makes
`app.json` advisory for the native build: CFBundleVersion is resolved from
`CURRENT_PROJECT_VERSION` in `project.pbxproj`, and `app.json`'s `ios.buildNumber`
only reaches the binary if someone runs `expo prebuild`. Nobody runs `expo
prebuild` here, and nobody should - the project carries native customisations
(`modules/pulse-now-playing/`, and the patch-package patch that stops the camera
from reconfiguring the shared AVAudioSession) that a regenerated project drops.

So the two numbers are independent, and nothing made them agree.

Commit 3757dbfb ("bump buildNumber to 14 so the landed live-audio viewer fix
ships") changed only `app.json`. `CURRENT_PROJECT_VERSION` stayed 13 in all four
build configurations, so the build produced CFBundleVersion 13 and the device
reported `PulseSoc com.pulsesoc.app 1.0.1 13`. The commit was green, the diff
looked correct, and the bump did nothing. Two costs follow: an App Store upload
is rejected as a duplicate of the existing build 13, and - the reason the bump
existed at all - the live-audio fix that needs a native build cannot be told
apart from the build that lacks it, because both report the same version.

A version bump is the one change whose whole purpose is to be observable from
outside the build. Failing silently defeats it entirely.

This suite is static: it reads two files and compares strings. It cannot catch a
build made from a dirty tree, so it is a drift guard, not a substitute for
reading CFBundleVersion out of the built app before an upload.
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JSON = ROOT / "mobile-native/app.json"
PBXPROJ = ROOT / "mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj"

CURRENT_PROJECT_VERSION = re.compile(r"^\s*CURRENT_PROJECT_VERSION = ([^;]+);", re.M)
MARKETING_VERSION = re.compile(r"^\s*MARKETING_VERSION = ([^;]+);", re.M)


def _expo():
    return json.loads(APP_JSON.read_text(encoding="utf-8"))["expo"]


def _pbxproj():
    return PBXPROJ.read_text(encoding="utf-8")


def test_native_ios_project_is_checked_in():
    # The premise of this suite. If ios/ ever stops being checked in, app.json
    # becomes authoritative again and these comparisons are meaningless.
    assert PBXPROJ.is_file(), (
        f"{PBXPROJ.relative_to(ROOT)} is missing. If the iOS project is no longer "
        "checked in, delete this suite; if it moved, update the path."
    )


def test_build_number_matches_current_project_version():
    declared = _expo()["ios"]["buildNumber"]
    found = CURRENT_PROJECT_VERSION.findall(_pbxproj())
    assert found, "No CURRENT_PROJECT_VERSION in project.pbxproj."
    mismatched = sorted({value.strip() for value in found} - {declared})
    assert not mismatched, (
        f"app.json ios.buildNumber is {declared!r} but project.pbxproj still has "
        f"CURRENT_PROJECT_VERSION {', '.join(repr(v) for v in mismatched)}. "
        "CFBundleVersion comes from the pbxproj, so the bump would not ship. "
        "Edit CURRENT_PROJECT_VERSION in every build configuration - do not run "
        "`expo prebuild`, it discards this project's native customisations."
    )


def test_marketing_version_matches_app_json_version():
    declared = _expo()["version"]
    found = MARKETING_VERSION.findall(_pbxproj())
    assert found, "No MARKETING_VERSION in project.pbxproj."
    mismatched = sorted({value.strip() for value in found} - {declared})
    assert not mismatched, (
        f"app.json version is {declared!r} but project.pbxproj still has "
        f"MARKETING_VERSION {', '.join(repr(v) for v in mismatched)}. "
        "CFBundleShortVersionString comes from the pbxproj."
    )


def test_every_build_configuration_carries_the_version():
    # A bump applied to the app target but not the test target (or vice versa)
    # still drifts; the failure is just slower to find. Both regexes are expected
    # to match once per XCBuildConfiguration that sets them, and the count of
    # each must line up so no configuration is silently missing one.
    text = _pbxproj()
    build_numbers = CURRENT_PROJECT_VERSION.findall(text)
    marketing = MARKETING_VERSION.findall(text)
    assert len(build_numbers) == len(marketing), (
        f"{len(build_numbers)} CURRENT_PROJECT_VERSION entries but "
        f"{len(marketing)} MARKETING_VERSION entries. A build configuration is "
        "missing one of the two, which is how a partial bump hides."
    )
    assert len(set(v.strip() for v in build_numbers)) == 1, (
        "Build configurations disagree on CURRENT_PROJECT_VERSION: "
        f"{sorted(set(v.strip() for v in build_numbers))}"
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
