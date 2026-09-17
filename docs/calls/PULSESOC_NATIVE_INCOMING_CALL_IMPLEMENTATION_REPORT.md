# PulseSoc Native Incoming Call — Implementation Report

**Status: PARTIAL — IMPLEMENTATION AND PHYSICAL DEVICE VERIFICATION INCOMPLETE**

Reporting commit: `7d2f0fc8` — `calls: settle call state by compare-and-set so one
answer wins`.

This report states what was changed, what was proven, and what was not. It does not
claim the incoming-call objective passed.

---

## 1. What was changed

One commit, two files, 57 insertions / 6 deletions.

| File | Change |
| --- | --- |
| `services/pulsesoc_communications_engine.py` | Compare-and-set in `_transition()`; CAS-loss handling in `accept_call`, `admin_force_end_call`, `_mark_missed_stale_calls_cur`, `_expire_stale_active_calls_cur` |
| `tests/test_call_accept_race.py` | New and rewritten tests proving the defect and the fix |

No schema change. No new environment variable. No new dependency. No new route.

### 1.1 The defect

`_transition()` was the single writer of `communication_calls.status`, but it wrote
against an in-memory snapshot taken by an earlier `SELECT`:

```sql
UPDATE communication_calls SET ... WHERE id = ?
```

No status predicate, no `rowcount` check. A read-modify-write race: any writer that
validated against a stale snapshot overwrote whoever had moved the row on in between.

### 1.2 The fix

```python
values.append(int(call["id"]))
values.append(current)
cur.execute(
    f"UPDATE communication_calls SET {', '.join(updates)} "
    f"WHERE id=? AND COALESCE(status,'created')=?",
    values,
)
if getattr(cur, "rowcount", -1) == 0:
    return _err("This call has already moved on.", 409, "transition_conflict",
                from_status=current, to_status=new_status)
```

The early return precedes `_event()` and `_voip_stop_ringing()`, so a losing writer
records no timeline event and sends no VoIP cancel.

Under PostgreSQL READ COMMITTED this is what converts last-writer-wins into
first-writer-wins: a blocked `UPDATE` re-evaluates its `WHERE` against the committed
row version (EvalPlanQual), and a predicate on `id` alone always passes that recheck.

### 1.3 Call-site handling

| Call site | On CAS loss |
| --- | --- |
| `accept_call` | Idempotent — returns a token off current state, but suppresses the duplicate `call_accepted` and the duplicate answered-elsewhere fan-out. Terminal status → `409 call_final`. |
| `_mark_missed_stale_calls_cur` | `continue` |
| `_expire_stale_active_calls_cur` | `continue` |
| `admin_force_end_call` | Maps to the existing "Call was already final." contract |
| `decline_call`, `end_call` | Unchanged by design — their per-participant bookkeeping is correct regardless of who wins the call-level teardown |

Suppressing the duplicate fan-out is load-bearing rather than cosmetic:
`_voip_stop_ringing()` excludes the answering device id, so a second emission carries a
*different* device id and cancels the CallKit ring on the device that actually won.

### 1.4 Severity finding

The two sweepers are **not** background workers. `Procfile` runs `web`, `undx_worker`,
`email_worker`, `ads_worker`, `alert_worker`, `media_worker`, `supplier_worker` — no
call sweeper. Both sweepers execute inline inside `call_status()`
(engine:1897) and `active_calls()` (engine:1914).

`call_status()` serves `GET /api/calls/<id>/status`, which the client polls every
**700 ms while ringing** (`mobile-native/src/calls/callSessionStore.ts:58`).

So the sweeper ran several times per second across 4 gunicorn workers × 8 threads
during exactly the ringing window in which an accept arrives. The pre-fix race was a
routinely reachable production path requiring only one device, not a rare multi-device
edge case.

---

## 2. Verified results

All figures below were produced by running the suites, not by inspection.

| Suite | Result |
| --- | --- |
| `tests/test_call_accept_race.py` | 4 passed, 1 skipped |
| `tests/test_call_acceptance_sync.py` | 10 passed |
| Multi-guest call suite | 22 passed |
| Two-sided hangup suite | 6 passed |
| VoIP PushKit delivery suite | 40 passed |
| UNDX call domain | 25 passed (102 subtests) |
| UNDX call guard | 23 passed |
| **Call / VoIP total** | **130 passed** |
| `scripts/protection/run_protection_suite.py` | **673 checks / 44 suites passed** |
| `scripts/realtime_audio_change_gate.py` | **passed**, 2 files inspected, clean |

