# PulseSoc — Full Backend Operations Recovery + Live Audio Stability

**Final report.** Branch `codex/emergency-live-audio-recovery`, base commit
`ef99f6aab4c7ef85b4a9fb62344270228333d8de`, 5 August 2026.

Every number in this document was measured during the final verification pass, not
recalled. Where a figure is quoted from an earlier run rather than re-measured, it
says so. Where something was not done, it says that too, in its own section, because
a recovery report whose omissions are invisible is the same defect as a dashboard
whose failures are invisible — which is most of what this mission turned out to be
about.

---

## 0. Read this before the next deploy

**`FLASK_SECRET_KEY` must be set in the Railway environment or the web service will
refuse to boot.**

This is a deliberate behaviour change made during this mission. It is the single item
here that can turn a routine deploy into an outage, so it leads the report. Generate
and set a value once:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Section 6 explains why refusing to boot is the correct behaviour. `docs/railway_deployment_runbook.md`
carries the operator-facing version.

---

## 1. What the mission asked, and what it actually found

The instruction was to inspect every backend operations surface, identify what is
wrong, misleading, degraded, stale or unsafe, and repair the underlying systems
rather than the dashboard presentation — with the explicit caution *"do not treat
green dashboard lights as proof."*

That caution turned out to be the finding. The recurring defect across the Operations
Center was not that systems were broken and the dashboard showed it. It was that
systems were broken and the dashboard could not show it, because the indicator was a
constant, an assertion, or a count over an unbounded window. Six independent
instances of the same shape:

| Surface | What it reported | What it measured |
|---|---|---|
| `/health` | `{"ok": true}` | nothing — hard-coded literal |
| Launch readiness registry | feature "verified" | a hand-maintained list |
| Provider readiness rows | six vendors green | environment variables no code reads |
| Department warning counts | a queue | an unbounded archive |
| Four worker backlogs | a queue | an archive nothing drains |
| Backup status | "backups exist" | dumps never restored |

Each of these is repaired below by making the indicator derive from the thing it
claims to describe. That is the whole of the dashboard work: no cosmetic change was
made to any operations screen.

## 2. Live broadcast incident — root cause

Reported symptom: *"Broadcast could not start. The native real-time audio engine did
not remain active."*

Device syslog shows the camera transition leaves the shared `AVAudioSession`
**inactive** (`cmsSetIsActive ... going inactive`), and iOS never delivers
`interruption-ended` while the camera holds the session. An Audio Device Module
restart issued against an inactive session silently no-ops — it returns success and
does nothing.

The existing post-camera guard was single-shot. It retried the ADM restart twice
against a session it could not start into, then threw. So it killed a broadcast whose
microphone track had already been published successfully. The guard was not wrong to
throw; it was wrong to have nothing to throw *after*.

## 3. Live broadcast incident — the fix

`mobile-native/src/live/useLiveBroadcastRoom.ts`, the `stabilizeAudio` callback, now
runs two stages: **recover, then verify.**

Stage 1 is `recoverRealtimeRecordingEngine` — non-throwing, multi-pass, re-activating
the session with a plain `setActive(true)` before each ADM restart, sweeping four
passes across the asynchronous teardown window because the exact moment RemoteIO
stops varies run to run. It is deliberately **not** a category reassert: reasserting
the category disrupts the running WebRTC video pipeline.

Stage 2 is the original authoritative guard, unchanged and still fail-closed. After
recovery has had its chance, if the engine is genuinely dead the broadcast still
fails. A silent broadcast can never be reported as healthy.

The publisher startup *ordering* is identical on the v2 and legacy paths — legacy
publishers were routed through the same call-grade stabilizer in `f385024d`. The
`useV2` flag is retained for telemetry and call-site clarity only, and a protection
test asserts it does not branch behaviour.

## 4. Live audio protection policy compliance

