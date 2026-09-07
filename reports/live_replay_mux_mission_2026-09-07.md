# PulseSoc replay repair — 2026-09-07

Status: implemented locally; production acceptance remains blocked. No deployment,
production data modification, public test content, or broadcast interruption was performed.

## Workspace and baseline

- Branch: `main`; starting HEAD: `6cab70a43cc07b4bb394659c93231d1fb7ecb744`.
- Inspected Git status and worktrees. No applicable AGENTS.md files found.
- Preserved unrelated `.scratch_locks/`, `CAPITAL_INTEGRITY_HANDOFF.md`, and
  `tests/test_pulsesoc_call_livekit_grants.py`.
- Baseline: 42 targeted Python tests passed; native typecheck passed; PostCard's
  22 tests passed. An initial Jest invocation used the wrong path for the end-flow
  suite; the corrected invocation passed. This was a command-selection error.

## Confirmed findings

1. The backlog scan selected the oldest 25 sessions before excluding existing
   jobs/completed publications. Ready sessions were requeued indefinitely.
   Production logs repeatedly processed the same old live IDs seconds apart.
   Read-only production SQL counted 85,762 done replay jobs and 79 failed jobs.
2. A recording playback ID was treated as readiness in end/archive/playback code.
   Mux can assign a recording playback ID while its asset is still live.
3. Ended playback could fall back to the old live ID, HLS URL, and RTC room.
4. Delayed connected/disconnected webhooks could change an ended session's status,
   causing the replay worker to skip it. Completion webhooks did not enqueue recovery.
5. Native `record_replay` was sent but not persisted or enforced. Publication lacked
   an independent saved preference check, and private replays notified followers.
6. Mux `recent_asset_ids[0]` selected the oldest listed recording. The latest is last.
   The disable helper used PATCH rather than the documented PUT disable endpoint.
7. A crash/lost response after Mux creation could create another asset on retry.
   A lost Agora stop response had no recovery through the finalized storage manifest.
8. Feed processing cards did not refresh themselves. Ended LiveScreen could still
   prefer RTC instead of replay HLS.

## Production recording evidence

- Web and media-worker active deployments both reported SUCCESS at SHA
  `6cab70a43cc07b4bb394659c93231d1fb7ecb744`.
- Mux webhook `sv1p97` is enabled at
  `https://coinpilotx.app/api/pulse/live/mux/webhook` in the credentials' environment.
  Configuration presence was verified; a newly delivered signed production event
  was not observed in this run.
- Live 292 ended at `2026-09-07T14:53:15` UTC. Its recording playlist was last
  modified at 14:53:25, and its Mux input playlist at 14:53:27.
- Mux asset `JxiKC02lX3e4EbrYiYaVbxzdCEdKxv5pWrcZOdEtc02qk` is `errored`,
  error type `invalid_input`: “The input file was not a valid video or audio file.”
  The database still called the replay `processing_replay`.
- Lives 285–291 also remained in `mux_retryable` with recording asset IDs.
- The original recording and stored signed Mux input both passed an actual ffprobe
  read: H.264 720×1280, AAC, duration **40.703 seconds**. Three TS objects were
  readable, with sizes 5,724,600; 5,392,216; and 134,044 bytes.
- **40.703 seconds is recording duration, not end-to-ready latency.** No successful
  end-to-playable time was measured. The source is currently readable; the precise
  cause of Mux's earlier rejection is not yet established.
- Asset creation now uses the current documented structured `inputs` and
  `playback_policies` fields. Its effect on this production rejection requires a
  controlled provider retry; no successful Mux ingestion is claimed.

## Implemented changes

- Persist recording and publishing preferences; enforce them at bootstrap,
  finalization, backlog recovery and publication. Recording-off sessions do not
  start Agora recording or publish replays. Mux-native ingest inherently records
  broadcasts; its recording-off preference suppresses application publication.
- Require confirmed ready, non-live Mux assets with playback information; keep
  preparing assets queued without spending failure attempts or failing for age.
- Check pending jobs and completed publications before backlog LIMIT, preserve
  restart recovery, and use slower five-minute provider checks for delayed assets.
- Commit a creation intent with a stable passthrough marker; recover ambiguous
  creation via bounded asset discovery or correlated webhooks. Never automatically
  repeat an ambiguous asset POST. If the request never reached Mux, support review
  is required to resolve the uncertain intent safely.
- Recover a finalized Agora manifest from private storage after a lost stop response;
  wait for ENDLIST before ingesting a recording.
- Make repeated end requests idempotent, preserve ended states on late webhooks,
  and enqueue durable reconciliation from readiness/completion/error events.
- Retain canonical feed/reel publication and its unique identity. Private publication
  suppresses follower notifications. The state endpoint now applies audience access.
- Use signed Mux policy for new private/followers/save-only recordings and refresh
  viewer tokens. No unsigned fallback when signing is unavailable.
- Add a host-only retry endpoint: preserve preparing assets; replacement is allowed
  only after Mux confirms an error and an original recording filename exists.
- Refresh visible processing cards after network errors and app foregrounding.
  Show two-minute and five-minute delay messages; expose host retry/check-status UI.
  Refresh signed replay information periodically and on foregrounding.
- Emit `LIVE_REPLAY_PLAYABLE` with seconds since live end for actual timing.

## Changed files

- `bot.py`, `media_worker.py`.
- `services/mux_live_service.py`, `services/agora_cloud_recording_service.py`,
  `services/live_archive_service.py`, `services/live_distribution_service.py`,
  `services/pulse_feed_engine.py`.
- `mobile-native/src/api/live.ts`, `mobile-native/src/components/PostCard.tsx`,
  `mobile-native/src/screens/LiveScreen.tsx`.
- Replay lifecycle/worker/manifest/recording tests and PostCard tests.
- `scripts/replay_readonly_diagnostic.py` reproduces storage/provider evidence without
  importing bot, modifying production, or printing credentials/signed source URLs.

## Verification and release gate

- Targeted Python suite covers readiness, duplicate/out-of-order webhook behavior,
  missed webhook polling, worker restarts, ambiguous creation, consent, private
  signing, retry authorization, and publication visibility. Final counts are in the
  handoff response.
- Native typecheck passed; 56 relevant native tests passed.
- All 191 critical audio/call/live regression tests passed (11 suites).
- No physical playback acceptance: P3r7or is paired and available, but this run did
  not install a build or observe replay playback.
- `MUX_SIGNING_KEY_ID` and `MUX_SIGNING_PRIVATE_KEY` are absent from the production
  media-worker variables. Configure signing on both web and worker before private
  replay rollout. Existing public-policy private assets require a separate migration
  review; this patch does not revoke existing provider playback IDs.
- New schema fields are additive: `record_replay`, `replay_publish_enabled`,
  `replay_retry_key`. Both web initialization and worker startup add them.
- Deployment requires explicit authorization under the mission brief. Following
  authorized deployment, perform a controlled retry/fresh private recording, observe
  Mux ready plus finalized playback ID, measure actual latency, and verify device
  playback, consent, visibility, and exactly-once publication before acceptance.

## Official references consulted

- https://www.mux.com/docs/core/listen-for-webhooks
- https://www.mux.com/docs/core/verify-webhook-signatures
- https://www.mux.com/docs/guides/start-live-streaming
- https://www.mux.com/docs/guides/stream-recordings-of-live-streams
- https://www.mux.com/docs/api-reference/video/live-streams
- https://github.com/muxinc/mux-node-sdk/blob/master/src/resources/video/assets.ts
