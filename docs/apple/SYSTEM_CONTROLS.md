# System controls — the half that shipped, and the half that cannot

Written 2026-09-19. Capability #14 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`.

The audit records #14 as **NOT IMPLEMENTED**, and for what the audit meant by it
— Action Button and Control Center — that is still correct. But "system
controls" as a surface is not empty in this app. PulseSoc already puts a full
transport control set on the lock screen, in Control Centre, on AirPods and in
CarPlay, through a local Expo module written in Swift that has been shipping for
some time. Nobody wrote it down.

So this document has two halves, and they want different things from the reader:

1. **What ships today** — `MPNowPlayingInfoCenter` + `MPRemoteCommandCenter` via
   `modules/pulse-now-playing`. Verified by reading the Swift, the JS bridge, the
   podspec, the lockfile and the engine that consumes it. Four findings, one of
   which is user-visible today.
2. **Action Button and Control Center** — genuinely not built, genuinely blocked,
   and the block is not the one people assume.

Everything below is read from the tree at commit `795c51ab` unless stated.

---

## Part 1 — the system controls PulseSoc already has

### It is real, and it is linked

Not a stub. The evidence chain, end to end:

| Link | Evidence |
|---|---|
| Swift module exists | `modules/pulse-now-playing/ios/PulseNowPlayingModule.swift`, 159 lines |
| Declared to Expo autolinking | `expo-module.config.json` → `{"platforms":["apple"],"apple":{"modules":["PulseNowPlayingModule"]}}` |
| Resolved as a dependency | `package.json:67` → `"pulse-now-playing": "file:./modules/pulse-now-playing"`; `node_modules/pulse-now-playing` is a symlink to it |
| Actually built into the app | `ios/Podfile.lock:342` `PulseNowPlaying (1.0.0)`, checksum `b09bd41c…` |
| Consumed | `src/native/nowPlayingBridge.ts` → `src/core/pulseRadio.ts:14` |

That last row matters more than it looks. `requireOptionalNativeModule` returns
`null` when a module is absent and every export becomes a silent no-op — which is
the correct design (see `APPLE_NATIVE_ARCHITECTURE.md`, the three-layer
availability pattern) but also means a module that *failed* to link looks exactly
like a module that is working on a platform that does not have it. The Podfile.lock
entry is what distinguishes the two, so it is worth citing rather than assuming.

### The control surface

`configureRemoteCommands()` runs once, in `OnCreate`, and arms eight commands.
Each one does the same thing: forward a string to JS and return `.success`.

| Command | Swift | Emitted event | Engine action |
|---|---|---|---|
| Play | `:41-45` | `{command:"play"}` | `playPulseRadio()` |
| Pause | `:47-51` | `{command:"pause"}` | `pausePulseRadio()` |
| Toggle play/pause | `:53-57` | `{command:"toggle"}` | `togglePulseRadio()` |
| Next track | `:59-63` | `{command:"next"}` | `playNextTrack()` |
| Previous track | `:65-69` | `{command:"previous"}` | `playPreviousTrack()` |
| Scrub | `:71-81` | `{command:"seek", positionSeconds}` | `seekPulseRadioTo(ms)` |
| Skip forward | `:83-89` | `{command:"skipForward", intervalSeconds}` | `seekPulseRadioBy(+ms)` |
| Skip backward | `:91-97` | `{command:"skipBackward", intervalSeconds}` | `seekPulseRadioBy(-ms)` |

Skip forward/back advertise `preferredIntervals = [15]` (`:84`, `:92`) — that is
what draws "15" inside the arrow glyph on the lock screen — but the handler reads
the interval off the event rather than trusting its own advertisement
(`:86`, `:94`), which is correct for CarPlay and accessories that send their own.

Metadata flows the other way through three functions: `setNowPlayingInfo`,
`updatePlaybackProgress`, `clearNowPlayingInfo`.

### Two pieces of care in the Swift worth keeping

**`applyNowPlayingInfo` merges; `updateProgress` refuses to create.**
`applyNowPlayingInfo` reads the existing dictionary and writes fields into it
(`:101`), so a metadata push does not wipe artwork that arrived asynchronously.
`updateProgress` does the opposite — it `guard`s on a dictionary already existing
(`:138`) and returns if there is none. Without that guard, a progress tick
arriving after `clearNowPlayingInfo()` would resurrect a bare, titleless Now
Playing entry on the lock screen for a track that is no longer playing.

