# Consolidation follow-up register — 2026-09-04

Everything this consolidation found and deliberately did **not** fix, with the
reason it was left. The point of writing them down is that a follow-up nobody
recorded is indistinguishable from a defect nobody noticed.

Each entry says what it is, why it was out of scope for a release closeout, and
what it would take to discharge. Nothing here is a blocker for the release that
shipped as `a4226e29`; if it were, it would have been fixed rather than filed.

Ordering is by consequence, not by discovery.

## 1. Production Messenger has no database-level idempotency guarantee

**Found:** by running the installer's own `pg_index` query against production.
The unique index `idx_comm_v2_messages_client_idem` is **absent**; 11 duplicate
`(conversation_id, sender_user_id, client_message_id)` groups — 17 excess rows —
block its creation.

**Why it was left:** resolving it means deleting production message rows, and
which copy survives is a judgement call — the right row may be the one carrying
replies, reactions or a read receipt. `scripts/messenger_idempotency_audit.py` is
read-only *by design* for exactly this reason, and overriding that design from a
release closeout would be the wrong call twice over.

**Not caused by this release.** The installer first ships in `689a0e45` (today);
the duplicates span 2026-07-29 to 2026-09-03. The degraded state is newly
*visible*, not newly true.

**To discharge:** a human picks a survivor per group, removes the rest, then
restarts the service so the installer retries. Confirm by re-running the probe
and seeing `hard_uniqueness_active: true`. Full detail and row ids in
`reports/production_probe_20260904.md`.

**Meanwhile:** the send path is still correct — lookup plus a conflict-safe
insert. Only the guarantee under a true race is missing.

## 2. The physical real-time-audio validation matrix is still undischarged

This is the largest outstanding item and the one most likely to be
mis-remembered as done. The audio work in this release is covered by static
gates, a protected-path manifest and several hundred tests — none of which can
hear anything. No test in this repository can tell you a track was audible on a
real handset.

**To discharge:** the matrix in `reports/realtime_audio_change_declaration.md`,
run on hardware. The release-range addendum states the position plainly and
should be left stating it: *"This addendum discharges nothing: it names files,
it does not put a phone in anyone's hand."*

## 3. The superseded LiveKit-era audio core is still in HEAD and unreachable

`useLiveBroadcastRoom.ts`, `realtimeAudioEngine.ts` and the rest of the Class-D
set — roughly 265 tests — are not reachable from the app entry point. RTC is
Agora-only.

**Why it was left:** deleting an entire subsystem is not a closeout-sized change,
and it would land in the same push as the release, making that diff much harder
to read. Removal is tracked in `reports/agora_audio_test_relevance_audit.md`.

**Care required:** the tests passing is not evidence the code is used. That is
the whole finding.

## 4. `useAgoraLiveBroadcastRoom` is not directly testable

Its Agora SDK import cannot be resolved under the current Jest config, so the
hook is covered only indirectly through extracted pure modules.

**To discharge:** either `--experimental-vm-modules` or dependency injection of
the SDK client. The second is preferable — it makes the seam explicit rather
than making the test runner clever.

## 5. Multi-guest Live is OFF, and the audit assumed it

`MULTI_GUEST_LIVE_ENABLED` defaults to `False` and is `false` in
`.env.example`. Several newly protected modules are unreachable *only* because
of that flag — not because of anything structural.

**To discharge when the flag flips:** re-run the audio relevance audit. Modules
currently classed unreachable become reachable, and the reachability half of the
declaration stops being true. Note the subtlety recorded during Item 18: the
flag gates *seating a guest*, not the modules themselves, so a few of them are
already reachable on a solo-host Live (`publisherVideoProfile` is; the music
restoration path is not).

## 6. `isLocalQaSession()` permits plaintext fallback on a physical device

The session-store fallback is gated on the API base URL pointing at
`127.0.0.1`/`localhost`, which permits it on a *physical* device pointed at a
LAN host — something the previous `Device.isDevice` gate did not.

The new suite `sessionStoreQaFallback.test.ts` documents this rather than
asserting it away, which is the right call: it is a debug configuration.

**To discharge:** a decision, not a code change — unless adhoc builds ever ship
pointed at a LAN host, at which point it becomes a real hole.

