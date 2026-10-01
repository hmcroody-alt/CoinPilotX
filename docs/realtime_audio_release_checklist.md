# Real-time audio release checklist

## When this checklist applies

**It does not apply to most releases.** Run it only when the release contains a
change to an audio-sensitive path. Deciding that by hand is unreliable, so let
the tooling decide:

```bash
python3 scripts/realtime_audio_change_gate.py --base <last-released-tag> --head HEAD
```

- Exit 0 with *"No protected real-time audio path changed"* → **this checklist
  is not required.** Ship normally. Attaching an audio checklist to releases
  that cannot affect audio is how checklists become rubber stamps.
- Any protected hit → work through everything below.

The gate also flags `mobile-native/package.json`, `package-lock.json`,
`app.json`, `eas.json`, `ios/Podfile`, `ios/Podfile.lock`, and the React Native
patch, because a lockfile refresh can move the media stack with no code diff at
all — the failure mode with the fewest visible symptoms.

## 1. Automated validation

Run all of these and record the actual result lines, not a claim that they
passed.

| Check | Command |
| --- | --- |
| Critical audio suite | `cd mobile-native && npm run test:realtime-audio-critical` |
| Full audio suite | `cd mobile-native && npm run test:realtime-audio` |
| Architecture boundary (native) | `cd mobile-native && npm run test:realtime-audio-architecture` |
| Architecture boundary (backend) | `python3 -m unittest tests.protection.test_realtime_audio_architecture -v` |
| Backend token and room policy | `python3 -m pytest -q tests/protection/test_agora_token_generation.py tests/protection/test_agora_rtc_provider_contract.py tests/protection/test_agora_direct_live_contract.py tests/protection/test_live_guest_authorization.py` |
| Full native suite | `cd mobile-native && npm test` |
| TypeScript | `cd mobile-native && npm run typecheck` |
| Native build | `cd mobile-native && npx expo prebuild --platform ios --no-install`, or an EAS build |

## 2. Build identity

A green test run against the wrong build proves nothing. Record, for the build
you are actually shipping:

- Commit SHA, and confirm local `HEAD` equals the remote ref.
- App version and iOS build number from `mobile-native/app.json`.
- The SHA embedded in the built binary, if the build embeds one.
- Backend deployment identifier and the backend commit it was built from.
- Agora environment: the App ID the build points at, and whether the backend is
  minting tokens with the matching certificate. A build pointed at the wrong
  App ID fails in a way that looks like a permissions bug, not a config one.

If any of these is unknown, write **NOT RECORDED**. Do not write a plausible
value; the whole purpose of the record is that a later rollback decision can
trust it.

## 3. Feature flag state

Confirm the state of each flag in the environment you are releasing to, because
the flag state determines which code path the physical validation below is
actually exercising:

- `LIVESTREAM_AUDIO_V2_ENABLED` (plus `_QA_ONLY`, `_PERCENT`, `_QA_USER_IDS`)
- The client-resolved path: `v1_legacy` or `v2_isolated`

Validating on `v1_legacy` and shipping with `v2_isolated` enabled means you
validated a path you are not shipping.

## 4. Physical audible validation

**This is the part no automation can do.** Every row must be performed by a
person on real hardware — a simulator cannot produce audible evidence.

Do not use "connected", "appeared functional", "seemed fine", or "no errors".
The only acceptable positive result is that a person **heard** the audio.

| # | Surface | Required result |
| --- | --- | --- |
| 1 | Audio call | Speech physically audible in **both** directions |
| 2 | Video call | Speech physically audible in both directions **while video is active** |
| 3 | Livestream viewer | A viewer physically heard the host's live speech |
| 4 | Livestream guest | The host physically heard an approved guest, and the guest heard the host |
| 5 | Route change | Audio remained audible across speaker → receiver → Bluetooth |
| 6 | Interruption recovery | An incoming PSTN call interrupted the session; after it ended, audio was audible again |
| 7 | Mixed session | Call → Live and Live → call in one app run, no restart, audible at each step |
| 8 | Cleanup | After ending each session, a subsequent session acquired audio successfully |

Record: device models, iOS versions, the date, and who performed each row.
Record them in `reports/realtime_audio_verified_baseline.md`, which is the
rollback reference — not in the pull request description, which is not
searchable a year from now.

Rows that this release cannot affect may be marked "not required" with a
one-line reason. That reason is a claim the reviewer can disagree with, which is
the point.

## 5. Telemetry watch after release

Watch these for the first hours after rollout, against the baselines in
`reports/realtime_audio_verified_baseline.md`:

- Room connection success rate
- Microphone publication success rate
- Remote subscription success rate
- Audible-path proxy (non-zero track energy)
- Ownership conflicts rejected
- Duplicate publications prevented
- Audio-session activation failures
- Route-change recoveries
- Cleanup completions

**Rollback threshold:** any of connection success, publication success,
subscription success, or the audible-path proxy falling more than 5 percentage
points below baseline, or audio-session activation failures rising above 1% of
sessions. These thresholds are deliberately blunt — a precise threshold nobody
can evaluate at 2am is worse than a blunt one somebody acts on.

## 6. Rollback

In order of increasing cost:

1. **No app release required:** set `LIVESTREAM_AUDIO_V2_ENABLED=0` server-side.
   This is a real kill switch — the flag is server-driven with no client
   override — but it only covers the livestream audio path.
2. **Backend:** redeploy the previous backend deployment.
3. **Client:** `git checkout realtime-audio-stable-v1` — the tag pointing at the
   last commit whose audio behavior was physically heard working — and rebuild.

After any rollback, confirm it worked by **hearing** audio on rows 1, 2, and 3
above. A rollback verified by a green build is not a verified rollback.

## 7. Sign-off

- [ ] Automated validation complete, results recorded
- [ ] Build identity recorded, unknowns marked NOT RECORDED
- [ ] Flag state recorded and matches what was validated
- [ ] Physical audible validation performed and recorded in the baseline document
- [ ] Telemetry baselines and rollback thresholds understood by whoever is on call
- [ ] Rollback path confirmed available (tag exists, flag reachable, previous deployment retained)
- [ ] `reports/realtime_audio_change_declaration.md` filled in and merged, if a protected path changed

Released by: __________  Date: __________  Commit: __________
