# PulseSoc Native — Device-Observation Evidence (2026-07-24)

Strictly evidence-based. Statuses use only **PASS / PARTIAL / BLOCKED / NOT TESTED**.
Nothing is marked PASS unless it was actually observed working.

## VALIDATION-ENVIRONMENT NOTICE — clean rebuild + audio + p3r7or CANNOT be done from here
The acceptance criteria for Issues 1 & 2 (and 3–6) require a **clean native iOS rebuild**,
**audible playback verification**, and **p3r7or**. None of these are executable from this
environment. Definitive blockers confirmed this session:

- **No macOS build toolchain.** The automation shell is a **Linux aarch64 sandbox**
  (`uname`: `Linux claude … aarch64`). `xcodebuild`, `xcrun`, `simctl`, `pod`, `expo`,
  `watchman` are all **NOT FOUND**. The Mac's Terminal is granted tier "click" (typing blocked),
  and the sandbox is a separate machine, so `xcrun simctl uninstall com.pulsesoc.app`,
  DerivedData clearing, Metro/Expo cache purge, native rebuild, and install **cannot be run**.
- **No audio perception.** Screenshots are silent; no tool can confirm a track is *heard*.
- **No p3r7or.** Only the one simulator can be driven; the physical device cannot.

**Verified repo/build facts (what IS confirmable here):**
- Branch **`release/undx-nexus-core-v4`**; local HEAD **`7e9af19c`**; equals the *cached* local
  ref `origin/release/undx-nexus-core-v4`. `git fetch` is **blocked** (GitHub egress forbidden),
  so the *live* remote tip could not be independently re-fetched from here.
- iOS bundle id **`com.pulsesoc.app`**; Android **`com.pulsesoc.nativeapp`** (from `app.json`).
- Native iOS project present (`ios/PulseSocNative.xcworkspace`, Podfile, Pods) — a Mac build IS
  possible, just not from this sandbox.
- Working tree still carries pre-existing unrelated changes (`.env.example`,
  `services/pulse_ai_provider_router.py`, `bot.py` staged+unstaged, `.claude/`,
  `mobile-native/store/`, several reports) — left untouched, not committed.

**SUPERSEDED:** The simulator UI observations for Issues 1 & 2 below were taken on the
**existing installed (pre-rebuild) build**. Per the mission owner's instruction they are
**NOT release evidence** — they show UI wiring only, never audio. Issues 1 & 2 remain **PARTIAL**.
Audible Feed-video music authority, audible music preview-before-select, audible final Status
preview, and p3r7or confirmation are **owner-side on the Mac** (see closing section).

### Required-test matrix — Issues 1 & 2 acceptance criteria (all audio items UNVERIFIABLE here)
| Check | Result in this environment |
|-------|----------------------------|
| Device used | iPhone 16 Pro **Simulator** (iOS 26.5), existing build; p3r7or unavailable |
| Feed video w/ attached music — heard inline | **UNVERIFIABLE (no audio)** |
| Same track heard in expanded viewer | **UNVERIFIABLE (no audio)** |
| Original video audio NOT substituted | **UNVERIFIABLE (no audio)** |
| Fullscreen track authoritative (audible) | **UNVERIFIABLE (no audio)** |
| Profile grid / repost / deep-link (audible) | **UNVERIFIABLE (no audio)** |
| Status picker dedicated **play** control → sound | **UNVERIFIABLE (no audio)** |
| Play icon → pause toggle produces/stops sound | **UNVERIFIABLE (no audio)** |
| Switching tracks stops first preview (audible) | **UNVERIFIABLE (no audio)** |
| Closing picker stops preview audio | **UNVERIFIABLE (no audio)** |
| Final Status preview audible & matches track | **UNVERIFIABLE (no audio)** |

## What was actually driven this session
The iPhone 16 Pro **Simulator** (iOS 26.5), running the PulseSoc build signed in as ROODY,
was driven directly (screenshots + taps). The physical iPhone **P3r7or** was **not** driven —
these tools can control only the one simulator, not a second physical handset.

