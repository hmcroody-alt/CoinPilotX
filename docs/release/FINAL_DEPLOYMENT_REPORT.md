# Final deployment report — 2026-09-17

## The deployed commit

`7dfeb7ac9af7ef55211e39718f00e7d2d5b2dd24`

`git ls-remote origin refs/heads/main` confirms it. The push was a fast-forward
of a pinned SHA onto `refs/heads/main`; `main` as a moving name was never pushed,
and nothing was forced.

## Every service, and what it reports

Railway rebuilt all services automatically from the push. Read back from
`railway deployment list --service <name> --json`:

| Service | Status | commitHash | branch | cliCaller |
|---|---|---|---|---|
| CoinPilotX (web, pulsesoc.com) | SUCCESS | `7dfeb7ac9af7…` | main | none |
| python telegram_worker.py | SUCCESS | `7dfeb7ac9af7…` | main | none |
| coinpilotx-pulse-worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| coinpilotx-media-engine | SUCCESS | `7dfeb7ac9af7…` | main | none |
| PulseSoc Command Center Worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| pulsesoc-ads-worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| coinpilotx-undx-worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| email_worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| supplier_worker | SUCCESS | `7dfeb7ac9af7…` | main | none |
| python alert_worker.py | SUCCESS | `7dfeb7ac9af7…` | main | none |

All ten report Online. `TEMP-undx-canonical-index-DELETE-AFTER` and
`tmp-replay-diagnostics` are one-shot jobs showing Completed and are not
deployable services. `Postgres` is Online.

`branch=main` with `cliCaller=none` is the specific thing being checked, not a
formality. A `railway up` deploy records a null commit hash and a CLI caller, and
runs code on no branch — a state that the next auto-deploy silently erases hours
later. Ten services all naming the same SHA on `main` with no CLI caller is what
rules that out.

## Backend surface actually affected

Only three files in the delta are server-side:

- `services/notification_service.py`
- `services/pulsesoc_notification_system.py`
- `tests/test_push_icon_badge_combined.py`

All three belong to `4754979e`, which makes every server push stamp the icon with
the combined unread count instead of one scope alone. Everything else in the
release is `mobile-native/`, `docs/`, `reports/` and `scripts/` — inert on the
server.

## Production smoke

| Path | Status | Note |
|---|---|---|
| `/` | 200 | 33.7 KB, 0.33 s |
| `/login` | 200 | 31.2 KB |
| `/search` | 200 | |
| `/pulse` | 302 | redirect, expected for an unauthenticated client |
| `/static/manifest.json` | 200 | |
| `/.well-known/apple-app-site-association` | 200 | serves `87ZC69AGSR.com.pulsesoc.app`, claims `/pulse` and `/pulse/*`, excludes `/pulse/app*` |
| `/api/pulse/feed` | 401 | `{"message":"Login required.","ok":false}` — a typed refusal, not a 500 |

The 404s recorded during smoke (`/api/health`, `/api/version`, `/marketplace`)
were guesses at paths that do not exist in this application. They are listed so
the record is complete, not because anything regressed; no route in the delta
changed.

## Money movement

Unchanged and closed. No file in the delta matches
`stripe|payout|payment|marketplace|settle|ledger|transfer|connect`. Production
holds `sk_live_…`/`pk_live_…` only, and none of
`MARKETPLACE_PAYOUT_WORKER_ENABLED`, `..._OWNER_AUTHORIZED`, or any transfer or
seller-fee variable is set. No test credential exists in production.

## One honest discrepancy

The five documents in `docs/release/` — including this one — can only be written
after deployment and device installation, so the final `main` will sit one
docs-only commit ahead of `7dfeb7ac`, the SHA that production runs and that both
binaries were built from.

The code lineage is identical. The difference is documentation and nothing else.
Stating it rather than rounding it off, because "everything is on one SHA" would
otherwise be very slightly untrue.
