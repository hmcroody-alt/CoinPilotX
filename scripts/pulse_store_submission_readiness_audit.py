#!/usr/bin/env python3
"""Audit iOS store-submission readiness for the app we actually build.

Scope
-----

This checks that the shipping iOS app (`mobile-native/`) and its App Store
submission metadata (`mobile-native/store.config.json`) agree with each other and
with the native Xcode project. It is a static drift guard over submission
*inputs*; it cannot tell you whether a build uploads cleanly.

What this file used to read, and why that was worthless
-------------------------------------------------------

Every source was `mobile/pulse-react-native/**` - the legacy Expo 51 app, frozen
since 2026-06-30 and explicitly off-limits per CLAUDE.md. The shipping app is
`mobile-native/`. Both trees declare `com.pulsesoc.app` and both `eas.json`
files name ascAppId 6777591572, so the reads looked plausible while certifying an
artifact nobody builds. The audit exited 0 and handed out a green "ready to
submit" for the wrong tree.

The trap: `app.json` is advisory here
-------------------------------------

`mobile-native/ios/` is a **checked-in bare-workflow Xcode project**. Keys in
`app.json` reach the binary only via `expo prebuild`, which nobody runs here and
nobody should - the project carries native customisations
(`modules/pulse-now-playing/`, the patch-package patch that stops the camera from
reconfiguring the shared AVAudioSession) that a regenerated project drops. So a
naive repoint from `mobile/pulse-react-native/app.json` to
`mobile-native/app.json` reproduces exactly the bug this audit had: reading a
file that does not decide the answer.

That failure is not hypothetical. `34f1d7d3` had to fix a build-number bump that
landed in `app.json` and `project.pbxproj`, passed a fully green contract suite,
and still shipped the previous build - because `Info.plist` holds the literal.
`app.config.js` makes it worse than merely inert: it *rewrites*
`ios.bundleIdentifier` at config-resolution time, so `app.json`'s value is not
even the Expo-resolved one.

So each check below reads the file that decides the value:

  * bundle id, device family, display name, app-icon set, entitlements path
    -> `ios/PulseSoc.xcodeproj/project.pbxproj` build settings
  * URL scheme, usage descriptions, encryption declaration, launch storyboard,
    background modes -> `ios/PulseSoc/Info.plist`
  * associated domains -> `ios/PulseSoc/PulseSoc.entitlements`
  * notifications actually linked -> `ios/Podfile.lock`
  * EAS project id, build profiles, ascAppId -> `app.json` `extra.eas` and
    `eas.json`, which are read by the EAS CLI and are genuinely authoritative
    for it

Versions are deliberately absent: `tests/protection/test_ios_build_version_contract.py`
already owns `CFBundleVersion`/`CFBundleShortVersionString` against the plist.
Repointing a build-number check here would duplicate it worse. The one version
assertion kept is store.config's `apple.version`, which that suite does not read.

`TARGETED_DEVICE_FAMILY` on its own belongs to
`scripts/pulse_app_store_review_fix_audit.py`. What is checked here instead is
the submission consequence: an iPhone-only binary must not ship iPad screenshot
slots.

Checks dropped because their subject does not exist
---------------------------------------------------

  * **All Android checks** (`android.package`, both `googleServicesFile` keys,
    Android app links, `POST_NOTIFICATIONS`, the Play service-account
    `.gitignore`). `mobile-native/` has no `android/` project, no `android`
    section in `eas.json`, and no `google-services.json`. There is no Android
    artifact to be ready for, so these checks could only ever have asserted
    something about a file nobody builds from. The package-name question they
    imply is a live human decision - see the note at the bottom of this file.
  * **Firebase config files.** Neither `GoogleService-Info.plist` nor
    `google-services.json` exists anywhere in `mobile-native/`, and no
    `@react-native-firebase` package is installed. Push is APNs/expo-notifications
    from the server side. A check demanding a Firebase file would fail forever
    against a correct tree.
  * **`assets/splash.png` and `assets/notification-icon.png`.** The native app
    launches from `SplashScreen.storyboard` + the `SplashScreenLogo` image set,
    which is what `UILaunchStoryboardName` names. The splash *subject* survives,
    so the check is repointed rather than deleted; the two PNG paths are gone.
  * **The five `store-metadata/*.md` files.** They exist only in the legacy tree.
    The submission-metadata subject survives as `store.config.json`, so those
    token checks are repointed at it rather than deleted.
"""