`useLiveBroadcastRoom.ts` is in `config/realtime-audio-protected-paths.json`. The
change gate was run:

```bash
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

It fires on this file, as designed. The declaration required by
`docs/realtime_audio_change_policy.md` is written up in
`reports/realtime_audio_change_declaration.md`.

None of the six categorically forbidden patterns were introduced: no screen-level
`AVAudioSession` setup, no second microphone track, no second LiveKit publication
path, no new global audio singleton, no bypass of ownership arbitration, no copy of
the audio-call implementation into another screen. The `expo-av` legacy allowlist
remains at its cap of six files.

## 5. Live audio test matrix

`docs/realtime_audio_live_test_matrix.md` — the scenario grid (host start, host
rejoin, camera flip mid-broadcast, incoming phone call, background/foreground,
Bluetooth route change, viewer-to-cohost promotion) with the expected engine state at
each transition.

**The physical two-device iPhone pass in this matrix was not run.** See section 27.

## 6. `FLASK_SECRET_KEY` — the highest-severity backend defect found

`bot.py` line 82. The key falls back to `secrets.token_hex(32)` when unset. On a
laptop that is correct. In production it is a silent outage.

The `Procfile` runs `gunicorn --workers 2`, and each worker executes the module
top-level separately, so an unset secret gives the two workers **different keys**.
That key signs Flask sessions *and* the mobile bearer tokens minted in
`issue_mobile_access_token()` and verified with `hmac.compare_digest` on every
`/api/mobile` call. A token minted by worker A fails on worker B.

The user-visible symptom is therefore not "sessions reset on deploy". It is **random
401s and random logouts on roughly half of all requests, indefinitely**, with nothing
in the logs but the auth failures themselves. That is the exact shape of bug that gets
triaged as a mobile-client problem for weeks.

The fix is to refuse to boot in a deployed environment. A deploy that fails loudly is
recoverable in the time it takes to set a variable. `PULSESOC_ALLOW_EPHEMERAL_SECRET=1`
restores the old behaviour for an operator who wants it, but they have to say so.

## 7. `/health` returned a constant

`/health` used to return a hard-coded `"ok": True`. Nothing computed it, so no outage
could falsify it. A web process whose database had gone away answered
`200 {"ok": true}` for as long as gunicorn stayed up, and every monitor watching it
reported a green platform through a total data-layer outage.

## 8. Liveness and readiness are now different questions

- **`/health`** answers *is this process alive* and deliberately stays **200** even
  when the database is unreachable. A platform that restarts a container on a
  transient database blip makes the outage longer, not shorter.
- **`/health/ready`** answers *should this process receive traffic* and returns
  **503** with `database_unreachable` or `route_packs_failed`.

Railway's healthcheck and any uptime monitor should point at **`/health/ready`**.

`/health/ready` is unauthenticated and deliberately does **not** echo the database
error text: SQLAlchemy connection failures embed the full DSN, password included.

The ping behind both is cached for 5 seconds in `services/db.py`, so per-second
probing does not turn the healthcheck into its own load problem. That pressure —
*the check is expensive, let's just return a constant* — is what produced the
hard-coded `True` in the first place, and removing the pressure is the only durable
fix.

## 9. Route-pack registration is now observable

Optional route packs register inside `except Exception` blocks so one broken feature
cannot block boot. The trade-off is that a subsystem can vanish in production without
the deploy failing. `ROUTE_PACK_STATUS` is now populated at registration time and
surfaced through `/health/ready`, so a missing subsystem produces a 503 rather than a
404 that looks like a routing bug.

## 10. Launch readiness registry now verifies rather than asserts

`services/backend_management_registry.py` gained `verify_features()`, `_rule_matcher()`
and `_effective_status()`. Feature readiness is now computed against the **registered
Flask URL rules** and the **existing database tables** passed in, rather than read
from a hand-maintained list. `launch_readiness()` takes `registered_rules` and
`existing_tables` as parameters so it can be exercised in tests and so the production
call site has to supply real evidence.

A feature whose route is not registered, or whose table does not exist, can no longer
be green.

## 11. Provider readiness is derived from configuration

`services/pulsesoc_reliability.py` gained `_requirement_met()`, `_requirement_label()`
and `_configured()`. Provider rows now compute from whether the credential is actually
present, and report *which* key is missing rather than a bare red dot.

## 12. Department warning counts are bounded

`department_counts()` in `bot.py` counted over all time. A count over an unbounded
window is an archive, not a queue: it can only grow, so it conveys no information
about the present and cannot be driven to zero. It is now a rolling window.

## 13. Command center actions are accountable

Every administrative action surfaced in the command center now records who invoked it
and against what. `tests/protection/test_admin_action_accountability.py` (new, 311
lines) locks this: an action route that mutates state without an audit trail fails the
suite.

## 14. Command center metrics query real tables

`backend_command_live_metrics()` and `backend_command_safe_scalar()` were reworked so
that a metric whose query fails reports as unavailable rather than defaulting to `0`.
A zero and a failure are different facts and had been rendered identically.

## 15. Provider / API inventory and the purchase question

`docs/provider_api_purchase_report.md` (195 lines) is the full inventory, with
per-variable behaviour and what happens when each is unset.

The headline finding: the six credentials in section 1 of that report —
`POLYGON_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `REUTERS_API_KEY`,
`AP_NEWS_API_KEY`, `WHALE_ALERT_API_KEY` — are read **only to colour a readiness
row**. No code path calls those vendors. **Buying them turns red rows green and
changes nothing else.** No paid API was purchased, per the mission's boundary, and
none should be until code exists that consumes it.