### 2.1 Anti-vacuity discipline

Every guard was re-run against the pre-fix code:

- Both negative tests **fail** without the fix.
- Both positive controls **pass** with and without the fix, confirming they are not
  passing merely because the loop rejects everything.

`SweeperStompsAnsweredCallTest` was converted from a permanently-skipped
PostgreSQL-only test into real SQLite coverage by injecting the competing write on the
sweeper's own cursor mid-batch — `fetchall()` materialises the batch, so the loop keeps
iterating stale dicts while the table changes underneath it. This is both deterministic
and a more faithful model of the defect: the loop trusts rows it read before doing
seconds of work.

### 2.2 Preserved foundations

Confirmed unchanged:

- **Agora** remains the sole RTC provider; one engine
  (`callSessionStore.ts:605`); `_generate_agora_token()` untouched; App Certificate
  never leaves the server.
- **Mux** untouched, retaining its livestream / replay / VOD role. It appears nowhere
  in the call path.
- **Realtime-audio coordinator** unmodified. No second `AVAudioSession` manager, no
  second microphone track, no second publication path, no screen-level session setup.
- `config/realtime-audio-protected-paths.json:185` protects only
  `services/pulsesoc_communications_engine.py#generate_agora_live_token`; the edits are
  outside it, and the gate confirmed clean.

---

## 3. Not verified

The following are open. None of them is closed by anything in §2.

### 3.1 Real PostgreSQL two-device concurrency proof — BLOCKED

`AcceptRaceTest` (two genuinely concurrent threads racing an accept under PostgreSQL
MVCC) remains `skipUnless(DATABASE_URL.startswith("postgres"))` and is **skipped**.

Why it is blocked on the verification machine:

- Docker Desktop's VM will not boot (GUI processes present and the socket file exists,
  so `ls` and `pgrep` both report healthy, but `docker info` / `ps` / `version` hang
  indefinitely; a background restart poll timed out).
- No Homebrew PostgreSQL formula and no `initdb` / `pg_ctl` binaries.
- `pip install pgserver` fails — no wheel for the only Python present (3.14).

Consequence: the SQLite coverage proves the **snapshot-staleness** half of the defect.
It does not prove the **two-writers-reach-the-UPDATE-simultaneously** half. On SQLite a
second connection cannot produce that interleaving at all — the sweeper holds an open
write transaction for the whole batch, so a competing connection dies with `database is
locked` before the interleaving occurs. That is a property of the harness, not evidence
about the engine.

The EvalPlanQual reasoning in §1.2 is therefore an argument from PostgreSQL's
documented behaviour, **not** an observed result.

### 3.2 Call-creation rate limiting — NOT IMPLEMENTED

`POST /api/calls/start` is authenticated (`_require_user()`) but carries no observed
per-user or per-IP rate limit. A signed-in account can originate calls without
throttling. Not implemented, not tested.

### 3.3 Environment-aware `aps-environment` — IMPLEMENTED, HALF VERIFIED

Superseded by `b3d3c4e2`. The entitlement is no longer a literal: it expands
`$(PULSESOC_APS_ENVIRONMENT)`, declared per build configuration — `development` for
Debug, `production` for Release. `apns-topic` is likewise derived per device from the
`app_bundle` recorded at registration rather than from one deployment-wide value.

What was proven on hardware (iPhone 16 Pro, Release, development-signed):

```text
application-identifier   87ZC69AGSR.com.pulsesoc.app
aps-environment          development
```

The build setting substitutes and the product signs. What was **not** proven, and why
it cannot be from here: under `CODE_SIGN_STYLE = Automatic` Xcode rewrites
`aps-environment` from the selected provisioning profile, so the entitlements file is
advisory on a local build. A Release build with no override — with
`ProcessProductPackaging` confirmed to have actually re-run, the first attempt having
silently reused a stale `.xcent` — still produced `development`. The wiring is
authoritative only under manual signing, which is the path EAS takes for preview and
store builds, and exercising it needs a distribution profile this machine does not
have.