import argparse
import json
import plistlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = "mobile-native"
PBXPROJ = f"{NATIVE}/ios/PulseSoc.xcodeproj/project.pbxproj"
INFO_PLIST = f"{NATIVE}/ios/PulseSoc/Info.plist"
ENTITLEMENTS = f"{NATIVE}/ios/PulseSoc/PulseSoc.entitlements"
STORE_CONFIG = f"{NATIVE}/store.config.json"

# `$(FOO)` and `${FOO}` are both accepted by Xcode.
SUBSTITUTION = re.compile(r"^\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]$")

REQUIRED_FILES = [
    PBXPROJ,
    INFO_PLIST,
    ENTITLEMENTS,
    STORE_CONFIG,
    f"{NATIVE}/eas.json",
    f"{NATIVE}/app.json",
    f"{NATIVE}/package.json",
    f"{NATIVE}/ios/Podfile.lock",
    f"{NATIVE}/ios/PulseSoc/SplashScreen.storyboard",
    f"{NATIVE}/ios/PulseSoc/Images.xcassets/AppIcon.appiconset/App-Icon-1024x1024@1x.png",
    f"{NATIVE}/assets/icon.png",
]

EXPECTED_BUNDLE_ID = "com.pulsesoc.app"
EXPECTED_SCHEME = "pulsesoc"
EXPECTED_ASC_APP_ID = "6777591572"
EXPECTED_EAS_PROJECT_ID = "03be39d7-db88-43af-af5f-50c267d830f8"
EXPECTED_DISPLAY_NAME = "PulseSoc"
IPAD_SCREENSHOT_KEYS = ("APP_IPAD",)


