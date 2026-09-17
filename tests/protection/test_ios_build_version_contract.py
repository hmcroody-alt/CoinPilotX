"""app.json, the Xcode project, and Info.plist must agree on the iOS version.

Why this exists
---------------

`mobile-native/ios/` is a checked-in bare-workflow Xcode project, so `app.json`
is advisory for the native build: `ios.buildNumber` only reaches the binary if
someone runs `expo prebuild`. Nobody runs `expo prebuild` here, and nobody
should - the project carries native customisations (`modules/pulse-now-playing/`,
and the patch-package patch that stops the camera from reconfiguring the shared
AVAudioSession) that a regenerated project drops.

Where CFBundleVersion actually comes from
-----------------------------------------

An earlier version of this file asserted that "CFBundleVersion is resolved from
`CURRENT_PROJECT_VERSION` in `project.pbxproj`". That is wrong, and the error was
not harmless - see below. `CURRENT_PROJECT_VERSION` only reaches the binary by
one of two routes:

  1. `GENERATE_INFOPLIST_FILE = YES`, which makes Xcode synthesise the plist; or
  2. the plist spelling `CFBundleVersion` as `$(CURRENT_PROJECT_VERSION)`.

This target does neither. `project.pbxproj` sets `INFOPLIST_FILE =
PulseSoc/Info.plist` with no `GENERATE_INFOPLIST_FILE`, and that plist holds a
literal. So **the plist is authoritative and `CURRENT_PROJECT_VERSION` is inert.**
(Substitution does work in this plist - `CFBundleName` is `$(PRODUCT_NAME)` - it
is simply not used for the version, which is why the mistake is easy to make by
reading the pbxproj alone.)

The two ways this has already failed
------------------------------------

`3757dbfb` ("bump buildNumber to 14 so the landed live-audio viewer fix ships")
changed only `app.json`; the build shipped 13. That is the drift this suite was
written for, and the first three tests still guard it.

`76084cc2` ("sync embedded CFBundleVersion to 20") is the failure the old premise
could not see: the pbxproj said 20 in both configurations while the plist still
carried a literal 17, and a local Release build produced a CFBundleVersion 17
binary. The suite was green throughout, because it never opened the plist.

It then happened a third time, which is why this file was rewritten: a bump to 23
landed in `app.json` and `project.pbxproj`, the suite passed, and EAS built
**build 22** - a duplicate of the build already sitting in App Store Connect.

A version bump is the one change whose entire purpose is to be observable from
outside the build. A green contract test that certifies a bump which will not
ship is worse than no test, because it converts a question into a false answer.

Scope
-----

This suite is static: it reads three files and compares strings. It cannot catch
a build made from a dirty tree, so it is a drift guard, not a substitute for
reading CFBundleVersion out of the built `.app` before an upload.
"""

import json
import pathlib
import plistlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JSON = ROOT / "mobile-native/app.json"
PBXPROJ = ROOT / "mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj"
INFO_PLIST = ROOT / "mobile-native/ios/PulseSoc/Info.plist"

CURRENT_PROJECT_VERSION = re.compile(r"^\s*CURRENT_PROJECT_VERSION = ([^;]+);", re.M)
MARKETING_VERSION = re.compile(r"^\s*MARKETING_VERSION = ([^;]+);", re.M)
GENERATE_INFOPLIST = re.compile(r"^\s*GENERATE_INFOPLIST_FILE = ([^;]+);", re.M)
INFOPLIST_FILE = re.compile(r"^\s*INFOPLIST_FILE = ([^;]+);", re.M)

# `$(FOO)` and `${FOO}` are both accepted by Xcode.
SUBSTITUTION = re.compile(r"^\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]$")


def _expo():
    return json.loads(APP_JSON.read_text(encoding="utf-8"))["expo"]


def _pbxproj():
    return PBXPROJ.read_text(encoding="utf-8")


def _plist():
    with INFO_PLIST.open("rb") as handle:
        return plistlib.load(handle)


def _unique(pattern, text, label):
    found = {value.strip() for value in pattern.findall(text)}
    assert found, f"No {label} in project.pbxproj."
    assert len(found) == 1, f"Build configurations disagree on {label}: {sorted(found)}"
    return found.pop()


def _resolve(raw, setting, label):
    """Resolve a plist value that may be a literal or a build-setting reference.

    Spelling the plist value `$(CURRENT_PROJECT_VERSION)` is the *better* shape -
    it deletes this whole class of drift by leaving one place to edit. So this
    resolves it rather than rejecting it: whichever shape the plist uses, the
    value that reaches the binary is what gets compared.
    """
    match = SUBSTITUTION.match(raw.strip())
    if not match:
        return raw.strip()
    referenced = match.group(1)
    assert referenced == setting, (
        f"Info.plist {label} expands {raw!r}, but this suite only knows how to "
        f"resolve $({setting}). Teach _resolve about {referenced} or compare it "
        "directly."
    )
    return _unique(
        CURRENT_PROJECT_VERSION if setting == "CURRENT_PROJECT_VERSION" else MARKETING_VERSION,
        _pbxproj(),
        setting,
    )