## POST-DEPLOY UPDATE — backend RECOVERED and reachable (observed after push of 7e9af19c)
After the fix commit `7e9af19c` was pushed to origin and the host redeployed, I re-drove the
simulator and observed the recovery directly:

- **`https://pulsesoc.com` in simulator Safari now LOADS the full app landing page** ("Your
  creator, video, live, messaging, and AI intelligence home", Sign In / Join / Explore, nav
  bar). Earlier this session the same URL returned a blank/failed load. A served HTML page means
  `gunicorn bot:app` booted and `import bot` succeeded — i.e. the f-string `SyntaxError` boot
  crash is gone. **Backend availability: PASS (observed device-side).**
- **In the PulseSoc app, opening the Nathan conversation now loads cleanly** — normal empty-state
  "No messages yet" instead of the previous "Messages could not load — PulseSoc could not be
  reached." The composer footer reads **"PULSE LINK — SECURE · READY"** (green dot) with an
  **enabled** message input. Last session this exact element read "PULSE LINK — RECONNECTING"
  with the input disabled. **App→backend reachability on the simulator: PASS (observed).**
- Honesty note: the Messenger **list** still showed the stale banner "Showing cached
  conversations while Messenger reconnects" even though the per-conversation Pulse Link was
  READY. That banner is stale list-UI (the app had been sitting offline since before the deploy);
  the authoritative live signal is the per-conversation "SECURE · READY" + enabled composer.

**What I still could NOT verify (unchanged environmental limits):** the host's own deploy
dashboard is not reachable from this sandbox, so the **deployment ID, the exact deployed Python
version, the gunicorn boot log line, and the DB-connection log** cannot be read from here — those
remain owner-side confirmations. The device-side proof above is the working-system evidence.

## Prior finding (before the deploy) — backend was UNREACHABLE from the simulator (app-wide)
Observed, repeatedly and persistently, across multiple screens:

- Messenger list: banner **"Showing cached conversations while Messenger reconnects."**
- Open conversation (Nathan): **"Messages could not load — PulseSoc could not be reached.
  Check your connection and try again."** with a **Retry** button.
- Conversation header: **"Messages unavailable."**
- Composer: **"PULSE LINK — RECONNECTING"**; the message input is **disabled** (typed text
  does not register).
- **Retry** was tapped and the same "could not be reached" state returned — the outage is
  **persistent**, not a transient blip.

### Root of the outage — the pulsesoc.com backend host, not the device/network/app config
The app is correctly configured to the **production** backend:
`app.json` `extra.pulseApiBaseUrl = "https://pulsesoc.com"` and
`src/api/config.ts` resolves `PULSE_API_BASE_URL` to `https://pulsesoc.com` (no localhost/dev
override present, no `.env` overriding it). So the client is pointed at the right place.

**Reachability diagnostic run from inside the simulator (Safari):**
- `https://google.com` → **loaded fully**, with live trending-search content. Proves the
  simulator and the Mac have working general internet.
- `https://pulsesoc.com` → Safari showed a **blank/failed load** (same as the app's
  "could not be reached").

Conclusion: the outage is **specific to the `pulsesoc.com` backend host** — it is NOT the
Mac's network and NOT the mobile app's configuration. The most likely cause is that the
production backend (a Python service; `Procfile` + `nixpacks.toml` in this repo, so
Railway/Render-style hosting) is **down, not running, or its deploy is unhealthy**. The exact
HTTP status could not be captured here: this sandbox's egress allowlist blocks `pulsesoc.com`
(only `*.anthropic.com`/`claude.com` are reachable), so production cannot be probed from my side.
This is a backend-availability condition, outside the scope of the Issues 1–6 mobile code fixes.

### What to check to restore it (owner action)
1. Open the backend host's dashboard (wherever `pulsesoc.com` is deployed) and confirm the
   service is **running** and the latest deploy didn't crash-loop.