class Sources:
    """Every file this audit reads, loaded once.

    Held as mutable state on purpose: `--self-test` perturbs these values
    in-process to prove each check discriminates. Nothing here writes to disk.
    """

    def __init__(self, root=ROOT):
        self.root = root
        self.missing = [path for path in REQUIRED_FILES if not (root / path).is_file()]
        self.pbxproj = self._text(PBXPROJ)
        self.podfile_lock = self._text(f"{NATIVE}/ios/Podfile.lock")
        self.info_plist = self._plist(INFO_PLIST)
        self.entitlements = self._plist(ENTITLEMENTS)
        self.app_json = self._json(f"{NATIVE}/app.json").get("expo") or {}
        self.eas = self._json(f"{NATIVE}/eas.json")
        self.store_config = self._json(STORE_CONFIG)
        self.package_json = self._json(f"{NATIVE}/package.json")
        self.appicon_files = self._appicon_files()
        self.screenshot_files = self._screenshot_files()

    def _text(self, path):
        target = self.root / path
        return target.read_text(encoding="utf-8") if target.is_file() else ""

    def _json(self, path):
        raw = self._text(path)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _plist(self, path):
        target = self.root / path
        if not target.is_file():
            return {}
        try:
            with target.open("rb") as handle:
                return plistlib.load(handle)
        except Exception:
            return {}

    def _appicon_files(self):
        appicon = self.root / NATIVE / "ios/PulseSoc/Images.xcassets/AppIcon.appiconset"
        return sorted(p.name for p in appicon.glob("*.png")) if appicon.is_dir() else []

    def _screenshot_files(self):
        """Which store.config screenshot paths actually exist on disk."""
        present = {}
        for locale in (self.store_config.get("apple", {}).get("info") or {}).values():
            for paths in (locale.get("screenshots") or {}).values():
                for path in paths:
                    present[path] = (self.root / NATIVE / path).is_file()
        return present

    def setting(self, name):
        """Distinct values of an Xcode build setting across all configurations.

        Returned as a set so a check can tell "Debug and Release disagree" apart
        from "the setting is missing" - a value set in only one configuration is
        how a half-applied change hides.
        """
        found = re.findall(rf"^\s*{re.escape(name)} = ([^;]+);", self.pbxproj, re.M)
        return {value.strip().strip('"') for value in found}

    def resolve_plist(self, key):
        """Read an Info.plist value, expanding a `$(BUILD_SETTING)` reference.

        `CFBundleDisplayName` is `$(PULSESOC_DISPLAY_NAME)`, so reading the plist
        alone returns the literal string `$(PULSESOC_DISPLAY_NAME)` and any
        comparison against it silently fails or silently passes depending on
        which side you wrote. Expand it instead.
        """
        raw = self.info_plist.get(key)
        if not isinstance(raw, str):
            return raw
        match = SUBSTITUTION.match(raw.strip())
        if not match:
            return raw.strip()
        values = self.setting(match.group(1))
        if len(values) != 1:
            return sorted(values) or None
        return values.pop()

    def url_schemes(self):
        schemes = set()
        for entry in self.info_plist.get("CFBundleURLTypes") or []:
            schemes.update(entry.get("CFBundleURLSchemes") or [])
        return schemes

    def dependencies(self):
        deps = dict(self.package_json.get("dependencies") or {})
        deps.update(self.package_json.get("devDependencies") or {})
        return deps

    def apple_info(self, locale="en-US"):
        return ((self.store_config.get("apple") or {}).get("info") or {}).get(locale) or {}


def _unique(sources, setting, expected, label, failures):
    """Assert a build setting resolves to exactly one value, and that it matches."""
    values = sources.setting(setting)
    if not values:
        failures.append(f"{label}: {setting} is not set in {PBXPROJ}.")
        return
    if len(values) > 1:
        failures.append(
            f"{label}: build configurations disagree on {setting}: {sorted(values)}. "
            "A value set in only one configuration ships from only one of them."
        )
        return
    actual = values.pop()
    if actual != expected:
        failures.append(f"{label}: {setting} is {actual!r}, expected {expected!r}.")


# --- checks -----------------------------------------------------------------
#
# Each takes (sources, failures) and appends human-readable gaps. Registered in
# CHECKS below with the mutation that proves it can fail.


def check_required_files(sources, failures):
    for path in sources.missing:
        failures.append(f"Missing required file: {path}")


def check_bundle_identifier(sources, failures):
    _unique(sources, "PRODUCT_BUNDLE_IDENTIFIER", EXPECTED_BUNDLE_ID, "ios bundle id", failures)


def check_display_name(sources, failures):
    name = sources.resolve_plist("CFBundleDisplayName")
    if name != EXPECTED_DISPLAY_NAME:
        failures.append(
            f"app display name: Info.plist CFBundleDisplayName resolves to {name!r}, "
            f"expected {EXPECTED_DISPLAY_NAME!r}."
        )


def check_url_scheme(sources, failures):
    schemes = sources.url_schemes()
    if EXPECTED_SCHEME not in schemes:
        failures.append(
            f"scheme: Info.plist CFBundleURLTypes declares {sorted(schemes) or 'nothing'}, "
            f"missing {EXPECTED_SCHEME!r}. This is the deep-link scheme the binary "
            "registers; app.json's `scheme` does not reach it without prebuild."
        )


def check_app_icon(sources, failures):
    _unique(sources, "ASSETCATALOG_COMPILER_APPICON_NAME", "AppIcon", "icon configured", failures)
    if not sources.appicon_files:
        failures.append(
            "icon configured: AppIcon.appiconset contains no PNG. "
            "ASSETCATALOG_COMPILER_APPICON_NAME names a set that must not be empty."
        )


