# Two decisions the Apple plan was blocked on

Written 2026-09-19. The Stage 1 audit
(`PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`) ended with two open questions that
gate everything in Waves 2–5. Both were written down as "needs a decision."
Neither actually needed a decision — both were answerable from evidence, and the
evidence is recorded here so the answers can be checked rather than trusted.

---

## Decision 1 — raise the deployment floor from 15.1 to **16.1**

### The question

`IPHONEOS_DEPLOYMENT_TARGET = 15.1` appears four times in
`mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj` (lines 435, 471, 541,
596 — Debug and Release for both the project and the target). It sits below the
floor of six of the fourteen audited capabilities:

| Capability | Needs |
|---|---|
| App Intents / Siri / Shortcuts | iOS 16.0 |
| Live Activities | iOS 16.1 |
| Dynamic Island | iOS 16.1 |
| Interactive widgets | iOS 17.0 |
| ActivityKit push updates | iOS 17.2 |
| Control Center controls | iOS 18.0 |

The audit deferred this because raising a floor drops users, and nobody had
counted how many.

### The evidence

Production Postgres, queried 2026-09-19 via
`railway run --service Postgres`. iOS major version parsed from
`mobile_security_sessions.user_agent`, using the `CPU iPhone OS <maj>_<min>`
form where present and the `Darwin/<maj>` form otherwise (Darwin 24 → iOS 18,
Darwin 25 → iOS 26; Apple renumbered 18 → 26, there is no iOS 19).

**Native app sessions — 7,441 parsed:**

| iOS major | Sessions | Share |
|---|---|---|
| 18 | 4,582 | 61.6% |
| 26 | 2,859 | 38.4% |
| anything below 18 | **0** | **0.00%** |

Minor versions: 18.6 (4,154), 26.6 (1,541), 26.4 (1,317), 18.1 (400), 18.7
(28), 26.5 (1).

A further 4,115 session rows carry `PulseSocNativeApp/1.0.x (ios; Expo)`, which
encodes no OS version at all. Those are **excluded**, not assigned to a bucket —
they cannot argue either way.

Cross-check by person rather than by session: 15 distinct users have ever held
an iOS session with a parseable OS. Ten are on 18, five on 26. None below.

### The one number that looks like a counter-argument, and is not

`visitor_logs` over 180 days has 39,498 parsed iOS user-agents, of which 3.46%
are below iOS 18 — including 1,007 hits on iOS 13 and a long thin tail down to
iOS 3. That tail is bot-shaped: 1,003 of the 1,007 iOS 13 hits are a single
frozen version string (`13.2`), which is a scraper signature, not a person with
a seven-year-old phone. More decisively, `visitor_logs` is **web traffic**. The
deployment target governs who can install and run the *app*. A Safari visitor on
iOS 13 is not affected by the floor either way, because the App Store will not
offer them the current build regardless.

### The decision

**16.1.** Recorded reasoning:

- It costs zero measured native users. Not "few" — zero sessions and zero people
  below 18, across the entire retained history of the table.
- It is the lowest floor that unlocks the most capabilities per point of risk:
  16.0 buys App Intents, and the extra 0.1 buys Live Activities *and* Dynamic
  Island, which are two of the three highest-value items in the audit.
- It stops short of 17 or 18 deliberately. The measured population would permit
  either, but 15 users is a small sample to extrapolate a hardware cutoff from,
  and 16.1 already unlocks everything Wave 2 and Wave 3 need. Going higher
  spends installable-device coverage on capabilities nothing is ready to build.
  17.2 and 18.0 features get `@available` gates instead — the app still runs on
  16.1, those specific surfaces just do not appear.

Hardware effect: 16.1 drops iPhone 6s, iPhone 7, and the first-generation SE.
15.1 supported them; no session from any of them exists.

### How to apply it — cheaper than first written

There is no `expo-build-properties` in `mobile-native/package.json`, so the
value lives only in the committed pbxproj. The first draft of this document
said two edits were needed — the pbxproj lines *and* an `expo-build-properties`
entry in `app.json` — on the assumption that a prebuild would otherwise put 15.1
back. **That assumption was tested on 2026-09-19 and is wrong.**

`npx expo prebuild --platform ios --no-install` was run against this tree. All
four `IPHONEOS_DEPLOYMENT_TARGET` lines came through untouched, as did
`PULSESOC_APS_ENVIRONMENT`, `PULSESOC_DISPLAY_NAME`,
`SWIFT_OBJC_BRIDGING_HEADER` and the associated-domains entitlement. A
**non-clean** prebuild merges and normalizes; it does not reset. Only
`--clean` regenerates, and `mobile-native/docs/LOCALIZATION.md:403` already
forbids that.

So the floor change is **one edit**: the four pbxproj lines. No `app.json`
change, which means no `dependency_watch` trip
(`config/realtime-audio-protected-paths.json:507`), no new dependency, and no
declaration.

`expo-build-properties` remains worth adding as belt-and-braces against a future
`--clean`, but it should ride along with some *other* change that is already
paying for a declaration, rather than triggering one by itself.

### Applied 2026-09-19