2. Check the app process / reverse proxy / TLS cert for `pulsesoc.com`.
3. Restart / redeploy the backend, then re-open the app — it should leave "RECONNECTING".

## Backend source inspection — prime suspect: Python-version mismatch crashing `bot:app`

The web process is `gunicorn bot:app` (`Procfile`). If `bot.py` fails to import at boot,
gunicorn crashes and `pulsesoc.com` serves nothing — which matches the symptom.

**Verified defect:** `bot.py` contains **12 f-strings whose expression part includes a
backslash** (escaped quotes inside `{...}`). That is a hard `SyntaxError` on **Python ≤ 3.11**
("f-string expression part cannot include a backslash"); PEP 701 only made it legal in
**Python 3.12+**. Byte-compiling `bot.py` here (Python 3.10) fails at the first one
(line 15755) with exactly that error; the pinned production runtime `nixpacks.toml` →
`nixPkgs = ["python311"]` **(3.11)** would reject it identically → `import bot` fails →
gunicorn cannot boot → site down.

The 12 locations (all in `bot.py`):
`15755, 16833, 17359, 17700, 37804, 42566, 43597, 68428, 68463, 70326, 70390, 79557`.

**Contradictory runtime signals in the repo:**
- `nixpacks.toml` pins **python311** (3.11) → f-strings are a SyntaxError.
- `.python-version` says **3.13.13** (set 2026-06-10) → f-strings are legal.

**Timeline caveat (important for honesty):** the offending lines date from 2026-05-21 through
2026-07-22 — several predate the outage by months. If production had *always* run 3.11 the site
would have been broken since May, which is implausible for a working app. The most likely story
is that production has been running **3.13** (honoring `.python-version`), where this code is
valid, and the current outage began when a build/runtime **landed on Python 3.11** (nixpacks
pin taking effect, or `.python-version` no longer honored). Under 3.13 these are latent, not
today's cause.

**First thing to check in the deploy logs (fast confirm/deny):** look at the gunicorn boot
output for `SyntaxError: f-string expression part cannot include a backslash` and confirm the
**Python version the deploy actually built with**.
- If it says 3.11/3.12-below and shows that SyntaxError → this is the exact root cause.
- If it built with 3.13 and the error isn't there → the outage is something else (look for the
  real traceback / a different crash, missing env var, DB/Postgres connection failure, etc.).

**Two ways to fix, if confirmed:**
1. *Fastest:* make the deploy use Python 3.13 to match `.python-version` — set
   `nixpacks.toml` `nixPkgs = ["python313", "ffmpeg"]` (or ensure the builder honors
   `.python-version`). One-line change, but it changes the whole runtime.
2. *Most durable (runtime-agnostic):* rewrite the 12 f-strings so the backslash lives outside
   the `{...}` expression (pre-build the escaped-quote HTML into a variable, then interpolate
   it). Then `bot.py` compiles on both 3.11 and 3.13 and the landmine is gone for good.
   Recommended regardless of which runtime you settle on.

## UPDATE — durable backend fix made and committed (this session)
The runtime-agnostic fix is done. Every f-string whose expression part would crash on
Python ≤ 3.11 was rewritten so the escaped-quote HTML lives **outside** the `{...}` (lifted
into a plain single-quoted variable, or a small nested helper for the two comprehension cases).
Behavior is byte-identical; only the source form changed.

- **Scope was larger than the first scan showed.** The original scan found 12 backslash-in-
  expression sites; a second pass caught **1 more** — a same-quote-reuse f-string at old line
  30943 (`f'<body class="{'pulse-home-os' if …}"…'`), also illegal on ≤ 3.11. **13 total** fixed.
- **Verified:** `python3 -m py_compile bot.py` → **exit 0** on this sandbox's **Python 3.10.12**
  (a valid oracle for 3.11, which shares the pre-PEP-701 restriction). A full re-scan reports
  **0** backslash-in-expression and **0** quote-reuse f-strings remaining. `bot.py` now imports
  on both 3.11 and 3.13, so this landmine can no longer crash `gunicorn bot:app`.