## 16. Railway variable audit

A plain `grep os.getenv` is unreliable in this repository: environment variables are
read through sixteen wrapper functions (`_env_value`, `_flag`, `subflag_enabled`, and
others), and two modules resolve names at runtime from data structures rather than
literals (`undx_router.PROVIDERS`, and the `required_env` lists in
`pulsesoc_intelligence_engine.py`). That is why earlier "undocumented variable" counts
in this project were wrong.

`scripts/undx_railway_variable_audit.py` (new, 186 lines) covers all three shapes. Its
run earlier in this mission reported **452 variables read, 462 declared in
`.env.example`, 0 undocumented**. The script walks the whole tree and takes several
minutes; it was **not re-run for this report**, and those figures are quoted from that
earlier run rather than re-measured.

`.env.example` gained 457 lines of documentation, including the required-variable
block for `FLASK_SECRET_KEY`.

## 17. Workers deployed vs. workers existing

The `Procfile` runs `web`, `undx_worker` and `email_worker`. Four workers exist in the
repository and are **not** deployed: `alert_worker.py`, `media_worker.py`,
`pulse_worker.py`, `telegram_worker.py`.

This is worth stating plainly because it is invisible from the dashboard: work queued
for those workers is never drained, and any metric counting their backlog counts an
archive rather than a queue — the same defect as section 12. Whether they should run
is a product decision and should be made deliberately rather than by someone noticing
the `Procfile`.

## 18. Backups: none existed

`scripts/ops/backup_database.py` (new, 381 lines) takes a dump **and restores it**.

- `backup_postgres` uses `pg_dump --no-owner --no-acl` and checks the exit status.
- `backup_sqlite` uses the `Connection.backup` online API rather than `cp`, so a
  concurrent writer cannot produce a torn file.
- `verify_postgres` restores into `PULSESOC_BACKUP_VERIFY_URL` and refuses to run if
  that URL equals `DATABASE_URL`.
- `verify_sqlite` runs `PRAGMA integrity_check` **and counts tables and rows**.

Verified live this session: **647 tables, 275,655 rows, exit 0.**

## 19. The empty-database trap, and why table counts are not optional

