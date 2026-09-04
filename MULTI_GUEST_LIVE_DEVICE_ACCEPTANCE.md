# Multi-guest Live — physical device acceptance script

Stages 50–53 of the multi-guest livestream mission, plus the evidence Stage 54
requires before the real-time audio hard lock may be re-baselined.

**Status: BLOCKED — NOT OBSERVED.** No physical device gate in this document has
been executed. This is a script to be run, not a record of a run. Every gate
below is currently `NOT OBSERVED`, and that is a different thing from `FAIL`:
nothing has been shown to be broken, and nothing has been shown to work.

---

## Why an automated suite is not enough here

The automated work in this mission proves the *decisions* are right. It runs the
real modules over simulated sessions and checks that a guest joining never
produces a rejoin, that the roster never doubles a person, that the stage stays
host-featured at thirteen people. Those are the failures that are hard to see on
a device and easy to see in a test.

It cannot prove that sound comes out of a phone.

Two specific reasons, both structural rather than incidental:

1. **The Agora SDK is not loadable under this repo's Jest.**
   `useAgoraLiveBroadcastRoom` reaches the SDK through
   `await import("react-native-agora")`, and `babel-preset-expo` under
   `jest-expo` does not transpile a dynamic import, so the call throws
   `ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING_FLAG` before any test assertion runs.
   The hook's entire `connect()` path — the code that actually creates the
   engine, sets the client role, configures the encoder and publishes — has no
   executable test coverage anywhere in this repository. Its *wiring* is pinned
   by source-text assertions in `tests/protection/`; its *behaviour* is pinned by
   this document and nothing else.

2. **Audio is a device property, not a code property.** One `AVAudioSession`, one
   microphone, one speaker, and an echo canceller whose behaviour depends on the
   physical distance between two handsets. The failure mode this mission's audio
   rules exist to prevent — a second engine stealing the session — produces green
   tests and silent phones.

So the gates below are the other half of the proof, and the mission's definition
of done is not met until they are executed and reported individually.

---

## Required hardware

Three simultaneously available physical devices. Simulators do not satisfy any
gate in this document: the iOS simulator does not have a real microphone, does
not run the real `AVAudioSession` category negotiation, and does not exercise
the hardware encoder.

| Role | Requirement |
|---|---|
| Device A — Host | Physical iPhone, real camera and microphone |
| Device B — Guest 1 | Physical iPhone, real camera and microphone |
| Device C — Guest 2 / Viewer | Physical iPhone or iPad, real camera and microphone |

Three distinct PulseSoc accounts, none of which block any other. Devices A and B
must be in **separate rooms or wearing headphones** for the audio gates — two
handsets on one desk will produce feedback that is a property of the room, not of
the software, and will make an otherwise passing gate look like a failure.

Enumerate what is actually available before starting:

```
xcrun devicectl list devices
```

If fewer than three devices show `available`, stop and record the gates as
`NOT OBSERVED` with the device count, exactly as
`reports/pulsesoc_live_three_device_acceptance_2026-07-21.md` did. Do not
substitute a simulator and do not report a partial run as a pass.

## Build under test

```
cd mobile-native
npm ci
npx expo run:ios --device --configuration Release
```

**Do not run `expo prebuild`.** This project's `ios/` directory carries native
customisations — the Hermes build fix and the LiveKit `AVAudioSession` patch,
both applied by `patch-package` — that a prebuild discards. Losing the audio
session patch would invalidate every audio gate below in a way that is not
obvious from the app's behaviour until a call and a Live overlap.

Record before starting: branch, HEAD commit, `ios.buildNumber`, and the server
values of `MULTI_GUEST_LIVE_ENABLED`, `LIVE_GUEST_REQUESTS_ENABLED` and
`LIVE_MAX_GUESTS`. A gate result without the flag state it ran under is not
evidence of anything.

---

## The gates

Each gate is reported on its own line with its own verdict. **They must not be
collapsed into a single "Live works".** The whole point of separating them is
that the interesting outcome of this mission is a partial pass — for example,
video correct and audio one-directional — and a single summary verdict destroys
exactly that information.