So the literal is gone and the configuration is now capable of expressing both
environments, but **no artefact carrying `aps-environment: production` has been
observed**. The backend's per-token host correction remains the thing actually keeping
development-signed devices working.

### 3.4 Physical iPhone 16 Pro verification — NOT PERFORMED

None of the following has been exercised on hardware:

- Lock-screen presentation
- Terminated-app delivery
- Backgrounded / suspended delivery
- Silent Mode and Focus behaviour
- Native Answer / Decline from the CallKit UI
- AirPods / Bluetooth routing
- Ring teardown on cancel, timeout, and answered-elsewhere
- Absence of a stale "Active call" banner

The simulator cannot substitute. It receives no APNs pushes, and the ad-hoc signing
required for Agora strips associated-domains and the team id, so entitlement-gated
behaviour fails there regardless of what the server does.

### 3.5 Merge and production deployment — DONE

Authorized and completed after this report was first written. `7d2f0fc8` is on `main`,
followed by the docs commits `b15f4a7a` and `edd3bfbb`. Railway auto-deployed and
`railway status --json` reported `commitHash: b15f4a7a86d5599219b6a8acd2ef6b5bd56cc4d3`;
the root path answered 200 and `/api/calls/capabilities` answered 401, which is the
correct auth gate rather than a boot failure. Production runs the compare-and-set fix.

### 3.6 No sweeper worker

Stale-call cleanup depends entirely on an inbound request hitting `call_status()` or
`active_calls()`. A call whose participants all disappear is not swept until an
unrelated request happens to sweep it. This predates `7d2f0fc8` and is unchanged by it.

---

## 4. Documentation produced

| File | Contents |
| --- | --- |
| `docs/calls/PULSESOC_NATIVE_INCOMING_CALL_AUDIT.md` | Where the system lives; route inventory; the defect, its consequences and why it was a hot path; VoIP transport; preserved foundations; verified results; unverified items |
| `docs/calls/PULSESOC_NATIVE_INCOMING_CALL_ARCHITECTURE.md` | Layer map; ownership boundaries; state machine and CAS semantics; incoming-call flow; ring teardown; push transport; iOS delivery contract; client call layer; audio ownership; configuration surface (names only) |
| `docs/calls/PULSESOC_NATIVE_INCOMING_CALL_RUNBOOK.md` | Pre-deployment checklist; deployment; post-deployment verification; seven troubleshooting scenarios; three-lever rollback; diagnostic reference |
| `docs/calls/PULSESOC_NATIVE_INCOMING_CALL_IMPLEMENTATION_REPORT.md` | This document |

No document contains a secret value. Environment variables are named only.

---

## 5. Rollback

Three independent levers, detailed in the runbook:

1. `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=0` at client build time — disables the native
   CallKit presentation path. A flag flip, not a revert.
2. Unset `APNS_VOIP_BUNDLE_ID` / `APNS_BUNDLE_ID` — `is_configured()` goes false and
   every VoIP send short-circuits. Emergency stop only.
3. `git revert 7d2f0fc8` — two files, no schema, no data repair. Restores the
   read-modify-write race.

None of the three touches Agora, Mux, the audio coordinator, reels, radio or voice
messages.

---

## 6. Blockers

### Technical

| Blocker | Impact |
| --- | --- |
| No PostgreSQL available locally | The two-thread accept race test cannot run; the concurrency half of the fix is argued, not observed |
| No physical iPhone 16 Pro verification | Lock screen, terminated app, Silent Mode, Focus, Bluetooth routing all unverified |
| No Apple Distribution profile | The `production` entitlement cannot be observed in a signed artefact; only the `development` half is proven |
| No dev/prod bundle split | Both configurations build `com.pulsesoc.app`; `app_bundle` is never reported by the client, so per-device topic derivation is inert |
| No call-creation rate limit | `/api/calls/start` is unthrottled |
| No sweeper worker | Stale-call cleanup is request-driven and can stall indefinitely |

### Deployment