A gzip of a freshly-created SQLite file is a valid gzip of a valid database. It passes
`PRAGMA integrity_check` perfectly. Only counting tables catches it — and an empty
database is exactly what a misconfigured `DATABASE_URL` produces. So the verification
that would seem sufficient is precisely the verification that misses the failure most
likely to occur.

Injected-fault results, all exercised:

| Injected fault | Exit code | Verdict |
|---|---|---|
| Truncated dump | 2 | verify failed |
| Valid gzip of empty database | 2 | verify failed |
| Good dump | 0 | verified restorable |
| No `PULSESOC_BACKUP_VERIFY_URL` | 3 | backup taken, **restore not verified** |

## 20. "Could not check" is not "checked and fine"

Exit code 3 exists because those are different facts. A script that returns 0 when it
skipped verification is committing the same error as the hard-coded `"ok": True` — it
reports success for work it did not do. `PULSESOC_BACKUP_VERIFY_URL` should point at a
**second, empty** Postgres service; without it the script exits 3 rather than
pretending the dump is known-good.

A bug of my own here is worth recording: the first version of the script let a
truncated-dump `EOFError` escape as a traceback, exiting **1** — the code that means
*the backup could not be taken* — for a run in which the backup was taken fine and the
restore was what failed. An operator reading exit codes would have looked in exactly
the wrong place. `verify()` now converts any exception into a verdict.

## 21. `backups/` was one commit away from being published

The backup mechanism wrote to `backups/`, which was untracked but **not gitignored**.
A dump is a byte-for-byte copy of production — every user row, every message, every
token — and git keeps history, so a later `git rm` does not take it back. Caught with
`git status --porcelain backups/` before any commit. `.gitignore` now excludes
`backups/` and `*.sql.gz`, verified with `git check-ignore -v`.

## 22. UNDX could rewrite its own guards

`undx_execution_kernel.apply_approved_changes()` writes into the working repository
after the phrase `APPROVE UNDX WRITE`. It refused to write outside the repository root
and refused to touch secrets. It did **not** refuse to write to itself.

`PROTECTED_PATTERNS`, `APPROVAL_PHRASE` and the containment check all live in
`undx_execution_kernel.py`, and that file matched none of the patterns. One approved
change to it — arriving in a batch labelled as a refactor, alongside a dozen innocuous
edits — could empty the protected list or blank the approval phrase, and every write
afterwards would be unguarded. The same held for `tests/protection/`,
`scripts/protection/` and `.github/workflows/`: rewriting those makes the safety net
report green without anything being fixed.

## 23. Escalation, not prohibition

The fix is a second, **different** phrase, `APPROVE UNDX GUARD CHANGE`, required for
`SELF_GOVERNING_PATTERNS`. UNDX improving its own guards is legitimate work; a flat ban
would be deleted the first time it was inconvenient. Requiring a distinct phrase means
approving a refactor can never also mean removing the rails, and costs nothing on the
common path.

Verified behaviourally against a temporary repository, not by grepping for the string
that implements it:

- self-rewrite with the write phrase alone → refused
- `services/../undx_execution_kernel.py` traversal → refused (path resolves before matching)
- write phrase replayed as the guard phrase → refused
- ordinary file with only the write phrase → **applies**, `guardPathsChanged == []`
- both phrases → applies, `guardPathsChanged` recorded in `undx_execution_log.jsonl`
- mixed batch without the guard phrase → refused **atomically**, ordinary file unmodified

That last one matters: half-applying a batch leaves an operator reconciling by hand,
mid-incident. The kernel now screens the whole selected batch before writing anything.

## 24. Gemini API key was being written to the application log

`undx_router` fans requests across OpenAI, Claude, Gemini, DeepSeek and Groq, holding
keys server-side so they never reach the browser. That part was right. The logging was
not.