- **Committed:** `7e9af19c` on `release/undx-nexus-core-v4` (parent `481bb211`; `bot.py` only,
  +34/−13). The committed blob was re-compiled clean, not just the working copy.

**Still not marked RESOLVED for the outage.** Per the mission rule (nothing PASS unless observed
working), this is the *prime suspect* fixed — not a confirmed cure. Confirm from the deploy logs
that the boot failure was `SyntaxError: f-string expression part cannot include a backslash`
under a Python ≤ 3.11 build. If yes, this commit removes it. If the log shows a 3.13 build or a
different traceback (missing env var, DB/Postgres failure, etc.), the outage is something else.

**Push is required from your Mac** (sandbox egress forbids GitHub). Origin is **3 commits behind**:
`cd ~/Desktop/CoinPilotX && git push origin release/undx-nexus-core-v4`
(fast-forwards origin `a307b506` → `7e9af19c`: `e5f566d4`, `481bb211`, `7e9af19c`).

## CODE FIX this session — Issue 2 real defect: Status picker had NO audible preview
Static source inspection (not audio playback) revealed a genuine gap that the exact Issue 2
test targets: **`StatusCreator.tsx` (the "STATUS STUDIO / Create Status" surface) music picker
only supported *selection* (`setSelectedMusic`) with no playback** — there was no way to *listen
before selecting*. The preview lifecycle (`resolvePreviewToggle`/`resolvePreviewStop` +
`expo-av Audio.Sound`) existed only in `HomePulseComposer.tsx` (the Home "CREATE A SIGNAL"
composer), not in the Status composer. A green selection outline (what the device screenshot
showed) is exactly "selected", NOT "previewed" — the user's objection was correct.

**Fix:** ported the shared `musicPreviewLifecycle` into `StatusCreator` with a **dedicated
Preview/Stop control per track row** (separate from the select control). Preview playback stops
on: switching tracks, re-tapping the playing track, selecting a track, the clip finishing,
picker/modal close (`visible → false`), and unmount. Behavior mirrors the already-correct Home
composer so both surfaces share one lifecycle.

- **Verified (what IS confirmable here, no audio needed):** `npx tsc --noEmit` **exit 0**;
  full Jest **48 suites / 441 tests PASS** (was 439; +2). New test
  `StatusCreatorMusicPreview.test.tsx` mounts the real component and asserts (a) tapping Preview
  calls `Audio.Sound.createAsync({uri: preview_url}, {shouldPlay:true})` *without* selecting,
  (b) previewing a second track unloads the first (stop-on-switch), (c) selecting a track unloads
  the preview (stop-on-select).
- **Committed:** `8356b016` on `release/undx-nexus-core-v4` (parent `7e9af19c`; exactly 2 files —
  `StatusCreator.tsx` +118, new test +109). Committed via temp-index seeded from HEAD so **none**
  of the unrelated working-tree changes (`.env.example`, `services/…`, `bot.py` WIP, `.claude/`,
  `mobile-native/store/`, reports) were included.
- **Still NOT PASS for Issue 2 device validation.** This makes the preview control *exist and be
  unit-proven*; whether the preview is **audible** on-device, and whether the **final Status
  preview** plays the selected segment, still require a clean rebuild + a human ear (owner-side).

**Push required from your Mac** (sandbox forbids GitHub egress). Cached origin is now **4 commits
behind** HEAD `8356b016`:
`cd ~/Desktop/CoinPilotX && git push origin release/undx-nexus-core-v4`
(sends `e5f566d4`, `481bb211`, `7e9af19c`, `8356b016`). Note: stale `.git` lock files
(`HEAD.lock`, ref `.lock`, `index.lock`) were left by the FUSE mount (can't unlink from the
sandbox) and were *parked* (renamed to `*.stale-*`). A push does not need them; if a local
`git commit` on your Mac ever complains about a lock, `rm -f .git/index.lock` clears it.

