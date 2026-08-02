# Real-time audio change policy

The audio foundation was physically heard working — audio call, video-call
audio, and livestream audio, all audible to a person on a device — at the commit
recorded in `reports/realtime_audio_verified_baseline.md`. Everything in this
document exists to keep that true, and to make it recoverable when it stops
being true.

Read this before editing anything listed in
`config/realtime-audio-protected-paths.json`.

## The rule for unrelated missions

**A mission whose subject is not real-time audio must not edit a protected
path.**

Named examples, because these are the missions that historically reach into
audio by accident: Marketplace, Advertising, Premium, Crypto, Profile, Feed,
Settings, Search, and general UI work.

This is not a bureaucratic preference. The way audio breaks in this codebase is
not a broken audio change; it is an unrelated screen calling
`Audio.setAudioModeAsync` or `AVAudioSession.setCategory` and taking the session
away from a live call. The build stays green, the types check, the tests pass,
and the symptom is silence in production.

### The exception, and all five conditions it requires

An unrelated mission may edit a protected path only when **all** of the
following hold:

1. The change is strictly required to complete the mission. "It was convenient"
   means the change belongs outside the boundary instead.
2. `reports/realtime_audio_change_declaration.md` is written for this specific
   change, in this specific pull request.
3. The critical audio suite and both architecture suites pass.
4. Physical audible regression testing is performed and recorded in
   `reports/realtime_audio_verified_baseline.md`.
5. A CODEOWNERS reviewer for the protected path approves.

If you cannot satisfy all five, restructure the change so it does not touch a
protected path. That is almost always possible, and it is almost always the
better design — see the extension points below.

## What is protected

`config/realtime-audio-protected-paths.json` is the authoritative list. It is
not documentation: it is read by `scripts/realtime_audio_change_gate.py`,
`tests/protection/test_realtime_audio_architecture.py`, and
`mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts`. Editing it
changes what CI enforces, which is why the manifest is itself protected.

The categories are: the shared audio-session coordinator, ownership arbitration,
microphone track creation and publication, remote subscription and viewer
playback, output routing, interruption recovery, the call adapter, the
livestream adapter, audio telemetry, the audio feature flags, backend token and
room policy, the tests that encode the invariants, and the lock's own files.

`bot.py` is protected by *content*, not by path. It is a single very large
module, and protecting the whole file would force an audio declaration onto
every backend change anywhere in the product — the kind of over-broad rule
developers learn to route around. The gate treats a `bot.py` diff as protected
only when the changed lines match one of the patterns in
`backend_diff_patterns`.

## Safe extension points

These do **not** require touching the boundary, and are the right shape for new
audio-adjacent work:

- **A new output policy** — expressed through the existing route controller in
  the coordinator, not by calling `overrideOutputAudioPort` from a screen.
- **Route telemetry** — added to `realtimeAudioTelemetry.ts` /
  `liveAudioTelemetry.ts`, which already carry hashed identifiers and
  correlation IDs.
- **Additional interruption recovery** — extend `liveAudioRecovery.ts`'s
  classification; the reconnect, backoff, and terminal-state logic already
  exists to be built on.
- **Improved noise suppression** and **improved echo cancellation** — capture
  options applied through the single publisher.
- **Additional participant mixing** — remote track handling through the existing
  subscription reconciliation pass.

Each of these is an argument or a policy passed to something that already owns
the resource. None of them creates a second owner.

## Forbidden patterns

These are forbidden regardless of who is making the change or why. Several are
enforced by the architecture tests; all of them are review-blocking.

- **Screen-level `AVAudioSession` setup.** The device has one audio session. A
  screen that configures it can silence a call that is already running.
- **A new unmanaged microphone track.** Publication must be serialized per room
  and confirmed by event. A direct `createLocalAudioTrack` races the publisher
  and produces two microphone tracks — heard as an echo or as silence depending
  on which one the server keeps.
- **A new feature-specific LiveKit publication path.** There is one publisher.
  A second one cannot see the first one's in-flight state.
- **A new global audio singleton.** Two coordinators are worse than one bad one,
  because the failure depends on ordering and is not reproducible.
- **Bypassing ownership arbitration.** `resetRealtimeAudioOwnership` skips
  generation checking, so a delayed caller can release a session a newer feature
  has since acquired. Release by lease.
- **Copying the audio-call implementation into another screen.** This is the
  most tempting one and the most damaging. The copy will not receive the next
  fix, and the two copies will fight for the same session.

## The `expo-av` legacy allowlist

Six files called `Audio.setAudioModeAsync` at the verified baseline:
`core/pulseRadio.ts`, `core/reelsAudioSession.ts`,
`core/voiceMessagePlayback.ts`, `calls/callSignalMedia.ts`,
`screens/MusicScreen.tsx`, and `screens/ChatScreen.tsx`.

They are **frozen**, not fixed. Rewriting them would change runtime behavior
that is currently verified working, which this hard-lock is not permitted to do.
The allowlist is capped at six entries and a seventh call site fails CI. New
media playback must route through one of the six.

## The workflow for a legitimate audio change

1. Run the gate locally before you open the pull request:
   `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`
2. Fill in `reports/realtime_audio_change_declaration.md`. Remove the
   `TEMPLATE-NOT-YET-FILLED` marker, name every protected file you changed, and
   answer the physical-validation table honestly — "not required" with a
   one-line reason is a valid answer for rows your change cannot affect.
3. Apply the `audio-critical-change` label.
4. Run the required validation listed in
   `docs/realtime_audio_release_checklist.md`.
5. Perform the physical validation you declared, on a real device, and record
   the result in `reports/realtime_audio_verified_baseline.md` — not in the
   declaration.
6. Get a CODEOWNERS approval.

## What automation can and cannot prove

The tests prove the invariants hold in a simulator: one microphone owner, one
track, one publication, publication after connection, no viewer publication, a
stale lease cannot deactivate a newer session, cleanup is idempotent.

They cannot prove a human heard anything. Every layer of this lock is built
around that gap. The declaration exists because the author is the only one who
can close it, and the baseline document exists because the record of who closed
it, on what hardware, is the only thing that makes a rollback decision possible
later.