| Blocker | Status |
| --- | --- |
| Push of `7d2f0fc8` | Done |
| Merge to `main` | Done |
| Production deploy | Done — Railway on `b15f4a7a` |
| Registering `com.pulsesoc.nativeapp.dev` as an explicit App ID with Push | Not required — one bundle id was chosen; see below |
| Apple Distribution signing | Absent; blocks any `production`-entitlement artefact |

**Bundle identifier: decided.** Both environments stay on `com.pulsesoc.app`, varying
only `aps-environment` by configuration. This removes the last item that needed Apple
Developer account access, and it matches what the committed Xcode project already
built — the split existed only in `app.config.js` (prebuild-only, inert while `ios/`
is committed) and in the development install script.

The decision is now enforced rather than described.
`tests/protection/test_ios_push_bundle_identity.py` fails if either configuration
drifts to a second bundle id, which is exactly what an `expo prebuild` would
reintroduce on its own. The gate matters because the failure it prevents is
irreversible per device: a token minted under an unaddressed bundle draws
`DeviceTokenNotForTopic`, and `services/pulsesoc_voip_push.py` revokes on that without
replaying — correctly, since unlike `BadDeviceToken` it cannot be a host mismatch. A
wrong host costs one request; a wrong topic costs the handset its ability to ring.

The development install script now builds `com.pulsesoc.app` as well. It had built
`com.pulsesoc.nativeapp.dev` and refused the production id outright, which kept a
development build installable beside the App Store one — that was traded away
knowingly, because a build under any other bundle cannot receive a VoIP push at all,
and the device used to test calls is the worst possible device to make unringable. The
install now replaces an App Store or TestFlight build on that handset (an upgrade in
place; the container survives) and the script warns before building.

Two smaller findings came out of making that change:

* `PULSESOC_DISPLAY_NAME` was referenced by `Info.plist` and defined by no
  configuration, so `CFBundleDisplayName` expanded to the empty string and iOS
  backfilled it from `CFBundleName`. Exactly the `aps-environment` failure shape —
  a plist expanding a build setting nothing declares, silent because an undefined
  setting is not an error. It only stopped being cosmetic when the two builds started
  sharing a bundle id, since the display name became the sole on-device distinction.
  Now declared in both configurations and gated.
* A development-signed build mints a *sandbox* PushKit token while the deployment
  addresses the production host, so the first push draws `BadDeviceToken` and falls to
  §6's one-shot replay. **An earlier revision of this document claimed that replay
  succeeds and the correction is paid once per device. Direct measurement against APNs
  disproved it** — and the claim was doing real harm, because it explained away as
  "designed" the exact symptom it should have escalated:

  | host | topic | result |
  |---|---|---|
  | sandbox | `com.pulsesoc.app.voip` | **403 `BadEnvironmentKeyInToken`** |
  | production | `com.pulsesoc.app.voip` | 400 `BadDeviceToken` |

  The APNs auth key is restricted to the production environment, so the sandbox host
  refuses the *request* — 403, faulting the key — before it ever evaluates the token.
  The replay cannot succeed for any development-signed build. Worse, the sender read
  "both hosts refused" as proof of a dead token and revoked it; alert-push suppression
  is conditioned on an *active* VoIP token, so revoking it silently downgraded the
  handset from CallKit to an ordinary banner. The token was never dead — iOS returned
  the identical hash after every revocation.

  Fixed by distinguishing *refused* from *inconclusive*: a replay that never reached a
  verdict on the token now degrades to `failed`, never `invalid_device`, and
  `voip_push_rejected` carries `replay_reason` so this is legible from logs without a
  hand-written probe. The push still will not arrive on a dev-signed build. That needs
  a `production`-signed one — which `Release` already is, so TestFlight rings today and
  this failure is specific to locally dev-signed installs.

---

## 7. Conclusion

A real, frequently reachable concurrency defect in the call state machine was proven
by test and fixed by compare-and-set, with 130 call/VoIP tests, a 673-check protection
suite and the realtime-audio gate all green, and with the Agora, Mux and audio-session
foundations demonstrably untouched.

That is not the same as the incoming-call objective passing. The concurrency proof is
partial, the APNs environment configuration is unresolved, and no verification has
taken place on physical hardware. The status of this work is:

**PARTIAL — IMPLEMENTATION AND PHYSICAL DEVICE VERIFICATION INCOMPLETE**