The Gemini call passed its key as `?key=<API_KEY>`. `requests` embeds the full request
URL in the string form of its exceptions, and `route_undx_request` logged `str(exc)` on
every failed provider call. So a Gemini outage — the condition most likely to produce a
burst of failures — wrote the API key into the application log once per request.
Nothing was compromised by the key being in the environment; it was compromised by
being in the log, where log shipping, support bundles and screenshots all reach.

Two independent defences, because either alone is one edit from being undone:

1. The key travels in the `x-goog-api-key` header, so it cannot appear in exception
   text, proxy access logs, or a referer.
2. `_safe_error()` redacts credentials before logging, both by query-parameter name and
   by matching known key values, with an 8-character floor so a trivial value cannot
   redact ordinary words out of every log line, and a 400-character cap.

The second exists because the next provider added to this module will be written by
copying an existing one.

## 25. Protection suite

**239 checks across 15 suites**, up from 205 at the start of this mission. Runner:
`scripts/protection/run_protection_suite.py`. Nine new suites this session:

```
test_admin_action_accountability.py      311 lines
test_environment_contract.py             356
test_operations_metric_truthfulness.py   312
test_backend_registry_verification.py    252
test_undx_kernel_guard.py                238
test_backup_and_secret_integrity.py      188
test_undx_router_credentials.py          177
test_protection_suite_integrity.py       113
_runner.py                                75
```

Every new lock was explicitly tested for its ability to go **red** — a protection test
that cannot fail is another hard-coded `True`.

## 26. A trap in static protection tests, hit twice

A protection test that matches raw source also matches prose that *quotes* the
forbidden pattern. `test_no_provider_passes_credentials_in_a_query_string` failed
against correct code because the substring `?key=` appears in the comment explaining
the defect. A test in that state pressures the next person to delete the explanation
instead of the problem.

Both occurrences were fixed by asking the precise question instead of a textual one:
`_code_of()` strips docstrings via AST, and the router test walks the AST for
`requests.post/get/put/request` call sites and inspects `params=` dict keys. The
detector was then confirmed to still fire against a deliberately reintroduced defect.

## 27. Mobile verification — what was and was not run

Run and passing:

| Check | Result |
|---|---|
| `npx tsc --noEmit` | exit 0 |
| Jest (6 shards) | **160 suites, 2,820 tests**, all passing |
| `npm run i18n:validate` | OK — 11 locales, catalog version 1.0.0 |

Shard breakdown: 27+27+27+27+26+26 suites; 533+427+327+477+750+306 tests. Six shards
were required because the sandbox's 45-second command ceiling cannot accommodate the
full run.

Advisory, not gating: `i18n:validate` notes that `fr` and `pt` each omit the advisory
plural form "many" in 20 families. `npm run i18n:hardcoded` reports 2,619 strings
across 170 files with 68/238 files clean — that is the advisory scanner, not the CI
gate.

**Not run: physical two-device iPhone QA.** The mission explicitly prohibits claiming
device testing unless a real iPhone was used, and no iPhone was available to this
work. The Live audio fix in sections 2–3 is therefore verified by code review, by the
protection suite, and against device syslog evidence of the failure — **but the
end-to-end broadcast has not been observed working on hardware.** Section 5's matrix
exists to be run by someone who has two devices. Static checks do not replace device QA
for livestream, push, checkout or uploads.

## 28. Backend verification is static, and why

The sandbox has no PyPI access, so `flask` and `stripe` are absent and `bot.py` cannot
be imported. All backend verification was therefore static: `py_compile` clean on
`bot.py`, `services/db.py`, `undx_router.py`, `undx_execution_kernel.py` and
`scripts/ops/backup_database.py`, plus the AST-based protection suites. The kernel and
backup tests are the exception — they have no Flask dependency and were exercised
behaviourally against temporary directories.

This is a real limit on confidence and is stated rather than papered over.

## 29. Change inventory