def check_launch_screen(sources, failures):
    storyboard = sources.info_plist.get("UILaunchStoryboardName")
    if storyboard != "SplashScreen":
        failures.append(
            f"splash configured: Info.plist UILaunchStoryboardName is {storyboard!r}, "
            "expected 'SplashScreen'. The native app launches from a storyboard, not "
            "from an app.json splash image."
        )
        return
    if f"{storyboard}.storyboard in Resources" not in sources.pbxproj:
        failures.append(
            f"splash configured: {storyboard}.storyboard is named by "
            "UILaunchStoryboardName but is not in the target's Resources build "
            "phase, so it would not be copied into the bundle."
        )


def check_usage_descriptions(sources, failures):
    for key in (
        "NSCameraUsageDescription",
        "NSMicrophoneUsageDescription",
        "NSPhotoLibraryUsageDescription",
    ):
        value = sources.info_plist.get(key)
        if not (isinstance(value, str) and value.strip()):
            failures.append(f"ios permissions: Info.plist {key} is missing or empty.")


def check_encryption_declaration(sources, failures):
    declared = sources.info_plist.get("ITSAppUsesNonExemptEncryption")
    if declared is not False:
        failures.append(
            f"ios encryption declaration: Info.plist ITSAppUsesNonExemptEncryption is "
            f"{declared!r}, expected False. Anything else makes App Store Connect ask "
            "for export-compliance documentation on every upload."
        )


def check_associated_domains(sources, failures):
    _unique(
        sources,
        "CODE_SIGN_ENTITLEMENTS",
        "PulseSoc/PulseSoc.entitlements",
        "ios associated domains",
        failures,
    )
    domains = sources.entitlements.get("com.apple.developer.associated-domains") or []
    if "applinks:pulsesoc.com" not in domains:
        failures.append(
            f"ios associated domains: entitlements declare {list(domains) or 'nothing'}, "
            "missing 'applinks:pulsesoc.com'. Universal links are entitlement-gated; "
            "app.json's associatedDomains is advisory here."
        )


def check_notifications_linked(sources, failures):
    if "expo-notifications" not in sources.dependencies():
        failures.append("notifications: expo-notifications is not a declared dependency.")
    if "EXNotifications" not in sources.podfile_lock:
        failures.append(
            "notifications: the EXNotifications pod is absent from Podfile.lock, so "
            "notifications are not linked into the native target. app.json's plugins "
            "list only takes effect through prebuild."
        )
    modes = sources.info_plist.get("UIBackgroundModes") or []
    if "remote-notification" not in modes:
        failures.append(
            f"notifications: Info.plist UIBackgroundModes is {list(modes)}, missing "
            "'remote-notification'."
        )


def check_eas_project_id(sources, failures):
    actual = ((sources.app_json.get("extra") or {}).get("eas") or {}).get("projectId")
    if actual != EXPECTED_EAS_PROJECT_ID:
        failures.append(
            f"eas project id: app.json extra.eas.projectId is {actual!r}, expected "
            f"{EXPECTED_EAS_PROJECT_ID!r}."
        )


def check_eas_production_profile(sources, failures):
    profiles = sources.eas.get("build") or {}
    production = profiles.get("production")
    if production is None:
        failures.append(
            f"eas production build: no 'production' profile in eas.json (found "
            f"{sorted(profiles)})."
        )
        return
    if production.get("distribution") != "store":
        failures.append(
            f"eas production build: production profile distribution is "
            f"{production.get('distribution')!r}, expected 'store'. Only a store "
            "distribution produces an App Store-submittable archive."
        )


def check_eas_channel_needs_expo_updates(sources, failures):
    profiles = sources.eas.get("build") or {}
    channelled = sorted(name for name, p in profiles.items() if "channel" in (p or {}))
    if channelled and "expo-updates" not in sources.dependencies():
        failures.append(
            f"eas channel requires expo updates: profiles {channelled} declare an "
            "update `channel`, but expo-updates is not installed. The channel is "
            "inert - those builds cannot receive an update - and EAS warns on every "
            "build. Either install expo-updates or drop the channel keys."
        )