def test_native_ios_project_is_checked_in():
    # The premise of this suite. If ios/ ever stops being checked in, app.json
    # becomes authoritative again and these comparisons are meaningless.
    assert PBXPROJ.is_file(), (
        f"{PBXPROJ.relative_to(ROOT)} is missing. If the iOS project is no longer "
        "checked in, delete this suite; if it moved, update the path."
    )
    assert INFO_PLIST.is_file(), (
        f"{INFO_PLIST.relative_to(ROOT)} is missing. It is the file that decides "
        "CFBundleVersion; if the target moved to a generated plist, see "
        "test_the_plist_is_the_authoritative_source."
    )


def test_the_plist_is_the_authoritative_source():
    """Pins *why* the plist is checked at all, so the model cannot invert silently.

    If someone sets `GENERATE_INFOPLIST_FILE = YES`, Xcode synthesises the plist
    from build settings and the checked-in literal stops mattering - at which
    point the tests below would be comparing a file that no longer reaches the
    binary, and the suite would go back to certifying bumps that do not ship.
    That is the exact failure this rewrite exists to end, so the assumption is
    pinned rather than left implicit.
    """
    text = _pbxproj()
    generate = {value.strip() for value in GENERATE_INFOPLIST.findall(text)}
    assert generate <= {"NO"}, (
        f"GENERATE_INFOPLIST_FILE is {sorted(generate)}. With YES, Xcode "
        "synthesises Info.plist from CURRENT_PROJECT_VERSION/MARKETING_VERSION "
        "and the checked-in plist no longer decides the version. Rewrite this "
        "suite to treat the pbxproj as authoritative before flipping that."
    )
    referenced = {value.strip() for value in INFOPLIST_FILE.findall(text)}
    assert referenced == {"PulseSoc/Info.plist"}, (
        f"INFOPLIST_FILE is {sorted(referenced)}, not just 'PulseSoc/Info.plist'. "
        "A second plist means a second place the version can drift; add it to "
        "this suite."
    )


def test_plist_build_number_matches_app_json():
    """The one that would have caught the build-22 upload.

    This is the assertion with teeth: the plist literal is what EAS and Xcode put
    in the binary, so if it disagrees with app.json the bump does not ship, and
    App Store Connect rejects the upload as a duplicate of the previous build.
    """
    declared = _expo()["ios"]["buildNumber"]
    shipped = _resolve(str(_plist()["CFBundleVersion"]), "CURRENT_PROJECT_VERSION", "CFBundleVersion")
    assert shipped == declared, (
        f"app.json ios.buildNumber is {declared!r} but Info.plist ships "
        f"CFBundleVersion {shipped!r}. The plist is what reaches the binary "
        "(INFOPLIST_FILE, no GENERATE_INFOPLIST_FILE), so the bump would not "
        "ship and the upload would be rejected as a duplicate. Edit "
        "mobile-native/ios/PulseSoc/Info.plist - do not run `expo prebuild`, it "
        "discards this project's native customisations."
    )


def test_plist_marketing_version_matches_app_json():
    declared = _expo()["version"]
    shipped = _resolve(
        str(_plist()["CFBundleShortVersionString"]), "MARKETING_VERSION", "CFBundleShortVersionString"
    )
    assert shipped == declared, (
        f"app.json version is {declared!r} but Info.plist ships "
        f"CFBundleShortVersionString {shipped!r}."
    )


def test_build_number_matches_current_project_version():
    declared = _expo()["ios"]["buildNumber"]
    found = CURRENT_PROJECT_VERSION.findall(_pbxproj())
    assert found, "No CURRENT_PROJECT_VERSION in project.pbxproj."
    mismatched = sorted({value.strip() for value in found} - {declared})
    assert not mismatched, (
        f"app.json ios.buildNumber is {declared!r} but project.pbxproj still has "
        f"CURRENT_PROJECT_VERSION {', '.join(repr(v) for v in mismatched)}. "
        "This setting does not currently reach the binary - Info.plist does - but "
        "it is what Xcode's UI shows and what the next person will read, so a "
        "stale value here is a trap. Keep all three files in step."
    )


def test_marketing_version_matches_app_json_version():
    declared = _expo()["version"]
    found = MARKETING_VERSION.findall(_pbxproj())
    assert found, "No MARKETING_VERSION in project.pbxproj."
    mismatched = sorted({value.strip() for value in found} - {declared})
    assert not mismatched, (
        f"app.json version is {declared!r} but project.pbxproj still has "
        f"MARKETING_VERSION {', '.join(repr(v) for v in mismatched)}."
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


def test_the_comparison_is_not_vacuous():
    """Positive control.

    Every assertion above is of the form "these strings are equal". If a parse
    silently returned None on both sides - a renamed plist key, an app.json
    restructure - the comparisons would still hold and the suite would pass
    against any build number at all. Pin that each side really produced a
    version-shaped value.
    """
    declared = _expo()["ios"]["buildNumber"]
    plist = _plist()
    assert str(declared).strip().isdigit(), f"app.json ios.buildNumber is not numeric: {declared!r}"
    assert str(plist["CFBundleVersion"]).strip(), "Info.plist CFBundleVersion is empty."
    assert str(plist["CFBundleShortVersionString"]).strip(), (
        "Info.plist CFBundleShortVersionString is empty."
    )
    assert CURRENT_PROJECT_VERSION.findall(_pbxproj()), "CURRENT_PROJECT_VERSION scan found nothing."


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