## 7. The rescued XCUITest is a reference, not a test

`qa/ios-uitests/PulseSocNativeCameraStudioQATests.swift` — 271 lines, recovered
from the stash reflog where it was the only copy — is not wired to any Xcode
target and does not run in CI.

**To discharge:** two things, both real work. Add a UI-testing target that
survives `expo prebuild` regenerating `ios/`, and re-check the accessibility
labels — it matches English strings (`"Feed"`, `"Snap"`, `"Flip"`, `"Publish"`)
and the app has since been fully localized, so on a non-English device every
query misses. Matching on testIDs would fix that properly.

## 8. `ensure_schema(conn)` can hang a route on PostgreSQL

Passing a route's connection skips the commit, so the DDL rolls back; a second
connection then blocks on the uncommitted catalog lock. Roughly 26 call sites
remain unswept.

**Why it was left:** it is a pre-existing backend hazard, unrelated to this
release's scope, and sweeping 26 call sites is its own mission with its own
regression risk.

## 9. Root-level mission writeups (~70 files) are not organised

Traceability artefacts sitting at the repository root.

**Why it was left:** moving them is a ~70-file rename that would land in the
same push as the release and make its diff materially harder to read — and this
consolidation exists to *protect* traceability artefacts, not to shuffle them
under time pressure.

## 10. There is no checked-in `ExportOptions.plist` for App Store upload

The iOS project rename (`f8fa3e6c`, `PulseSocNative` → `PulseSoc`) deleted
`mobile-native/ios/ExportOptions-AppStore.plist` and did not recreate it under
the new name. HEAD has no `ExportOptions` file anywhere.

This is a sibling of item 7 — the same rename orphaned the XCUITest — and HEAD's
own `realtime_audio_change_declaration.md` already names both casualties, calling
this one "a release-packaging concern". It is recorded here because a concern
noted inside a report about something else is easy to lose.

**Impact:** nothing at runtime; `xcodebuild -exportArchive` for an App Store
Connect upload has no checked-in options file. The device and simulator builds in
`reports/release_builds_and_qa_20260904.md` do not use it and were unaffected.

**To discharge:** restore it from `f8fa3e6c^` — nine lines, `destination:
upload`, `method: app-store-connect`, `signingStyle: automatic`, `teamID:
87ZC69AGSR`, `uploadSymbols: true` — under `mobile-native/ios/`, and confirm the
team ID still matches. Deliberately not restored here: it is a release-packaging
change, and inventing one during a closeout that is not itself doing an App Store
upload would ship an untested build input.

## 11. Smaller items

| Item | Note |
|---|---|
| `liveSession.ts:72` names `parseMediaQualityFlags` in a comment | The module was deleted by `f93e7ce3 chore(rtc): fire LiveKit`. Prose, not an import — nothing breaks, but the comment describes a normalisation step that no longer exists. |
| `tests/briefings/test_pulse_briefings.py` declares `TopicIndependenceTests` twice (~lines 512, 589) | The second shadows the first, so one class never runs. Real coverage loss, small blast radius. |
| The idempotency audit script cannot run under `railway run` | It imports `bot`, which refuses to boot without a stable `FLASK_SECRET_KEY` when it detects a deployed environment. The guard is correct; the read-only path should not need the `PULSESOC_ALLOW_EPHEMERAL_SECRET=1` opt-out. |
| Forbidden-API marker redesign | The current marker scheme is positional and brittle. |
| Private Office security-table writer boundary | Worth an explicit statement of who may write. |
| `capabilityRegistry.ts` `secure_storage` owner_module | Unowned entry. |
| 61 `.fuse_hidden*` files in the shared `~/Desktop/CoinPilotX` checkout | Gitignored, untracked, invisible to every gate. Deliberately not deleted: by construction some process still holds them open, and that checkout is shared with other sessions. |
| 12 local branches are provable ancestors of HEAD | Safe to delete, kept anyway — see the branch-cleanup section of the final report. |

## Decisions still owed by a human

1. **Decision 8** — owner visual acceptance of the five Premium tiles on P3r7or.
2. **Which duplicate message survives**, per group, for item 1 above.
3. **Whether adhoc builds may ever point at a LAN host**, for item 6.
