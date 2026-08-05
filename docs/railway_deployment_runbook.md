# Railway Deployment Runbook

Mission phases 7 and 17. Report-only: nothing in this document was applied to
Railway from here, because doing so requires credentials this work did not have
and should not have had.

## Read this before the next deploy

**`FLASK_SECRET_KEY` is now required. Without it the web service will refuse to
boot.**

This is a deliberate behaviour change made during this mission, and it is the
one item here that can turn a routine deploy into an outage. Check the variable
is set in Railway *before* pushing.

The reason is in `bot.py` around line 82. That key signs Flask sessions **and**
the mobile bearer tokens minted in `issue_mobile_access_token()` and verified on
every `/api/mobile` call. The `Procfile` runs `gunicorn --workers 2`, and each
worker executes the module top-level separately. With the variable unset, the
old code gave each worker its own `secrets.token_hex(32)`, so a token minted by
worker A failed `hmac.compare_digest` on worker B.

The user-visible symptom was not "sessions reset on deploy". It was random 401s
and random logouts on roughly half of all requests, indefinitely, with nothing
in the logs but the auth failures themselves — which is exactly the shape of bug
that gets triaged as a mobile-client problem for weeks.

Generate a value and set it once:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

`PULSESOC_ALLOW_EPHEMERAL_SECRET=1` restores the old behaviour if you genuinely
need to boot without it. It is an escape hatch for debugging, not a fix.

## Variable inventory

The repository reads environment variables through sixteen wrapper functions
(`_env_value`, `_flag`, `subflag_enabled`, and others), and two modules resolve
names at runtime from data structures rather than literals: `undx_router.PROVIDERS`
and the `required_env` lists in `pulsesoc_intelligence_engine.py`. A plain grep
for `os.getenv` misses all three shapes, which is why the earlier
"undocumented variable" counts in this project were unreliable.

`scripts/undx_railway_variable_audit.py` covers all three. Its run earlier in
this mission reported **452 variables read, 462 declared in `.env.example`, 0
undocumented**. That script walks the entire tree and takes a few minutes; it was
not re-run for this document, and the counts above are quoted from that earlier
run rather than re-measured.

```bash
python3 scripts/undx_railway_variable_audit.py --service web
```

## What must be set

Full per-variable behaviour, including what happens when each is unset, is in
`docs/provider_api_purchase_report.md`. In short:

| Variable | Consequence if unset |
|---|---|
| `FLASK_SECRET_KEY` | **Boot fails.** See above |
| `DATABASE_URL` | Falls back to local SQLite — data is lost on every container replacement |
| `STRIPE_SECRET_KEY` + webhook secret | Payments disabled |
| `LIVEKIT_*` | Calls and Live broadcast cannot start |
| `R2_*` / `AWS_*` | Uploads fail |
| `MUX_*` | Streaming/VOD disabled |
| `BREVO_API_KEY` | No email or SMS, including password reset |
| `TURN_SERVER_URL` | Calls fail for users behind restrictive NAT — silently, and only for some of them |

The six credentials named in section 1 of the purchase report
(`POLYGON_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `REUTERS_API_KEY`,
`AP_NEWS_API_KEY`, `WHALE_ALERT_API_KEY`) are read **only** to colour a readiness
row. No code calls those vendors. Buying them turns red rows green and changes
nothing else.

## Deploy sequence

1. Confirm `FLASK_SECRET_KEY` is set in the Railway environment.
2. Run the protection suite locally: `python3 scripts/protection/run_protection_suite.py`
   — currently **239 checks across 15 suites**.
3. Run the audio change gate:
   `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
   This fires on `mobile-native/src/live/useLiveBroadcastRoom.ts`, which is in
   the protected manifest.
4. Push. Railway builds via nixpacks (Python 3.11 + ffmpeg).
5. Watch the boot log for route-pack registration failures. Optional packs
   register inside `except Exception` blocks, so a subsystem can vanish without
   the deploy failing. `/health` now reports this — see below.
6. Verify:

```bash
curl -fsS https://pulsesoc.com/health        # liveness, always 200 while the process answers
curl -fsS https://pulsesoc.com/health/ready  # readiness, 503 when it cannot serve
```

## Which endpoint your platform should probe

Point Railway's healthcheck and any uptime monitor at **`/health/ready`**.

`/health` used to return a hard-coded `"ok": True`. Nothing computed it, so no
outage could falsify it: a web process whose database had gone away answered
`200 {"ok": true}` for as long as gunicorn stayed up, and any monitor watching it
reported a green platform through a total data-layer outage. It now derives `ok`
from a cached `SELECT 1` and from route-pack registration.

The split matters:

- **`/health`** answers *is this process alive* and deliberately stays **200**
  even when the database is unreachable. A platform that restarts a container on
  a transient database blip makes the outage longer, not shorter.
- **`/health/ready`** answers *should this process receive traffic* and returns
  **503** with `database_unreachable` or `route_packs_failed`.

`/health/ready` is unauthenticated and deliberately does **not** echo the
database error text. SQLAlchemy connection failures embed the full DSN, password
included.

The ping behind both is cached for 5 seconds (`services/db.py`), so per-second
probing does not turn the healthcheck into its own load problem. That pressure —
"the check is expensive, let's just return a constant" — is what produced the
hard-coded `True` in the first place.

## Workers

The `Procfile` runs `web`, `undx_worker` and `email_worker`. Four other workers
exist in the repository and are **not** deployed:

```
alert_worker.py   media_worker.py   pulse_worker.py   telegram_worker.py
```

This is worth stating plainly because it is invisible from the dashboard: work
queued for those workers is never drained, and any metric counting their backlog
counts an archive rather than a queue. That is the same defect the department
warning counts had before this mission bounded them to a rolling window.

Whether these should be running is a product decision, not a deployment one. It
should be made deliberately rather than by noticing the Procfile.

## Backups

Configure `PULSESOC_BACKUP_VERIFY_URL` to point at a **second, empty** Postgres
service. Without it, `scripts/ops/backup_database.py` exits 3 — backup taken,
restore not verified — rather than pretending the dump is known-good. See
`docs/backup_and_restore_runbook.md`, including the gaps that are still open
(no off-site copy, no schedule, restore time unmeasured).

## The SHA invariant

The mission requires that at final validation the approved local SHA equals the
remote branch SHA, the Railway web SHA, the worker SHA and the native embedded
SHA. Railway exposes its deployed commit as `RAILWAY_GIT_COMMIT_SHA`. Checking
this needs the Railway dashboard or CLI and could not be done from here, so it
is listed as an operator step rather than reported as satisfied.