**Artwork loading has a race guard.** `loadArtwork` captures the URL string it
was asked for and, on completion, re-checks `lastArtworkUrl == requestedUrlString`
before applying (`:150`). Skipping tracks quickly otherwise lands whichever
download finished last. The in-flight task is also cancelled on change (`:125`)
and on clear (`:33`).

### The property that makes this safe under the real-time audio locks

This is the reason this capability can be documented at all without tripping the
protection rules, and it is worth stating precisely.

**The native module never touches `AVAudioSession`.** Grep it: the file imports
`ExpoModulesCore` and `MediaPlayer` and nothing else. It owns no player, no
session, no category. A lock-screen button press does not start audio; it sends a
string to JavaScript.

Arbitration then happens where everything else in this app arbitrates — in
`core/mediaPlaybackCoordinator.ts`, which ranks claimants:

```
call: 100, recording: 90, live: 70, voice: 60, viewer: 50,
status: 40, reel: 40, feed: 35, music_preview: 30, radio: 20
```

and refuses any claim beneath the incumbent (`mediaPlaybackCoordinator.ts:43`).
Every radio entry point — including all four that a remote command can reach —
claims as `kind: "radio"`, the lowest rank in the table. So a play press on the
lock screen during a call is not ignored by the OS or disabled by the app: it
arrives, it asks for playback, and it is **refused**, leaving the state at
`paused` with `"Pulse Radio is paused for active audio."`.

That was a safety claim resting entirely on reading code. It now has a test —
see below.

### What the lock screen actually shows, and who decides

`pulseRadio.ts` is the **only** writer of Now Playing information in the entire
app. `git grep pushNowPlayingInfo -- src` returns exactly one non-test caller
file. Calls, live streams, voice messages, reels, status and music previews all
produce audio and none of them populate the Now Playing centre.

For calls this is fine and arguably correct — CallKit owns the lock screen during
a call and draws its own UI. For live streams it is not, and it produces the one
user-visible finding below.

---

## Findings

### 1. A paused radio track outlives its interruption, with a play button that does nothing

**This is the one a user can hit.**

When a higher-priority owner preempts the radio, the coordinator calls the
radio's `pause` callback, which is `() => pausePulseRadio(false)` — the
`releaseOwnership = false` form. That path ends at
`pushNowPlayingProgress(state.positionMillis / 1000, false, 0)`
(`pulseRadio.ts:156`): rate 0, **not** `clearNowPlaying()`. The lock screen
therefore keeps showing the PulseSoc track, rendered as paused.

During a **call** the user never sees it — CallKit is in front.

During a **live stream** they do. `live` is priority 70, retained in the
background (`BACKGROUND_RETAINED_KINDS`), and has no system UI of its own. So the
sequence is: listener has Pulse Radio playing → opens a live stream → locks the
phone → the lock screen shows a paused PulseSoc track with a play button →
pressing it calls `playPulseRadio()` → the claim is refused → state goes back to
`paused` → **nothing on the lock screen changes.** A button that is drawn,
enabled, pressable, and inert.

The refusal is correct. The silence about it is not.

**Not fixed here, deliberately.** Three options exist — `clearNowPlaying()` on a
non-owning pause; pushing an interruption-aware title; or having the live surface
own the Now Playing entry for its duration — and the third is the right one but
is a real feature, not a patch. More to the point, the change lives in radio
playback, which the mission brief locks by name even though
`config/realtime-audio-protected-paths.json` does not list the file. Documenting
it first and changing it deliberately is the correct order. Tracked below.

### 2. Next and previous are enabled unconditionally

`center.nextTrackCommand.isEnabled = true` and the previous equivalent are set
once in `OnCreate` and never revised. At the end of a queue with repeat off,
`playNextTrack()` reaches `stopAtEndOfQueue()` (`:316`), which pauses and pushes
progress `(0, false, 0)`. Pressing "next" on the last track therefore stops
playback and jumps the scrubber to zero — defensible, but not what the enabled
glyph promises. The system convention is to disable the command so the glyph
greys out.

Low severity; needs a JS→native call to toggle `isEnabled`, which the module does
not currently expose.

### 3. The command targets are registered and never removed

