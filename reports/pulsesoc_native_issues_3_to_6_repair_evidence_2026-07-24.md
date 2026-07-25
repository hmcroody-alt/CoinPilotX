# PulseSoc Native — Issues 3–6 Repair Evidence (2026-07-24)

Honest completion report for the multi-issue repair mission. This documents what
was actually observed, and marks each item PASS / PARTIAL / NOT OBSERVED strictly
by evidence. Automated evidence (Jest + `tsc`) was run in this environment.
Physical-device and two-user media validation was **not** run here because this
sandbox has no Xcode, simulator, or device.

## Commit & branch

- Branch: `release/undx-nexus-core-v4`
- New commit: **`481bb211d85eb3e3cb5b0c27311f8d0898db9686`**
  - `fix(mobile): repair messaging latency, in-app banner, live guest audio, and call tones`
  - 19 files changed, 1070 insertions(+), 47 deletions(-)
- Also unpushed on this branch: `e5f566d4` (Issues 1 & 2, feed/composer).
- Relationship to remote: `origin/release/undx-nexus-core-v4` is at `a307b506`.
  Local is **2 ahead, 0 behind**, and the origin tip is an ancestor of local →
  a clean **fast-forward** push (no divergence, no rebase needed).

## Push status — BLOCKED from this environment (must be pushed from your Mac)

The commit is in your real local repository (this session's `.git` is the actual
`.git` under `/Users/hmcherie/Desktop/CoinPilotX`). The **push** could not be
completed here because the sandbox network proxy forbids GitHub egress:

- SSH (`git@github.com:22`): `socat E CONNECT github.com:22: Forbidden` → `fatal: Could not read from remote repository`.
- HTTPS (`https://github.com/...:443`): `Received HTTP code 403 from proxy after CONNECT`.
- No credential helper and no `GH_TOKEN`/`GITHUB_TOKEN` present in the sandbox.

Both transports are blocked at the proxy, so the push cannot originate from here.
To publish, run from your Mac (which has the SSH keys and network):

```
cd ~/Desktop/CoinPilotX
git push origin release/undx-nexus-core-v4
```

This will fast-forward origin from `a307b506` to `481bb211` (2 commits).

## Git lock — root-caused and resolved without deleting an active lock

Stale, 0-byte `index.lock` and `HEAD.lock` (~3.5h old, no owning process per
`lsof`/`fuser`/`ps`) were blocking writes. Root cause: the sandbox FUSE/virtiofs
mount **denies `unlink()`/`rmdir()`** (`rm` → `Operation not permitted`) but
**permits `rename()`**. Resolution: renamed the confirmed-stale locks aside
(`index.lock` → `index.lock.stale`), which unblocked git. The commit itself then
succeeded because git writes objects to a temp file and **renames** them into
place; the only residual noise is `warning: unable to unlink '.git/objects/**/tmp_obj_*'`
(the temp cleanup fails, but each object is correctly stored — `git fsck` reports
no missing or broken objects).

## Root causes and fixes

### Issue 3 — chat message bubble delayed after Send  →  code PASS
Root cause: the send path `await`ed `sendTyping(conversationId, false)` — a full
network round-trip — *before* `sendPayload` inserted the optimistic bubble, so the
bubble appeared only after the typing signal returned.
Fix: the typing signal is fire-and-forget (`void sendTyping(...).catch(...)`) so the
optimistic bubble inserts synchronously. Message de-duplication/ordering was
extracted into a pure `mergeConversationMessages` module.
Tests: `src/api/__tests__/messengerOrdering.test.ts`.

### Issue 4 — in-app notification banner won't auto-dismiss / double banner  →  code PASS
Root cause: the OS notification handler returned `shouldShowAlert/Banner: true`,
so a foreground notification stacked the OS heads-up banner on top of the app's own
banner; there was also no dedicated auto-dismiss controller.
Fix: added `InAppNotificationBanner` driven by a pure `notificationBannerLifecycle`
controller (auto-dismiss timer + queue), and suppressed the OS foreground banner
(`shouldShowAlert/Banner: false`) while keeping list, sound, and badge so the
notification still lands in Notification Center. Background notifications unaffected.
Tests: `src/navigation/__tests__/notificationBannerLifecycle.test.ts`.

### Issue 5 — Live host inaudible + guest join fails  →  code PASS / real-media PARTIAL
Two distinct defects:
- **Viewer remote-audio mute leak (host "inaudible" class):** when a viewer muted
  and then a new remote audio track was subscribed, or the room reconnected, the new
  track played at full volume — the mute state didn't stick. Fix: `applyRemoteAudioEnabled`
  is reapplied on `TrackSubscribed` and `Reconnected`, backed by `remoteAudioEnabledRef`,
  driving `track.setEnabled` (SDK path) or `mediaStreamTrack.enabled` (fallback) on
  every subscribed remote audio track. Test: `src/live/__tests__/remoteAudioReapply.test.ts`.
