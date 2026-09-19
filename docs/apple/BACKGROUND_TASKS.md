# BackgroundTasks — the array is guarded in the wrong file

Written 2026-09-19. Capability #5 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`,
recorded there as **NOT IMPLEMENTED** since the unused `fetch` declaration was
dropped in `f8abd3bc`.

That status is correct and the capability is genuinely unbuilt, so a plan for
`BGTaskScheduler` would be short. The document is not short, because auditing
*why* it is unbuilt turned up something about the declaration that is already
there.

---

## The question is narrower than it looks

PulseSoc's periodic work already runs, and it does not run on the phone. The
`Procfile` starts six workers beside the web process — `undx_worker`,
`email_worker`, `ads_worker`, `alert_worker`, `media_worker`, and
`supplier_worker`, the last on an explicit `--interval 300` (`Procfile:7`). The
backend is the authority, it knows when something changed, and push is how it
says so.

So "should the iOS app run background tasks" is not a question about refresh
intervals. It is a question about whether there is any fact the phone needs that
the server cannot tell it. Hold that thought until the recommendation.

The audit's own verdict already pointed here: *"Most of what background refresh
would buy is already delivered by push."*

---

## What is declared today

`ios/PulseSoc/Info.plist:68-73`:

```xml
<key>UIBackgroundModes</key>
<array>
	<string>audio</string>
	<string>voip</string>
	<string>remote-notification</string>
</array>
```

`audio` and `voip` are load-bearing for the locked surfaces — background call
audio and background radio playback depend on the first, PushKit on the second.
`remote-notification` is the subject of Finding 3.

`app.json` carries the identical array, so a clean Expo prebuild cannot
reintroduce `fetch`. That matters, and it is also where Finding 1 starts.

---

## Finding 1 — the protection test guards `app.json`; the build reads `Info.plist`

`tests/protection/test_realtime_audio_architecture.py:312` is
`test_ios_microphone_and_background_audio_configuration_is_intact`. Its body:

```python
app = json.loads((ROOT / "mobile-native" / "app.json").read_text(encoding="utf-8"))
info_plist = app.get("expo", {}).get("ios", {}).get("infoPlist", {})
self.assertTrue(str(info_plist.get("NSMicrophoneUsageDescription", "")).strip())
self.assertIn("audio", info_plist.get("UIBackgroundModes", []))
```

The variable is named `info_plist`. It is `app.json`.

The comment above it is exactly right about the stakes — *"without the audio
background mode a backgrounded call goes silent. Neither failure is visible in a
simulator run or a unit test."* The author understood the failure precisely and
guarded the file that does not ship.

`mobile-native/ios/` is committed, and `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`
settled that it stays committed-and-defended; the only prebuild in CI runs in an
ephemeral runner and asserts nothing. So `ios/PulseSoc/Info.plist` is what
Xcode reads and what ships. Nothing checks it:

| File | Holds `UIBackgroundModes` | In `categories[].paths` | In `dependency_watch.files` | Asserted by a test |
|---|---|---|---|---|
| `mobile-native/app.json` | yes | no | **yes** (`:507`) | **yes** (`:312`) |
| `mobile-native/ios/PulseSoc/Info.plist` | yes — **and this is the one that builds** | no | no | **no** |

Delete `<string>audio</string>` from the plist and every gate stays green: the
protection suite passes because `app.json` is untouched, the real-time audio
change gate reports no protected path changed, `npm run verify` is unaffected,
and background call and radio audio are dead on device.

This is the same shape the manifest already documents happening once before, in
its own note on `useLiveBroadcastRoom.ts`: the manifest named a file that the
Agora migration had reduced to a shim, *"so the entire Live audio runtime could
be rewritten without a declaration."* Here the protected file is not a shim — it
is a genuine source — but it is the *upstream* one, and the downstream artefact
it generates is committed and authoritative.

**Not fixed here, deliberately.** The fix is to add
`mobile-native/ios/PulseSoc/Info.plist` to the manifest and to extend the test to
read both files. But `config/realtime-audio-protected-paths.json` is itself in
`categories[].paths` under `audio_governance` — "The lock itself" — so editing it
requires `reports/realtime_audio_change_declaration.md` and a CODEOWNERS
approval. And `unrelated_mission_policy` states the rule this mission is bound
by: *a mission whose subject is not real-time audio must not edit any path in
`categories[].paths`*. An Apple-capabilities mission is not an audio mission.
Recorded as owed, with the exact patch, below.

### A smaller one alongside it

`dependency_watch.required_ios_configuration` (`:523-526`) states both
assertions in machine-readable form:

```json
"app.json:expo.ios.infoPlist.NSMicrophoneUsageDescription": "must be present and non-empty",
"app.json:expo.ios.infoPlist.UIBackgroundModes": "must contain \"audio\""
```

Nothing reads that key. `grep -rn required_ios_configuration scripts tests
mobile-native/src` returns nothing; the gate reads only
`dependency_watch["files"]` (`realtime_audio_change_gate.py:172`) and the
architecture test reads `must_be_exactly_pinned` and `baseline_versions`
(`:305-309`) but never this. The assertions are enforced by the hardcoded test at
`:312` that happens to agree with them.

So the manifest — a file whose own description insists *"It is not documentation:
editing it changes what CI enforces"* — contains one block that is documentation.
Correcting it there would change nothing. Worth knowing before someone tightens
the wording and believes they tightened the rule.

## Finding 2 — removing `fetch` created a re-add obligation, and §5 does not say so

The audit's resolution note reads: *"If background refresh is wanted later it
arrives with a `BGTaskSchedulerPermittedIdentifiers` entry and an actual task."*

That is incomplete, and the missing half is in Apple's own headers. From the iOS
26.5 SDK, `BackgroundTasks.framework/Headers/BGTask.h`:

> Executing app refresh tasks requires setting the `fetch` `UIBackgroundModes`
> capability. *(on `BGAppRefreshTask`)*

> Executing processing tasks requires setting the `processing` `UIBackgroundModes`
> capability. *(on `BGProcessingTask`)*

So `BGTaskScheduler` is not a `fetch`-free modern replacement. A refresh task
needs `fetch` — the exact mode just removed — **and** the identifiers key. A
processing task needs `processing`, which this app has never declared.

Dropping `fetch` was still right: it was claimed and unimplemented, which is the
worst of the three states. But the cost was not zero, and the audit records it
as though it were. Implementing #5 is now a **two-key** Info.plist change in
**two files**, one of which (Finding 1) nothing guards.

Quoted from the SDK rather than from memory on purpose: the plausible-sounding
wrong version — "`BGTaskScheduler` superseded the `fetch` mode" — is wrong, and
it is wrong in the direction that produces a runtime error nobody can read.

## Finding 3 — `remote-notification` is not `fetch`'s twin

The obvious next question after removing one unused mode is whether the other one
is unused too. It is, but in a different way, and the difference decides the
recommendation.

The chain, verified end to end:

| Hop | State |
|---|---|
| `UIBackgroundModes` contains `remote-notification` | **present** (`Info.plist:72`) |
| A background-notification receiver in the binary | **present** — `expo-notifications@~0.32.17` ships `NotificationsBackgroundTaskConsumer.swift` and `NotificationsAppDelegateSubscriber.swift:21-29` implements `didReceiveRemoteNotification:fetchCompletionHandler:` |
| That receiver reachable | **no** — it is an `EXTaskConsumerInterface`, registrable only through `expo-task-manager`, which is absent from `package.json`, from `node_modules`, and from `Podfile.lock` |
| A server that sends a background push | **no** — the entire backend emits exactly two `apns-push-type` values: `voip` (`services/pulsesoc_voip_push.py:523`) and `alert` (`services/pulsesoc_notification_system.py:2545`). There is no `background`. `apns-push-type: background` is mandatory for a silent push, so no code path can produce one. |

With no consumer registered, `NotificationCenterManager.didReceiveNotification`
(`:132-139`) finds no delegate that handles the payload and calls
`completionHandler(.noData)`. A clean no-op, not a leak.

The asymmetry with `fetch` is the point. For `fetch`, neither half existed and
neither was ever going to — nothing in the app called the legacy fetch API. For
`remote-notification` the receiving half is **already compiled into the shipped
binary** and is one dependency away from reachable, while the sending half is one
header away. It is the cheapest unbuilt background capability the app has.

**Recommendation: keep it.** Not out of caution — because removing it would be
removing the cheap option rather than removing dead weight, and because unlike
`fetch` it is ubiquitous and unremarkable in any app that receives push.

## Finding 4 — the simulator cannot test this at all

`BGTaskScheduler.h`, on `BGTaskSchedulerErrorCodeUnavailable`:

> - The user has disabled background refresh in settings.
> - The app is running on Simulator which doesn't support background processing.

Apple names the simulator in the error documentation. So #5 joins universal
links, associated domains, StoreKit and keychain access groups on the
**device-only** list. There is no partial simulator signal to work from; a
`submit` call there fails for a reason unrelated to the code.

## Finding 5 — the two failure codes are each ambiguous

`BGTaskSchedulerErrorCodeNotPermitted` (=3) has four documented causes, and the
first two are the ones that will actually happen here:

- the app doesn't set the appropriate `UIBackgroundModes` mode — i.e. Finding 2's
  re-added `fetch`