Two files, neither of them a `dependency_watch` path:

- `PulseSoc.xcodeproj/project.pbxproj` — all four occurrences, 15.1 → 16.1.
- `ios/Podfile.properties.json` — added `"ios.deploymentTarget": "16.1"`.
  `ios/Podfile:19` reads this key (`podfile_properties['ios.deploymentTarget']
  || '15.1'`) to set the Podfile's `platform :ios`. This is the same key
  `expo-build-properties` writes, reached without adding the dependency.

Re-verified by a second `npx expo prebuild --platform ios --no-install`: all
four lines came back as 16.1.

### What `pod install` actually does with this, and what it costs

Run in a scratch copy of the repo, to answer this before it surprised someone:

- **`Podfile.lock` did not change.** The floor is not part of the resolved
  dependency graph. The `dependency_watch` trap on that file
  (`config/realtime-audio-protected-paths.json:510`) is therefore **not**
  triggered by this change — the deferred declaration this document originally
  predicted does not exist.
- **The individual pods stayed at 15.1** — 288 of them, with a handful lower.
  That is not a failure of the setting. React Native's own `post_install` hook
  overwrites each pod's target with
  `max(min_ios_version_supported, existing)` (`react-native/scripts/cocoapods/utils.rb:352`),
  and RN 0.81's minimum is 15.1. Only the four Pods-project aggregate configs
  take 16.1.

A pod floor *below* the app's is correct and supported; only the reverse is an
error. So the end state is: app at 16.1, pods at 15.1, lockfile untouched, no
declaration owed.

### It compiles — verified, not assumed

The commit that applied the floor said a build was still owed. It has now been
run: a full Release `xcodebuild` of the `PulseSoc` scheme against
`generic/platform=iOS Simulator`, in the warm rig at `~/Desktop/cpx-prefetch-iso`
with the 16.1 patches applied and `pod install` re-run.

**`** BUILD SUCCEEDED **`**, exit 0, zero compiler diagnostics. (A naive
`grep -c "error:"` returns 1; that hit is the *source line* `setCategory:...
error:&err` quoted inside an unrelated warning, not a diagnostic. Anchor the
grep to the `file:line:col: error:` form before believing it.)

The 39,464-line log also confirms the split described above, by counting the
`-target` triples the compiler was actually invoked with:

| Triple | Compile units |
|---|---|
| `apple-ios15.1-simulator` | 4,186 |
| `apple-ios16.1-simulator` | 18 |

That is the predicted end state observed directly: the app's own targets build
at 16.1, the pods build at 15.1 because RN's `post_install` pins them there, and
the mixed floor links cleanly. The rig was restored to its committed state
afterwards.

What this does **not** establish: that the app runs correctly on a 16.1 device.
Compiling is necessary, not sufficient. A device install on the iPhone 16 Pro
remains owed before the next store build — but the risk it is checking for is
now runtime behaviour, not the floor change itself.

### What a non-clean prebuild does churn

Five committed files, all cosmetically:

| File | Change |
|---|---|
| `project.pbxproj` | quoting normalized (`PRODUCT_NAME = PulseSoc` → `"PulseSoc"`, `TARGETED_DEVICE_FAMILY = 1` → `"1"`), plus Expo's `noop-file.swift` added to the group and Sources phase |
| `PulseSoc.entitlements` | trailing newline stripped |
| `SplashScreen.storyboard`, the 1024px app icon, `SplashScreenBackground/Contents.json` | regenerated byte-differently, same content |
| `PulseSoc/noop-file.swift` | new, untracked |
| `PulseSoc/Info.plist` | indentation rewritten end to end — same keys, same values |

None of it is destructive, but it is exactly the kind of diff someone commits by
accident. It was committed by accident here: `f8abd3bc` carried prebuild's
93/92-line reformat of `Info.plist` on top of a one-line deletion, and
`5a87e753` had to undo it. The `Info.plist` row is in this table because that
reformat was invisible at review time — the file was already modified for an
unrelated reason, so the churn hid inside a diff that was expected to be there.

The rule that follows: **anything that runs `expo prebuild` must diff `ios/`
before staging**, and restore every file it did not intend to change. Files
already dirty for another reason are the dangerous ones.

---

## Decision 2 — extensions are **committed Xcode targets**, not generated by a config plugin

### The question

`APPLE_CAPABILITIES_AND_ENTITLEMENTS.md` called the target-generation strategy
"the single largest unknown in the whole plan." WidgetKit, Live Activities,
Share Extensions and Control Center controls all require a second native target,
and the repo has exactly one (`grep -c "isa = PBXNativeTarget"` → 1). The
assumption behind the unknown was that `ios/` is generated by prebuild, which
would make a hand-added target something prebuild deletes — forcing a custom
config plugin that writes Xcode targets, a genuinely hard and fragile thing to
build.

### The evidence

The assumption was wrong. `ios/` is a **committed bare workflow**. Twenty-six
files are tracked under `mobile-native/ios/`, and `project.pbxproj` is one of
them:

```
$ git ls-files --error-unmatch mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj
mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj
$ git check-ignore -v mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj
(no output — not ignored)
```

