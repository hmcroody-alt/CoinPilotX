# Real-Time Audio — Live Test Matrix

Mission phase 10. This is the procedure that decides whether the Live broadcast
fix actually works. Everything upstream of it — 2,820 Jest tests, 200 protection
checks, a clean typecheck — establishes that nothing regressed in code. None of
it establishes that a human hears audio.

That gap is not a formality. The defect this matrix exists to catch produced a
green build, passing tests, and a broadcast that terminated with *"The native
real-time audio engine did not remain active."* The failing mechanism —
AVAudioSession going inactive during a camera transition, with no
interruption-ended event, so an ADM restart silently no-ops — has no software
model. It is a property of iOS audio hardware arbitration.

**A simulator run is not evidence for any row in this document.** The iOS
Simulator does not reproduce AVAudioSession arbitration, RemoteIO teardown on
camera transition, Bluetooth route changes, or the interruption semantics of a
real phone call. A simulator pass here means only that the app launched.

---

## What you need

Two physical iPhones, both on a build produced from the commit under test. One
is the **host**, one is the **viewer** — the whole point is that a second human
confirms sound arriving, because the host's own device can appear healthy while
publishing silence. A Bluetooth headset and a wired headset for the routing
rows. Two networks if you can manage it (one device on Wi-Fi, one on cellular),
because NAT relay behaviour differs and a TURN-less deployment fails only for
some paths.

Console.app attached to the host device, filtered to `PulseSocRealtimeAudio`.
The telemetry sink logs at error level deliberately — the comment at
`src/core/realtimeAudioTelemetry.ts:72` explains that engine-state events must
reach the device syslog to reveal exactly when the native record engine stops
after camera startup. That log is your primary evidence, and you should capture
it for every failing row.

Record for each row: pass/fail, the device models, the iOS versions, the build
number, and the timestamp. A row marked "pass" with no capture is not a result;
it is a memory.

---

## Group A — the incident itself

These are the rows that were failing. If any of them fails, the fix did not
work and nothing below matters.

| # | Scenario | Pass criterion |
|---|---|---|
| A1 | Host starts a Live broadcast, camera on, from a cold app launch | Broadcast starts. Viewer hears the host within 5 seconds. No `engine did not remain active` error |
| A2 | Same, but the app has been running for several minutes first | Identical to A1 — the fix must not depend on cold-start timing |
| A3 | Host toggles the camera **off** mid-broadcast, then back **on** | Viewer's audio never drops. This is the exact transition that left the session inactive |
| A4 | Host switches front → rear camera mid-broadcast, then back | Audio survives both switches |
| A5 | Two consecutive five-minute broadcasts, no app restart between them | Second broadcast starts and is audible. Session state must not leak between broadcasts |
| A6 | Host ends the broadcast and immediately starts another | No stuck session; audio present in the second |

A3 is the load-bearing row. The recovery added at `stabilizeAudio` sweeps four
passes across an asynchronous teardown window whose exact timing varies
run-to-run, so **run A3 at least five times**. A fix that works four times out
of five is a fix that will fail in front of an audience.

---

## Group B — the fail-closed invariant

The change adds a non-throwing recovery stage *before* the authoritative guard.
The guard still runs last and still throws. That ordering is the thing that
stops a silent broadcast from being reported as healthy, and it needs its own
verification — otherwise the "fix" could simply be a suppressed error.

| # | Scenario | Pass criterion |
|---|---|---|
| B1 | Deny microphone permission, then start a broadcast | Broadcast fails with a clear error. It must **not** report success |
| B2 | Revoke microphone permission in Settings while broadcasting | Broadcast ends or clearly signals failure; it must not continue reporting a healthy publish |

If B1 or B2 shows a "live" broadcast that publishes nothing, the recovery stage
is swallowing a genuine failure and the change must be reverted regardless of
how well Group A performed.

---

## Group C — audio routing

| # | Scenario | Pass criterion |
|---|---|---|
| C1 | Connect Bluetooth headset before starting a broadcast | Host hears through the headset; viewer hears the host |
| C2 | Connect Bluetooth headset **during** a broadcast | Route switches; audio continues both directions |
| C3 | Disconnect Bluetooth mid-broadcast | Falls back to speaker; audio continues |
| C4 | Wired headset connect / disconnect mid-broadcast | Same as C2/C3 |
| C5 | Speaker ↔ earpiece toggle | Route changes; no drop |

---

## Group D — interruptions

| # | Scenario | Pass criterion |
|---|---|---|
| D1 | Real incoming phone call during a broadcast, declined | Broadcast audio resumes after decline |
| D2 | Real incoming phone call, accepted then ended | Broadcast recovers or fails clearly — it must not sit in a silent "live" state |
| D3 | Background the app, wait 30 s, foreground | Audio resumes. `UIBackgroundModes` includes `audio`, so this should hold |
| D4 | Lock the screen for 60 s, unlock | Audio continues throughout |
| D5 | Timer/alarm fires during a broadcast | Audio ducks and recovers |

D2 is the one most likely to surface a variant of the original defect, since a
real call takes the session away far more aggressively than the camera does.

---

## Group E — no collateral damage

The audio session is shared. The mission's own policy names the characteristic
failure: an unrelated screen configures the session and steals it from a live
call. These rows confirm the change did not do that.

| # | Scenario | Pass criterion |
|---|---|---|
| E1 | One-to-one audio call, both directions | Both parties hear each other |
| E2 | One-to-one video call, both directions | Audio and video both work |
| E3 | Audio call → end → start a Live broadcast | Broadcast audible; no leftover session state |
| E4 | Live broadcast → end → start an audio call | Call audible |
| E5 | Play a reel, then start a broadcast | Broadcast audible; reel audio stopped cleanly |
| E6 | Record a status/story video, then start a broadcast | Broadcast audible |
| E7 | Co-host joins a broadcast | Host, co-host and viewer all hear each other |
| E8 | Viewer-side playback with the app backgrounded | Viewer continues to hear the host |

---

## Group F — network conditions

| # | Scenario | Pass criterion |
|---|---|---|
| F1 | Host on cellular, viewer on Wi-Fi | Audio arrives |
| F2 | Host switches Wi-Fi → cellular mid-broadcast | Reconnects; audio resumes |
| F3 | Both devices behind restrictive NAT (e.g. two different mobile networks) | Audio arrives. **This is the row that tests TURN.** A failure here is a `TURN_SERVER_URL` gap, not an audio-engine defect — see `docs/provider_api_purchase_report.md` §2 |
| F4 | Airplane mode on for 10 s mid-broadcast, then off | Reconnects or fails clearly |

F3 deserves a note: it will pass on any normal office network whether or not
TURN is configured, which is precisely why it has to be run deliberately on two
separate mobile networks. A missing TURN relay does not break calls in testing —
it breaks them for a fraction of real users, silently.

---

## Sign-off

The change may ship when Group A and Group B pass on two physical iPhones,
Group A row A3 has been run five times without a failure, and Group E shows no
regression in calls. Groups C, D and F should be run, and any failure recorded,
but a C or F failure is diagnostic information about routing and infrastructure
rather than a blocker on this specific change.

Record results as a new file under `reports/`, dated, naming the exact commit
SHA, device models and iOS versions. `reports/realtime_audio_verified_baseline.md`
section 7 is the format to follow — it is the record of the last time audio was
physically heard working, and it is what the whole protection scheme is
anchored to.

Anything not physically run should be written down as not run. The reason this
matrix exists at all is that a green signal which no change could turn red is
worse than no signal.