- the identifier wasn't in `BGTaskSchedulerPermittedIdentifiers`

One error code, and from the code alone they are indistinguishable. Both are
Info.plist mistakes, both are silent until a real device submits a real request,
and Finding 1 means one of the two files involved has no guard. Any
implementation should log which of the two it believes it configured, because the
error will not say.

## Finding 6 — the gate that will not run

Carried from the audit and from memory, because it is the failure most likely to
be written into a first implementation: a module-scope `AppState.currentState`
read is always `"inactive"` in this codebase — it is seeded during app launch, so
a module singleton that gates work on being foregrounded kills itself for the
whole process. Jest mocks `AppState` as a function, so CI structurally cannot see
it.

A background task gated that way never runs, reports no error, and passes every
test.

---

## Recommendation

**Do not implement `BGTaskScheduler`.** Not "later" — the shape is wrong for this
architecture. `BGTaskScheduler` is the client guessing when to ask the server
whether anything changed. In a system where the backend is the authority and
already runs six workers, the server knows when something changed and does not
need to be asked. Polling-shaped background refresh is the wrong primitive for a
server-authoritative product, and it costs battery to be wrong.

**If background work is ever genuinely needed, the path is the silent push**, not
the scheduler: add `expo-task-manager`, register the background consumer that is
already in the binary, and add `apns-push-type: background` beside the two types
the backend already sends. That is Finding 3's chain, completed, and it inherits
the existing push infrastructure — device registration, token lifecycle, the APNs
`.p8` auth — rather than building a second scheduling system next to it.

**Before either, close Finding 1.** The unguarded `Info.plist` is not a
BackgroundTasks problem; it is a real-time audio problem that BackgroundTasks
work happened to find, and it is live today.

---

## What is owed

| Item | Where | Trigger |
|---|---|---|
| **Protect `ios/PulseSoc/Info.plist`** — add it to `dependency_watch.files`, and extend `test_realtime_audio_architecture.py:312` to assert `audio` in **both** files | `config/realtime-audio-protected-paths.json`, `tests/protection/test_realtime_audio_architecture.py` | **audio mission + change declaration + CODEOWNERS.** Live exposure today, not a future one |
| Correct audit §5's resolution note to say `fetch` (or `processing`) must return alongside `BGTaskSchedulerPermittedIdentifiers` | `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` | done in this commit |
| Decide whether `required_ios_configuration` should be read by the test rather than duplicated in it | `config/realtime-audio-protected-paths.json` | same audio-mission gate as row 1 |
| Device validation of any background task | iPhone 16 Pro | **owed by construction** — the simulator returns `Unavailable` (Finding 4) |

---

## What was verified, and what was not

**Verified by reading, at `867acdda`:** the complete `UIBackgroundModes` array in
both `ios/PulseSoc/Info.plist` and `app.json`, and that they agree; the absence of
`BGTaskScheduler`, `BGAppRefreshTask`, `BGProcessingTask` and `BackgroundTasks`
from `src/`, `ios/` and `modules/`; the absence of `expo-task-manager` and
`expo-background-fetch` from `package.json`, `node_modules` and `Podfile.lock`;
`NotificationsBackgroundTaskConsumer.swift` and
`NotificationsAppDelegateSubscriber.swift` in the installed `expo-notifications`;
`NotificationCenterManager.swift:132-139`; every `apns-push-type` occurrence in
`services/`; `test_realtime_audio_architecture.py:312` in full; that no file in
`scripts/`, `tests/` or `mobile-native/src/` reads `required_ios_configuration`;
`realtime_audio_change_gate.py:172`; the `Procfile`; and the `BGTask.h` /
`BGTaskScheduler.h` header comments quoted above, from the iOS 26.5 SDK at
`/Applications/Xcode.app/…/iPhoneOS26.5.sdk`.

**Not verified.**

- **Finding 1's exploit was not performed.** The claim that deleting `audio` from
  the plist leaves every gate green follows from the table — no manifest entry, no
  test reference — not from having deleted it and watched CI pass. Deliberately
  not attempted: the experiment is a protected-path edit.
- **No `BGTaskScheduler` call was ever made**, on device or simulator, so
  Findings 4 and 5 are read from Apple's headers rather than observed. That is
  first-party evidence but it is documentation, not behaviour.
- **No background push was sent.** Finding 3's chain is established by absence —
  a missing package, a missing push type — and absence is the one thing a grep
  proves well. But that the chain would *work* once completed is untested.
- **The `fetch` removal itself was not device-validated.** Nothing implemented
  background fetch, so there is nothing to regress; recorded because "nothing to
  regress" is a judgement, not an observation.