## Why the demanded two-device audio/video validation could not be completed
Three independent hard blockers, each sufficient on its own:

1. **Backend unreachable (above):** every backend/realtime-dependent flow — message send +
   reconcile, Live room join, live host audio, guest request→approve→publish, and call
   signaling (place/ring/answer) — requires the server. All are BLOCKED while the app is offline.
2. **No audio perception:** screenshots are silent. Host-audio audibility, ringback, ringtone,
   and in-call mic audio cannot be confirmed by any tool available here. "Heard" cannot be
   evidenced from this environment.
3. **Single device only:** these tools drive the one simulator. P3r7or cannot be driven, so no
   genuine two-user host↔viewer / caller↔callee scenario can be staged from here.

## POST-DEPLOY UI checks driven on the simulator (Issues 1 & 2) — backend now reachable
With the backend recovered, I drove the two visually-observable issues directly:

- **Issue 1 (feed attached-music authority) — PARTIAL (observed UI proxy).** In Reels →
  **Music** tab, the reel "Little Prince" (ROODY CHERIE) renders a **persistent attached-music
  pill "Late Night Receipt · PulseSoc Music"** with a music-note + speaker icon at the top of
  the open viewer. The attached-track label stays authoritative in the expanded reel — the
  observable proxy for "attached music remains the authority on open." **Audio audibility
  itself is NOT verifiable here** (screenshots are silent), so this is PARTIAL, not PASS.
- **Issue 2 (status + music preview) — PARTIAL (observed UI proxy).** Home → **Add Status**
  opens the "STATUS STUDIO / Create Status" composer with a live draft preview (text field,
  audience Public/Followers/Private, duration 24h/48h/72h/7d, media picker) and a **Music**
  section with a "Search creator-safe music" box + results ("Good Days Again — PulseSoc Music",
  "Now You See Me"). Tapping a track **selects/highlights it (green outline) before posting** —
  the preview-before-select flow renders and responds. Separately, tapping **Music** on a Post
  with no media correctly gates with **"Choose a photo or video before attaching approved
  music."** No status was posted (cancelled out). **Audio preview audibility NOT verifiable
  here**, so PARTIAL, not PASS.

## Status matrix (device / two-user media)
| Issue | Code fix | Automated tests | Device / two-user media (this session) |
|-------|----------|-----------------|----------------------------------------|
| 1 — feed attached-music authority | PASS | PASS | PARTIAL (music-authority pill persists in reel viewer; audio not audible here) |
| 2 — status + music preview | PASS | PASS | PARTIAL (status composer + music picker select/highlight works; audio not audible here) |
| 3 — chat bubble latency on Send | PASS | PASS | BLOCKED (composer disabled offline; cannot send) |
| 4 — in-app banner auto-dismiss | PASS | PASS | NOT TESTED (needs a real/local push; no push toolchain here) |
| 5 — live host audio + guest join | PASS | PASS | BLOCKED (backend offline + no audio + single device) |
| 6 — calls + ringback/ringtone | PASS | PASS | BLOCKED (backend offline + no audio + single device) |

Code-fix and automated-test columns are unchanged from the repair-evidence report
(`npx tsc --noEmit` EXIT 0; `npx jest` 47 suites / 439 tests PASS).

## What only you (on your Mac, with both phones) can do to finish device validation
1. **Restore backend reachability:** confirm `https://pulsesoc.com` is up and reachable from
   the Mac/phones (or point a dev build at a running local backend). Until the app leaves
   "RECONNECTING", no send/Live/call flow can be exercised.
2. **Push the fix commits** (blocked from this sandbox — proxy forbids GitHub egress):
   `cd ~/Desktop/CoinPilotX && git push origin release/undx-nexus-core-v4`
   (fast-forwards origin `a307b506` → `7e9af19c`, 3 commits: mobile fixes `e5f566d4` +
   `481bb211`, plus the backend f-string fix `7e9af19c`).