Verdicts are `PASS`, `FAIL` or `NOT OBSERVED`. There is no `PASS WITH NOTES`.

---

### Stage 50 — single-host Live, unchanged

The regression gate. This runs first, because if multi-guest work has damaged the
ordinary Live then nothing after it matters.

| Gate | What to do | Pass condition |
|---|---|---|
| 50.1 | Device A starts a Live. Device C watches. | C sees A's video within 5 seconds and hears A speak. |
| 50.2 | A speaks for 60 seconds continuously. | No dropout, no echo, no restart. C's audio is continuous. |
| 50.3 | A switches camera front↔rear twice. | Video continues on C. Audio is not interrupted. |
| 50.4 | A leaves the app for 20 seconds and returns. | The Live is still running. C never saw it end. |
| 50.5 | A ends the Live. | C sees it end. A replay reel appears in the feed within 5 minutes. |

Set `MULTI_GUEST_LIVE_ENABLED=false` on the server and repeat 50.1 and 50.2. A
single-host Live must be **identical** with the flag off. This is Stage 41's
property and it is the one an operator will rely on during an incident.

| Gate | What to do | Pass condition |
|---|---|---|
| 50.6 | Flag off. A starts a Live, C watches. | Indistinguishable from 50.1. No error, no degraded mode. |

---

### Stage 51 — host plus one guest

| Gate | What to do | Pass condition |
|---|---|---|
| 51.1 | A is live, C watching. A invites B. | B receives the invite prompt within 5 seconds, exactly once. |
| 51.2 | B accepts. | B shows a local preview *before* B's tile is visible to anyone else. |
| 51.3 | B appears on stage. | A, B and C all see two tiles. B's tile shows video, never a black rectangle. |
| 51.4 | **A speaks.** | B hears A. C hears A. |
| 51.5 | **B speaks.** | A hears B. C hears B. |
| 51.6 | A and B speak alternately for 60s. | No echo on either device. No dropout. |
| 51.7 | Watch A's video on C throughout 51.1–51.6. | **A's stream never froze, blacked out or restarted at any point.** |
| 51.8 | Check A's tile size on all three devices. | A is visibly larger than B. The stage does not read as a two-person call. |
| 51.9 | B leaves the stage. | **The Live continues.** A is still broadcasting, C is still watching. |
| 51.10 | A ends the Live. | It ends for everyone. The replay reel shows both A and B tiled. |

Gate 51.7 is the mission's central claim and deserves a deliberate observer:
someone watching device C's screen continuously across the whole sequence, whose
only job is to say whether the picture ever went away.

---

### Stage 52 — host plus two guests

| Gate | What to do | Pass condition |
|---|---|---|
| 52.1 | A is live with B on stage. A invites C. C accepts. | Three tiles on all three devices. |
| 52.2 | The full audibility matrix, six directed pairs. | Each of A→B, A→C, B→A, B→C, C→A, C→B is audible. Check each individually. |
| 52.3 | All three speak in turn. | The active-speaker highlight follows the speaker and **does not move the tiles**. |
| 52.4 | Observe B's tile while C joins. | B does not flicker, re-mount or change position. |
| 52.5 | A's tile at three publishers. | Still the featured, full-width tile. |
| 52.6 | A mutes B from the moderation sheet. | B is silent to A and C. B's own microphone indicator shows muted. C is unaffected. |
| 52.7 | A removes C from the stage. | C becomes a viewer. C's camera and microphone stop. A and B are undisturbed. |
| 52.8 | B leaves. | **The Live continues with A alone.** The stage returns to full-bleed solo. |

Gate 52.2 must be recorded as six separate results. "Audio works" is not a
result; the failure this catches is one-directional.

---

### Stage 53 — chaos and endurance