`configureRemoteCommands()` discards every handle returned by `addTarget`
(`:42`, `:48`, `:54`, …) and there is no `OnDestroy`. `MPRemoteCommandCenter` is a
process-wide singleton, so if the module is ever recreated within one process the
old closures stay registered and each press fires `sendEvent` more than once.

In a release build the module is created once and lives for the process, so this
never bites. It bites in development, where a fast refresh or a reload can
recreate module instances — and a duplicated `next` reads as "the lock screen
skips two tracks", which is the kind of bug that gets chased in JS for an hour.
Worth fixing when the file is next opened: keep the returned handles and remove
them in `OnDestroy`.

### 4. The lock screen had no test at all — now it does

Both radio suites that load the engine — `pulseRadio.test.ts` and
`pulseRadioOffline.test.ts`, 22 tests between them — mock the bridge with
`onRemoteCommand: jest.fn(() => () => undefined)`. The listener is registered and
thrown away. (The third radio suite, `pulseRadioQueueOrder.test.ts`, tests pure
functions and never loads the engine at all.) So 22 green tests covered the
in-app transport controls and **zero** covered the eight buttons a user presses
with the phone in their pocket —
including the arbitration property in the section above, which was the only thing
standing between a lock-screen press and audio contention with a live call.

`src/core/__tests__/pulseRadioRemoteCommands.test.ts` (new, this commit) captures
the listener instead of discarding it and drives it the way the native module
does. Twelve tests: the eight commands route correctly, an unknown command is
ignored rather than thrown, and — the reason the file exists — a play, next,
previous or toggle press while the claim is refused creates **no sound and calls
no audio-mode setter**, while pause still works, because stopping must never be
denied.

The suite was proved to discriminate by mutation rather than by being green:
removing the `if (!granted) return` guard from `playPulseRadio` and deleting the
`case "next"` handler turned exactly three tests red, and only those three. The
mutation was reverted and `git diff` on `pulseRadio.ts` is empty.

No production file was changed for this. The engine, the bridge and the Swift are
byte-identical to what shipped.

### 5. Two properties the Now Playing entry does not set

- **`MPNowPlayingInfoPropertyIsLiveStream`** is never set. Correct today — the
  radio plays finite tracks with durations. It becomes wrong the moment a live
  stream populates this surface (the fix for finding 1), and the lock screen will
  otherwise draw a scrubber for something that cannot be scrubbed.
- **`MPNowPlayingInfoCenter.playbackState`** is never set; the state is inferred
  from `MPNowPlayingInfoPropertyPlaybackRate` (`:117`, `:140`). Inference from
  rate is the older mechanism and works, but the explicit property is what iOS
  prefers and is more reliable across Control Centre and CarPlay. Cheap to add.

---

## Part 2 — Action Button and Control Center

This is what the audit meant by #14, and it remains NOT IMPLEMENTED.

### The Action Button is not an API

There is no "Action Button support" to build. The button (iPhone 15 Pro and
later, so including the iPhone 16 Pro target device) is assigned **by the user**
in Settings, and one of the things they can assign is a Shortcut. An app becomes
available to it by exposing an **App Intent**. That is the entire integration.

So the Action Button is not blocked on an Action Button feature. It is blocked on
capability #3, App Intents — and once #3 ships, Action Button availability is a
consequence, not a further task.

### Control Center controls are blocked on a version floor

A Control Center control is a `ControlWidget`, which requires **iOS 18.0** and a
widget extension target. The app floor is **16.1** — raised from 15.1 and applied
on 2026-09-19 (`IPHONEOS_DEPLOYMENT_TARGET = 16.1` appears four times in
`ios/PulseSoc.xcodeproj/project.pbxproj`, and 15.1 appears zero times; see
`DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`).

18.0 is a further two major versions. The deployment-target decision was made on
measured session data showing nothing below iOS 18 in native use, so the *data*
would arguably support an 18.0 floor — but that decision was taken deliberately
and conservatively at 16.1, and reopening it for a Control Center control, which
the audit scores as low user value, inverts the cost of the two.

There is a second, smaller block: a `ControlWidget` also needs App Intents,
because a control's action **is** an App Intent. So #14 is blocked on #3 twice
over, by two independent routes.

### Recommendation, unchanged

Deprioritise. Revisit after App Intents ships. Do not create a widget extension
target for this.