- **Guest join / silent on-stage bubble:** co-host connect wasn't gated on a token
  that both grants publish **and** is bound to a real guest slot; a viewer/partial
  token could connect and appear on stage without publishing. Fix: `canConnectAsCohostPublisher`
  gates connect (requires `canPublish`, positive `guestId`, and token+url), so the
  client rejects a publish-incapable/unbound token with a clear error instead of a
  silent bubble. Test: `src/live/__tests__/cohostPublishGate.test.ts`.
- Backend traced end-to-end (`bot.py`): the token route grants `can_publish` only
  after a `pulse_live_guests` row exists (host-gated accept), and every mobile
  endpoint (request / cancel / status / token / publish-complete) has a handler. No
  additional backend defect was found; the observed failure mode is a
  publish-incapable/unbound token, now rejected client-side.

### Issue 6 — audio/video calls + ringback/ringtone  →  code PASS / real-media PARTIAL
- **Predicate correctness:** extracted `callToneLifecycle` — ringback plays only on
  the outgoing side before media connects; ringtone only on the recipient side;
  every terminal status stops tone. Test: `src/calls/__tests__/callToneLifecycle.test.ts`.
- **Reentrancy leak:** `startCallTone`'s async `Audio.Sound.createAsync` could leak a
  second, orphaned looping `Sound` if a stop or a newer start landed mid-load. Fix:
  a monotonic `toneGeneration` guard — a superseded in-flight load stops+unloads
  itself. Test: `src/calls/__tests__/callSignalMediaReentrancy.test.ts`.
  - **Regression caught by that test:** the first version of the guard captured the
    generation *before* the internal `stopCallTone()` bumped the counter, so every
    normal tone start looked stale and unloaded itself → total silence. The new test
    exposed it; fixed by claiming the generation *after* `stopCallTone()`.

## Files changed in `481bb211` (19)

App.tsx; src/api/push.ts; src/api/messengerOrdering.ts (+test);
src/screens/ChatScreen.tsx; src/components/InAppNotificationBanner.tsx;
src/navigation/notificationBannerLifecycle.ts (+test);
src/live/liveSession.ts; src/live/useLiveBroadcastRoom.ts;
src/live/__tests__/cohostPublishGate.test.ts; src/live/__tests__/remoteAudioReapply.test.ts;
src/screens/LiveScreen.tsx; src/calls/IncomingCallLayer.tsx;
src/calls/callSignalMedia.ts; src/calls/callToneLifecycle.ts (+test);
src/calls/__tests__/callSignalMediaReentrancy.test.ts; src/screens/CallScreen.tsx.

Deliberately **excluded** from this commit (unrelated to Issues 1–6): `.env.example`,
`services/pulse_ai_provider_router.py` (UNDX self-hosted candidate), the security
findings ledger, `mobile-native/store.config.json`, `mobile-native/store/`, `.claude/`,
and the UNDX Stage-0 inventory report. These remain uncommitted in the working tree.

## Automated verification (run in this environment)

- `npx tsc --noEmit` → **EXIT 0** (clean).
- `npx jest` (full suite) → **47 suites / 439 tests PASS**, clean exit.

## Explicit status matrix

| Issue | Code fix | Automated tests | Real device / two-user media |
|-------|----------|-----------------|------------------------------|
| 3 — chat bubble latency | PASS | PASS | NOT OBSERVED |
| 4 — in-app banner auto-dismiss | PASS | PASS | NOT OBSERVED |
| 5 — live host audio + guest join | PASS | PASS | **NOT OBSERVED → overall PARTIAL** |
| 6 — calls + ringback/ringtone | PASS | PASS | **NOT OBSERVED → overall PARTIAL** |

## Remaining risks / what is NOT proven here

- **No hardware in this sandbox** (no Xcode/simulator/device), so live host↔viewer
  audio, guest publish on stage, and call audio + ringback/ringtone playback have
  **not** been observed on a real device. Issues 5 and 6 must be validated with
  host + guest + viewer iPhones and a real two-user call before marking PASS.
- The mute-stick fix relies on LiveKit track objects exposing `setEnabled` or
  `mediaStreamTrack`; verified against fakes, not the live SDK on a device.
- The push is unpushed pending a run from your Mac (sandbox proxy blocks GitHub).
- `P3r7or` was **not** re-flashed/updated in this session (no device toolchain here);
  device install + QA remains an owner action after the push.