Also tracked: `AppDelegate.swift`, `PulseSoc.entitlements`, `Info.plist`,
`PrivacyInfo.xcprivacy`, `PulseSoc-Bridging-Header.h`, `Configuration.storekit`,
the xcscheme, the xcworkspace, and two hand-written build scripts
(`build_device.sh`, `build_sim.sh`).

The policy is already written down. `mobile-native/docs/LOCALIZATION.md:403`:
the native project is committed, release builds use it rather than regenerating
it, native configuration changes must be applied in *both* Expo config and the
committed project, and `expo prebuild --clean` is not to be run.

Prebuild does run in CI — `.github/workflows/realtime-audio.yml:257`,
`npx expo prebuild --platform ios --no-install`. It is harmless here: the job
runs on a fresh `actions/checkout` in an ephemeral `macos-latest` runner, does
not commit its output, and asserts only that `NSMicrophoneUsageDescription` and
the `audio` background mode survived. Nothing compares the regenerated project
against the committed one, so a hand-added target cannot fail that gate.

And the stronger result, from actually running it (see Decision 1): a non-clean
prebuild **preserved every hand-set value in the project**, including custom
build settings and the bridging header. The native-target count came back as 1 —
unchanged, not reset. That is direct evidence that a committed extension target
survives the prebuild that CI and developers actually run. The destructive case
is `--clean` alone.

### The decision

**Add extension targets directly to the committed Xcode project.** No config
plugin that synthesizes targets.

What this buys: the hard part stops being "make prebuild emit a widget target"
and becomes ordinary Xcode work — add the target, add it to the scheme, add the
App Group and `keychain-access-groups` entitlement to both targets, add the
second App ID in the portal.

What it costs, stated plainly: the project now has hand-maintained state that
`expo prebuild` does not know about. The existing dual-maintenance policy
already covers this, but it currently covers plist keys and bundle ids, which
are individually small. A whole extension target is not small. The failure mode
is a developer running `npm run prebuild:ios` (it is still in `scripts`) and
committing the result, which would drop every extension target at once.

Mitigation: a protection test asserting the expected `PBXNativeTarget` count and
each target's name in the committed pbxproj. That converts "somebody regenerated
the project" from a silent capability deletion into a red build. Cheap, and it is
the only thing standing between this decision and its one real risk.

### The mitigation shipped first — 2026-09-19

It was originally scoped to "land with the first extension." That was the wrong
order, and the reasoning is worth keeping: the risk is not *adding* a target, it
is **losing** one, and the test can only notice a loss if it was already there
before the loss happened. Landing it with the first extension would leave the
window between now and then unguarded — and that window includes the 16.1 floor,
which has exactly the same failure mode.

`tests/protection/test_ios_native_target_inventory.py`, auto-discovered by
`scripts/protection/run_protection_suite.py` (its `SUITE_DIR.glob("test_*.py")`
means adding the file is enough to enforce it). Six assertions over two
hand-maintained properties of `project.pbxproj` that share one failure mode — a
clean prebuild reverts them, nothing fails to compile, and the loss is invisible
in review because the diff is enormous and mostly cosmetic:

- the native target count matches `EXPECTED_TARGETS`;
- every expected target is present, by name **and product type** (count alone
  cannot see a widget swapped for a share extension);
- every `IPHONEOS_DEPLOYMENT_TARGET` is 16.1;
- there are still **four** such declarations — because an *absent* setting does
  not error, it inherits, so "every value present is 16.1" is satisfied by a file
  that declares it once and leaves three configurations low;
- the project file is committed and present at all;
- the parse is not vacuous.

That last one earns its place. The house hazard for a gate like this is that
every failure of its *reader* makes it greener — a regex that stops matching
returns an empty set, which compares equal to an empty expectation and passes
forever.

**Verified by mutation, not by assertion.** Nine cases run against a throwaway
`ROOT` in `/tmp` holding only the test and a copy of the pbxproj; the real tree
was never written to, and a `git status` + `git diff` shasum either side came
back identical (`6f61ba25…` both times). Each mutation had to turn **its named
test** red, not merely something:

| Mutation | Must fail |
|---|---|
| add an untracked second target | count |
| rename the app target | names |
| revert the floor to 15.1 | floor |
| revert *one* configuration to 15.1 | floor |
| delete one floor declaration | declaration count |
| break the target-block regex | vacuity guard |
| strip `productType` | names |

Plus **two controls that had to stay green** — an unmutated copy, and a
cosmetic comment edit. Without those, a harness that reported "everything fails"
would look like perfect coverage. Final score **9/9**.

Adding an extension now requires editing `EXPECTED_TARGETS` in the same commit.
That is the intent, not an inconvenience: it makes the change deliberate and
reviewable instead of accidental and silent.

---

## Status after these two decisions

Wave 2 is no longer blocked on unknowns. What remains is ordinary sequenced
work, and the next item is still Sign in with Apple — highest value, no target
work, no interaction with any protection lock.

Nothing in this document has been implemented. It records decisions and their
evidence, which is the point: the next person to ask "why 16.1?" or "why not a
config plugin?" should be able to settle it by reading.