| Gate | What to do | Pass condition |
|---|---|---|
| 53.1 | B and C join and leave repeatedly, ten cycles, in varied order. | A's broadcast never restarts. No duplicate tiles. No ghost participants. |
| 53.2 | Put B in airplane mode for 20 seconds, then restore. | B rejoins as **one** participant, not two. A and C see one B. |
| 53.3 | Put A in airplane mode for 20 seconds, then restore. | The Live survives the grace period. C is not told the Live ended. |
| 53.4 | Put A in airplane mode for longer than the 90-second grace period. | The Live ends cleanly. No stuck session, no orphaned recording. |
| 53.5 | C joins a Live that already has A and B on stage. | C immediately sees both, in the same arrangement A and B see. |
| 53.6 | Post comments throughout every join and leave above. | The comment stream never clears, never duplicates and never scrolls backwards. |
| 53.7 | Send reactions from C while C is a viewer. | Reactions appear for everyone. Being a viewer does not restrict them. |
| 53.8 | Watch the viewer count across all joins and leaves. | It counts viewers, does not double, does not go negative, does not include stage members twice. |
| 53.9 | Run a Live with three publishers for 30 continuous minutes. | No thermal shutdown, no audio drift, no memory growth that ends the session. |
| 53.10 | Start a Live, then receive an incoming PulseSoc audio call on device A. | The two do not fight over the audio session. Whichever wins, the other fails **cleanly and audibly**, not silently. |
| 53.11 | Start screen share during a three-publisher Live. | Screen share works and the other publishers are unaffected. |
| 53.12 | Start the Live music mix during a three-publisher Live. | Music is heard by viewers, and the publishers are still heard over it. |

Gate 53.10 is the one that most directly tests this mission's audio rules against
the pre-existing call system. It is also the gate most likely to fail, because it
is the only place where two subsystems contend for the single `AVAudioSession`.
A clean audible failure is a pass. A silent phone is not.

---

### Stage 54 — the hard lock re-baseline

**Precondition: every gate in Stages 50–53 reports `PASS`.**

The real-time audio hard lock (`config/realtime-audio-protected-paths.json`)
records a `verified_commit` — a commit at which the audio path was *physically*
demonstrated to work. Advancing that pointer is a claim that someone put phones
in a room and heard sound.

**This mission has not advanced it, and must not, until the gates above are run.**
The baseline still points at `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`
(2026-08-02). Moving it on the strength of a passing automated suite would
convert "we tested the decisions" into "we verified the audio", which is the
precise misrepresentation the hard-lock mechanism exists to prevent.

When the gates do pass, the re-baseline is:

1. Write `reports/realtime_audio_change_declaration.md` with all eight required
   sections, naming the gates that were run and the devices they ran on.
2. Update `baseline.verified_commit`, `baseline.last_audio_commit`,
   `baseline.snapshot_tag` and `baseline.verified_date` in
   `config/realtime-audio-protected-paths.json`.
3. Record the run in `reports/realtime_audio_verified_baseline.md`, section 7.
4. Re-run `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.

---

## Reporting template

Copy this into the acceptance report. One line per gate. Fill in every line —
an omitted gate is `NOT OBSERVED`, not an implied pass.

```
Branch:            <branch>
HEAD:              <commit>
Build number:      <ios.buildNumber>
Devices:           A=<model/iOS>  B=<model/iOS>  C=<model/iOS>
Server flags:      MULTI_GUEST_LIVE_ENABLED=<>  LIVE_GUEST_REQUESTS_ENABLED=<>  LIVE_MAX_GUESTS=<>
Date:              <date>
Observers:         <names>

50.1  <PASS|FAIL|NOT OBSERVED>  <note>
50.2  ...
...
53.12 ...

Audibility matrix (52.2), six directed pairs:
  A -> B  <PASS|FAIL|NOT OBSERVED>
  A -> C  ...
  B -> A  ...
  B -> C  ...
  C -> A  ...
  C -> B  ...

Stage 54 re-baseline performed:  <YES|NO>   (NO unless every gate above is PASS)
```

---

## What a failure here means

If a gate fails, the mission is not done, regardless of how many automated tests
pass. The automated suite and this document test different things and neither
substitutes for the other:

- An automated failure with device passes means a test encodes the wrong rule.
- A device failure with automated passes means the rules are right and the
  wiring — almost certainly inside `useAgoraLiveBroadcastRoom.connect()`, the
  one function with no executable coverage — is wrong.

The second is the likelier outcome, and it is why the gates are written to
isolate *which* direction of *which* pair failed rather than to produce a
verdict.