26 files modified, **2,243 insertions / 209 deletions**; 17 new files. Largest
modifications: `bot.py` (+738), `.env.example` (+457), `services/backend_management_registry.py`
(+274), `reports/realtime_audio_change_declaration.md` (+137), `services/undx_brain/config.py`
(+114), `undx_execution_kernel.py` (+95), `scripts/protection/run_protection_suite.py`
(+90), `mobile-native/src/live/useLiveBroadcastRoom.ts` (+57),
`services/db.py` (+56), `services/pulsesoc_reliability.py` (+56),
`undx_router.py` (+37).

## 30. Documentation written

- `docs/railway_deployment_runbook.md` — deploy sequence, variable consequences,
  which endpoint to probe, workers deployed vs. existing, the SHA invariant.
- `docs/backup_and_restore_runbook.md` — usage, exit codes, verified results, injected
  faults, and an explicit "what is still not done".
- `docs/provider_api_purchase_report.md` — the API and purchase table.
- `docs/realtime_audio_live_test_matrix.md` — the device scenario grid.
- `docs/undx_manual.md` — new section 18b, "Guard Changes Need A Second Phrase".
- `CLAUDE.md` — project guide.

## 31. Deploy sequence

1. Confirm `FLASK_SECRET_KEY` is set in the Railway environment. **This blocks boot.**
2. `python3 scripts/protection/run_protection_suite.py` — 239 checks across 15 suites.
3. `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
4. Push. Railway builds via nixpacks (Python 3.11 + ffmpeg).
5. Watch the boot log for route-pack registration failures.
6. `curl -fsS https://pulsesoc.com/health` and `curl -fsS https://pulsesoc.com/health/ready`.

## 32. The SHA invariant — an operator step, not a satisfied condition

The mission requires that at final validation the approved local SHA equals the remote
branch SHA, the Railway web SHA, the worker SHA and the native embedded SHA. Railway
exposes its deployed commit as `RAILWAY_GIT_COMMIT_SHA`. Checking this needs the
Railway dashboard or CLI, which this work did not have and should not have had. It is
therefore listed as an operator step and **is not reported as satisfied.**

## 33. What is still open

- **No off-site backup copy, no schedule, restore time unmeasured.** The mechanism
  exists and is verified; the operational practice around it does not.
- **No error tracking.** There is no Sentry or equivalent integration, so an exception
  in production is visible only to whoever is reading logs at the time.
- **Physical device QA not run** (section 27).
- **Four workers exist but are not deployed** (section 17) — a product decision.
- **`fr` and `pt` plural coverage** — advisory, 20 families each.
- **2,619 hardcoded strings** flagged by the advisory i18n scanner.
- **`webhook_app = Flask(...)` appears twice** in `bot.py` (lines 384 and 1130); the
  second assignment wins and discards the first, so anything attached to the app
  between those lines is silently lost. Not changed during this mission — untangling it
  safely needs a dedicated pass — but it is the next person's first surprise.
- **Repository housekeeping** — hundreds of stale `.fuse_hidden*` files and a pile of
  `*_REPORT.md` mission writeups in the root.

## 34. Standing judgement

Nothing in this mission was applied to Railway. No secret value was invented, no paid
API was purchased, no credential was exposed, no security control was disabled
globally, no production-private user data was used as a QA fixture, and no
physical-device testing is claimed.

The through-line of the work is narrow and worth stating on its own: **an indicator
that cannot go red is not an indicator.** The hard-coded `"ok": True`, the asserted
feature registry, the six vendor keys nothing calls, the unbounded warning counts, the
undrained worker backlogs, the never-restored backups, and my own protection test that
failed against correct code are all the same object. The repairs above are all the same
repair — make the signal derive from the thing it describes, and prove it can fail.

---

*Prepared for CoinPlotXAI Inc. Report-only with respect to Railway; all code changes
are local to branch `codex/emergency-live-audio-recovery` and uncommitted at the time
of writing.*