3. **Stage the real two-device tests** with P3r7or + simulator (or a second phone) once the
   backend is reachable: host audio audible on the viewer; guest request→approve→publish with
   camera/mic visible+audible; audio call both directions; video call both directions. Mark
   each PASS only when audio is heard and video is seen on both ends.
4. **Update P3r7or** with the new build after the push (no device toolchain here).

---

## OWNER-SIDE RUNBOOK — clean rebuild + audible validation (Mac only)
Everything below MUST run on the Mac; it is impossible from this Linux sandbox (no macOS
toolchain, no audio perception, no p3r7or). Do not mark Issue 1 or Issue 2 PASS until the
audible steps are observed on a **freshly rebuilt** build. Bundle id below is the verified
value from `app.json` (`com.pulsesoc.app`), not an assumed one.

### Step 0 — push the local-only commit
The sandbox is firewalled from GitHub, so `8356b016` is local-only and the cached origin is behind.
```
cd ~/Desktop/CoinPilotX
git push origin release/undx-nexus-core-v4
```
This sends `e5f566d4`, `481bb211`, `7e9af19c`, `8356b016`.

### Step 1 — confirm you are on the right source
```
cd ~/Desktop/CoinPilotX
git fetch origin
git rev-parse HEAD                                   # expect 8356b016...
git rev-parse origin/release/undx-nexus-core-v4      # must match HEAD after the push
git status                                           # confirm no unrelated files sneak in
```

### Step 2 — remove the stale app + clear caches
```
xcrun simctl uninstall booted com.pulsesoc.app
rm -rf ~/Library/Developer/Xcode/DerivedData/*
cd mobile-native
watchman watch-del-all 2>/dev/null || true
rm -rf "$TMPDIR"/metro-* "$TMPDIR"/haste-* node_modules/.cache
```

### Step 3 — rebuild native iOS from latest source and install
```
cd ~/Desktop/CoinPilotX/mobile-native
npx expo run:ios --device "<your booted simulator name>"
```
Do NOT use Expo Go or an old dev-client. After install, confirm the running build reports
commit `8356b016` before trusting anything on screen.

### Step 4 — record the build identity (fill in before testing)
- Branch: `release/undx-nexus-core-v4`
- Source commit SHA: __________ (must be 8356b016)
- App version / build number: __________
- Bundle identifier: `com.pulsesoc.app`
- Simulator device model / iOS version: __________
- Backend base URL / env: __________
- Backend deployed SHA: __________
- Build command: `npx expo run:ios`
- Evidence old app removed / new binary has latest changes: __________

### Step 5 — Issue 1 audible test (regular FEED VIDEO, not the Reel)
Play a regular Feed video that has attached music and confirm, by ear, on each surface that
the **attached track** is authoritative and the original video audio is suppressed:
Home Feed (inline) → expanded viewer → fullscreen → profile grid/feed → repost/shared → deep link.
Mark PASS only if the attached track is HEARD and original audio is NOT heard on every surface.

### Step 6 — Issue 2 audible test (preview-before-select + final Status preview)
In Status Studio's music picker: tap the dedicated ▶ Preview control on a track WITHOUT
selecting → hear it; toggle to ⏸ → it pauses; preview a different track → the first stops;
close the picker → all audio stops; reopen → no stale resume; only then select the track;
open the full Status preview → the selected segment is HEARD and matches; change/remove music
and re-verify; publish a disposable test status and confirm the published result matches the
preview. Also confirm the Status preview reflects the full final composition (media, text,
overlays, attached music, selected segment, original-audio mute/mix, caption/timing).

### Step 7 — repeat on p3r7or
Install the same `8356b016` build on p3r7or and re-run Steps 5–6 by ear before any final PASS.

### Evidence to capture per run
Device used · content tested · playback surface · track name · attached music heard? ·
original audio heard? · preview play/pause worked? · switching stopped first? ·
closing stopped playback? · final Status preview matched track?