def check_asc_app_id(sources, failures):
    actual = (
        ((sources.eas.get("submit") or {}).get("production") or {}).get("ios") or {}
    ).get("ascAppId")
    if actual != EXPECTED_ASC_APP_ID:
        failures.append(
            f"eas apple app id: eas.json submit.production.ios.ascAppId is {actual!r}, "
            f"expected {EXPECTED_ASC_APP_ID!r}."
        )


def check_store_metadata_urls(sources, failures):
    info = sources.apple_info()
    for key in ("privacyPolicyUrl", "supportUrl", "title", "description", "promoText"):
        value = info.get(key)
        if not (isinstance(value, str) and value.strip()):
            failures.append(f"store metadata: store.config.json apple.info.en-US.{key} is empty.")
    review = (sources.store_config.get("apple") or {}).get("review") or {}
    if not (isinstance(review.get("notes"), str) and review["notes"].strip()):
        failures.append("store metadata: apple.review.notes is empty; App Review needs them.")
    if review.get("demoRequired") and not review.get("demoUsername"):
        failures.append(
            "store metadata: apple.review.demoRequired is true but demoUsername is empty."
        )


def check_store_metadata_version(sources, failures):
    declared = (sources.store_config.get("apple") or {}).get("version")
    shipped = sources.resolve_plist("CFBundleShortVersionString")
    if declared != shipped:
        failures.append(
            f"store metadata version: store.config.json apple.version is {declared!r} "
            f"but Info.plist ships CFBundleShortVersionString {shipped!r}. The config "
            "would target a different App Store version record than the binary."
        )


def check_screenshots_exist(sources, failures):
    if not sources.screenshot_files:
        failures.append("screenshots: store.config.json declares no screenshots.")
        return
    for path, exists in sorted(sources.screenshot_files.items()):
        if not exists:
            failures.append(f"screenshots: store.config.json references a missing file: {path}")


def check_screenshots_match_device_family(sources, failures):
    """An iPhone-only binary must not ship iPad screenshot slots.

    `TARGETED_DEVICE_FAMILY` on its own is asserted by
    scripts/pulse_app_store_review_fix_audit.py. The gap this catches is the
    *pair*: App Store Connect has no iPad slots for a `1`-family build, so iPad
    screenshots in the config cannot be uploaded, and their presence is a sign
    the metadata still describes an iPad-capable submission.
    """
    families = sources.setting("TARGETED_DEVICE_FAMILY")
    if families != {"1"}:
        return
    ipad = sorted(
        key
        for locale in (
            (sources.store_config.get("apple") or {}).get("info") or {}
        ).values()
        for key in (locale.get("screenshots") or {})
        if key.startswith(IPAD_SCREENSHOT_KEYS)
    )
    if ipad:
        failures.append(
            f"screenshots vs device family: TARGETED_DEVICE_FAMILY is iPhone-only (1) "
            f"but store.config.json declares iPad screenshot slots {ipad}. App Store "
            "Connect offers no iPad slots for this binary, so these cannot be "
            "uploaded."
        )


def check_paid_access_claim_is_current(sources, failures):
    """Store metadata must not still claim iOS has no paid digital access.

    The 1.0 workaround for Guideline 3.1.1 was to disable purchases in the iOS
    build, and the metadata says so. That was **reversed on purpose**:
    `src/payments/appleIapPremium.ts` ships StoreKit 2 with server-side
    verification, `Configuration.storekit` defines the products, and the review
    notes now tell App Review to buy Premium. A description that contradicts the
    review notes is a rejection risk in either direction.
    """
    stale = "Paid digital access is not available in this iOS build"
    iap_shipped = (ROOT / NATIVE / "src/payments/appleIapPremium.ts").is_file()
    description = sources.apple_info().get("description") or ""
    if iap_shipped and stale in description:
        failures.append(
            "paid access claim: store.config.json still says "
            f"{stale!r}, but mobile-native/src/payments/appleIapPremium.ts ships "
            "StoreKit purchases and apple.review.notes tell App Review to buy "
            "Premium. The description contradicts both."
        )


