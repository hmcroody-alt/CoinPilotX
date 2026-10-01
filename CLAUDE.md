# CoinPilotX / PulseSoc — Project Guide

Repo folder is `CoinPilotX` (the original crypto-bot product). The live product is
**PulseSoc** (pulsesoc.com): a social platform with feed, reels, live streaming, chat,
marketplace, ads, and an AI layer. Crypto features still exist as a subsystem.

## Layout

```
bot.py                  Flask monolith — 111k lines, ~1,538 routes
services/               239 modules, the actual business logic
models/, migrations/    thin; most schema lives in bot.init_db()
templates/ static/      server-rendered Jinja + vanilla JS (PWA)
mobile-native/          ACTIVE React Native app (Expo 54, RN 0.81.5)
mobile/                 LEGACY app (Expo 51) — do not develop here
tests/                  pytest; tests/protection/ holds the golden-path suite
scripts/                ~200 audit/one-off scripts, plus scripts/protection/
docs/                   architecture + the protection policies (read these)
config/                 realtime-audio-protected-paths.json (CI reads this)
*_worker.py             background workers
undx_*.py               UNDX AI mission/execution layer
```

## Backend

Flask app object is `bot:app` (alias of `webhook_app`). Note: `webhook_app = Flask(...)`
appears **twice** (bot.py:384 and bot.py:1130) — the second assignment wins and discards
the first. Anything attached to the app between those lines is lost. Worth knowing before
debugging "my config isn't applying."

Route families by volume: `/api/pulse` (323), `/api/business-os` (199), `/api/arena` (120),
`/admin/business-os` (49), `/api/admin` (37), `/api/dashboard` (29), then crypto, messages,
account, mobile, reels, undx, payments.

Optional route packs are registered inside `except Exception` blocks so one broken feature
can't block boot. The trade-off: a subsystem can silently vanish in production. Check boot
logs for registration failures rather than assuming a 404 means a routing bug.

Data: SQLAlchemy over SQLite locally (`coinpilotx.db`), PostgreSQL via `DATABASE_URL` in
prod. `services/db.py` is the accessor. Schema is created imperatively in `bot.init_db()`
with ~170 tables in `AUTO_PK_TABLES` — there is no real migration framework, so schema
changes are hand-rolled and must be idempotent.

Deploy: Railway, nixpacks (Python 3.11 + ffmpeg). Procfile runs gunicorn `web` plus
`undx_worker` and `email_worker`. Other workers (`alert_worker`, `media_worker`,
`pulse_worker`, `telegram_worker`) exist but aren't in the Procfile.

Integrations: Stripe, Telegram bot, LiveKit (calls/live), Mux (streaming), Brevo (email/SMS),
Cloudflare R2 via boto3, Firebase/FCM + APNs + web push, Google Cloud Translation, CoinGecko,
optional Redis. `.env.example` documents ~180 keys.

## UNDX

An AI mission/execution layer that spans root modules (`undx_router.py`,
`undx_execution_kernel.py`, `undx_brain_layer.py`, `undx_desktop_connector.py`) plus ~25
`services/undx_*.py` modules. `undx_router` picks between OpenAI/Claude/Gemini/DeepSeek/Groq
server-side so keys never reach the browser. `undx_execution_kernel` can propose diffs
against the repo but only writes after the approval phrase `APPROVE UNDX WRITE`; it blocks
`.env`, `.git`, venv, secrets, and sqlite paths, and logs to `undx_execution_log.jsonl`.
See `docs/undx_manual.md`.

## Mobile

Work in `mobile-native/`. Expo SDK 54, RN 0.81.5, React 19, TypeScript 5.9, React Navigation,
Zustand. Bundle ID `com.pulsesoc.app` (dev: `com.pulsesoc.nativeapp.dev`), EAS profiles
development / development-simulator / preview / production.

API layer: `src/api/` with a shared `pulseApi()` wrapper over `PULSE_API_BASE_URL`
(default `https://pulsesoc.com`). Bearer token + session cookie, refresh via
`POST /api/mobile/auth/refresh`, tokens in expo-secure-store.

Native bits: `modules/pulse-now-playing/` (iOS lock-screen controls, Swift) and two
`patches/` — a Hermes build fix, and a LiveKit WebRTC patch that stops the camera from
reconfiguring the shared `AVAudioSession`. Both are load-bearing; `patch-package` runs
postinstall.

Verify with `npm run verify` (typecheck + i18n + jest). i18n is gated — hardcoded strings
fail CI.

## Rules that will bite you

**Real-time audio is hard-locked.** Read `docs/realtime_audio_change_policy.md` before
touching anything in `config/realtime-audio-protected-paths.json`. A mission that isn't
about audio must not edit a protected path. The characteristic failure here is an unrelated
screen calling `Audio.setAudioModeAsync` or `AVAudioSession.setCategory` and stealing the
session from a live call — build stays green, tests pass, production goes silent.

Forbidden regardless of justification: screen-level AVAudioSession setup, a second
microphone track, a second LiveKit publication path, a new global audio singleton, bypassing
ownership arbitration, or copying the audio-call implementation into another screen. The
`expo-av` legacy allowlist is capped at six files; a seventh call site fails CI.

`bot.py` is protected by diff *content*, not by path — only lines matching
`backend_diff_patterns` trigger the gate. Run it locally:
`python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`

**Protection suite** covers 21 subsystems (livestream, reels, chat, uploads, camera, payments,
auth, navigation, …). Runner: `scripts/protection/run_protection_suite.py`, CI in
`.github/workflows/protection.yml`. Static checks don't replace device QA for livestream,
push, checkout, or uploads.

## Current state (as of Aug 2026)

Branch `codex/emergency-live-audio-recovery`, dirty: modified `bot.py`,
`services/pulse_ai_service.py`, `services/undx_agent_policy.py`,
`services/undx_architecture.py`, `services/undx_brain/config.py`, `undx_worker.py`;
untracked `services/undx_mission_runtime.py`,
`tests/undx_agent/test_safety_precedence.py`, `scripts/undx_railway_variable_audit.py`.
Recent commits are iOS build 13 prep and audio protection work.

Housekeeping: the repo root has hundreds of stale `.fuse_hidden*` files and a pile of
`*_REPORT.md` mission writeups.

## Paths

Files: `/Users/hmcherie/Desktop/CoinPilotX`
Bash: `/sessions/happy-sweet-brahmagupta/mnt/CoinPilotX`