### The one thing worth doing now, which is free

When App Intents (#3) is designed, the intents should be chosen so that at least
one is a sensible Action Button assignment — a single-tap, no-argument,
immediately-useful action. "Start Pulse Radio", "Go live", "New post". An intent
designed only for Siri phrasing or Spotlight often takes parameters and is a poor
Action Button target, and discovering that afterwards means designing the intent
twice. This costs nothing at design time and is the entire Action Button
integration.

---

## Interaction with the protection locks

Recorded explicitly, because this capability sits next to audio.

- `modules/pulse-now-playing/**` — **not** in
  `config/realtime-audio-protected-paths.json`. Verified against the manifest.
- `src/core/pulseRadio.ts`, `src/native/nowPlayingBridge.ts`,
  `src/core/mediaPlaybackCoordinator.ts` — also not in the manifest. But the
  mission brief locks radio playback and ownership arbitration by name, and the
  brief is the stricter document. Treat them as locked.
- The realtime-audio gate was run against this commit and reported no protected
  path changed.
- Nothing in this commit modifies an audio path. The only production-adjacent
  file added is a test.

`pulseRadio.ts` does call `Audio.setAudioModeAsync` (`configureAudio()`), which
is what puts it on the six-file `expo-av` legacy allowlist. That call is
untouched and this document is not a licence to add a seventh.

---

## What is owed

| Item | Where | Priority |
|---|---|---|
| Decide the fix for finding 1 (stale paused entry during a live stream) | `pulseRadio.ts` / live surfaces — **locked, needs a deliberate change** | Medium — user-visible |
| `OnDestroy` removing the command targets (finding 3) | `PulseNowPlayingModule.swift` | Low — dev-only today |
| Set `MPNowPlayingInfoCenter.playbackState` explicitly (finding 5) | `PulseNowPlayingModule.swift` | Low |
| Expose `isEnabled` toggling for next/previous (finding 2) | module + bridge | Low |
| Choose one App Intent that works as an Action Button assignment | capability #3 design | Free, do it then |
| Device validation of the lock screen | iPhone 16 Pro | **Owed** |

---

## What was verified, and what was not

**Verified by reading, at `795c51ab`:** the full Swift module (159 lines) including
every `addTarget` and both merge/guard behaviours; `index.js`, `index.d.ts`,
`expo-module.config.json` and the podspec; the presence of `PulseNowPlaying` in
`ios/Podfile.lock` and the `node_modules` symlink; `nowPlayingBridge.ts` in full;
the remote-command switch and every engine entry point it reaches in
`pulseRadio.ts`; the complete `mediaPlaybackCoordinator.ts` including the priority
table and the refusal branch; every `claimMediaPlayback` call site in `src/`;
every `pushNowPlayingInfo` call site, which established the single-writer fact;
the deployment target in `project.pbxproj`; and the protected-path manifest.

**Verified by execution:** the new remote-command suite, 12 tests, and the four
radio suites together, 46 tests, all green — plus the two-point mutation that
turned exactly the three expected tests red and no others.

**Not verified.** No device test was performed, and for a capability whose entire
output is pixels on a lock screen that is a real limitation:

- **Nothing was observed on an actual lock screen.** That the title, artist,
  artwork and scrubber render as intended — and that the eight glyphs appear —
  is read from the `MPNowPlayingInfoCenter` contract, not seen.
- **Finding 1 was reasoned, not reproduced.** The sequence (radio playing → open
  a live stream → lock) follows from the priority table, the background-retention
  set and the `pausePulseRadio(false)` code path. It has not been performed on an
  iPhone 16 Pro. It should be, before the fix is designed, because the exact
  visual end state determines which of the three fixes is right.
- **No Control Centre, CarPlay, AirPods or Apple Watch interaction was tested.**
  These share `MPRemoteCommandCenter` but each renders a different subset, and
  the "skip is drawn but the queue has ended" case (finding 2) may look worse on
  some than others.
- **The duplicate-registration hazard (finding 3) was not reproduced.** It is
  inferred from the singleton plus the discarded handles, not from watching a
  reload double a `next`.
- **Nothing about Action Button or Control Center was tested at all**, because
  neither exists to test. Part 2 is a reading of Apple's requirements against
  this app's floor and dependency graph.