CHECKS = [
    ("required files", check_required_files),
    ("ios bundle id", check_bundle_identifier),
    ("app display name", check_display_name),
    ("url scheme", check_url_scheme),
    ("app icon", check_app_icon),
    ("launch screen", check_launch_screen),
    ("usage descriptions", check_usage_descriptions),
    ("encryption declaration", check_encryption_declaration),
    ("associated domains", check_associated_domains),
    ("notifications linked", check_notifications_linked),
    ("eas project id", check_eas_project_id),
    ("eas production profile", check_eas_production_profile),
    ("eas channel needs expo-updates", check_eas_channel_needs_expo_updates),
    ("asc app id", check_asc_app_id),
    ("store metadata urls", check_store_metadata_urls),
    ("store metadata version", check_store_metadata_version),
    ("screenshots exist", check_screenshots_exist),
    ("screenshots match device family", check_screenshots_match_device_family),
    ("paid access claim", check_paid_access_claim_is_current),
]


def audit(sources):
    failures = []
    for _, check in CHECKS:
        check(sources, failures)
    return failures


# --- self-test --------------------------------------------------------------
#
# The bug this file had was not only that it read the wrong tree: it read files
# whose values could not change the verdict. A check that cannot fail is
# indistinguishable from a check that passes. So every check above ships a
# mutation that must break it, and every check that is *currently red* also
# ships a repair that must make it green - together those prove the check reads
# a value that decides the answer, in both directions.


def _break_required_files(sources):
    sources.missing = [f"{NATIVE}/ios/PulseSoc/Info.plist"]


def _break_setting(setting, replacement):
    def mutate(sources):
        sources.pbxproj = re.sub(
            rf"^(\s*{re.escape(setting)} = )[^;]+;",
            rf"\g<1>{replacement};",
            sources.pbxproj,
            flags=re.M,
        )

    return mutate


def _break_plist(key, value):
    def mutate(sources):
        sources.info_plist[key] = value

    return mutate


MUTATIONS = {
    "required files": _break_required_files,
    "ios bundle id": _break_setting("PRODUCT_BUNDLE_IDENTIFIER", "com.pulsesoc.nativeapp"),
    "app display name": _break_setting("PULSESOC_DISPLAY_NAME", "PulseSoc Dev"),
    "url scheme": _break_plist("CFBundleURLTypes", [{"CFBundleURLSchemes": ["pulse"]}]),
    "app icon": _break_setting("ASSETCATALOG_COMPILER_APPICON_NAME", "Icon"),
    "launch screen": _break_plist("UILaunchStoryboardName", "Splash"),
    "usage descriptions": _break_plist("NSMicrophoneUsageDescription", ""),
    "encryption declaration": _break_plist("ITSAppUsesNonExemptEncryption", True),
    "associated domains": lambda s: s.entitlements.update(
        {"com.apple.developer.associated-domains": ["applinks:example.com"]}
    ),
    "notifications linked": lambda s: setattr(
        s, "podfile_lock", s.podfile_lock.replace("EXNotifications", "EXSomethingElse")
    ),
    "eas project id": lambda s: s.app_json["extra"]["eas"].update({"projectId": "deadbeef"}),
    "eas production profile": lambda s: s.eas["build"]["production"].update(
        {"distribution": "internal"}
    ),
    "eas channel needs expo-updates": lambda s: s.eas["build"]["preview"].update(
        {"channel": "preview"}
    ),
    "asc app id": lambda s: s.eas["submit"]["production"]["ios"].update({"ascAppId": "1"}),
    "store metadata urls": lambda s: s.apple_info().update({"supportUrl": ""}),
    "store metadata version": lambda s: s.store_config["apple"].update({"version": "9.9"}),
    "screenshots exist": lambda s: s.screenshot_files.update(
        {next(iter(s.screenshot_files)): False}
    ),
    "screenshots match device family": lambda s: s.apple_info()
    .setdefault("screenshots", {})
    .setdefault("APP_IPAD_PRO_3GEN_129", ["x.png"]),
    "paid access claim": lambda s: s.apple_info().update(
        {
            "description": "Paid digital access is not available in this iOS build "
            "until Apple In-App Purchase support is implemented and approved."
        }
    ),
}

# Repairs for checks that are red against the tree as it stands. Breaking an
# already-red check proves nothing, so these prove the other direction: the
# check goes green when the underlying value is fixed.
REPAIRS = {
    "eas channel needs expo-updates": lambda s: [
        (p or {}).pop("channel", None) for p in (s.eas.get("build") or {}).values()
    ],
    "store metadata version": lambda s: s.store_config["apple"].update(
        {"version": s.resolve_plist("CFBundleShortVersionString")}
    ),
    "screenshots match device family": lambda s: [
        locale.get("screenshots", {}).pop(key, None)
        for locale in ((s.store_config.get("apple") or {}).get("info") or {}).values()
        for key in [
            k for k in list(locale.get("screenshots") or {}) if k.startswith(IPAD_SCREENSHOT_KEYS)
        ]
    ],
    "paid access claim": lambda s: s.apple_info().update(
        {"description": "PulseSoc is a social platform. Premium is sold via Apple IAP."}
    ),
}


def self_test():
    """Prove every check discriminates in both directions.

    A check is only meaningful if its verdict tracks its source. For a check that
    is green against the tree, breaking the source must turn it red. For a check
    that is already red, breaking it again proves nothing - it is already at a
    failure - so the proof runs from its repair: repairing must turn it green,
    and breaking the repaired sources must turn it red again.
    """
    baseline = audit(Sources())
    red = sorted(label for label, check in CHECKS if _run(check, Sources()))
    problems = []

    for label, check in CHECKS:
        if label not in red:
            broken = Sources()
            MUTATIONS[label](broken)
            if not _run(check, broken):
                problems.append(
                    f"{label}: green, and stayed green after its source was mutated. "
                    "This check cannot fail."
                )
            continue

        if label not in REPAIRS:
            problems.append(
                f"{label}: red against the tree but has no REPAIRS entry, so it is "
                "not proven it can ever pass - an always-red check is as useless as "
                "an always-green one."
            )
            continue
        repaired = Sources()
        REPAIRS[label](repaired)
        if _run(check, repaired):
            problems.append(
                f"{label}: still red after its repair mutation, so it cannot pass."
            )
            continue
        rebroken = Sources()
        REPAIRS[label](rebroken)
        MUTATIONS[label](rebroken)
        if not _run(check, rebroken):
            problems.append(
                f"{label}: went green when repaired but stayed green when re-broken, "
                "so its verdict does not track its source."
            )

    print(f"self-test: {len(CHECKS)} checks, {len(baseline)} live failures")
    print(f"self-test: currently red: {red or 'none'}")
    if problems:
        print("self-test FAILED")
        for problem in problems:
            print(f"- {problem}")
        raise SystemExit(1)
    print(
        f"self-test ok: {len(CHECKS) - len(red)} green checks can be broken, "
        f"{len(red)} red checks can be repaired and re-broken"
    )
    return 0


def _run(check, sources):
    failures = []
    check(sources, failures)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove every check discriminates instead of auditing the tree",
    )
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(self_test())

    failures = audit(Sources())
    if failures:
        print("PulseSoc store submission readiness audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("pulsesoc store submission readiness audit ok")


if __name__ == "__main__":
    main()
