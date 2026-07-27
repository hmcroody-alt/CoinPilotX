# PulseSoc Native — Security Findings Ledger

**Organization:** COINPLOTXAI INC. (to be verified — see Open Items)
**Audited branch:** `release/undx-nexus-core-v4`
**Audited commit SHA:** `a0cc15cd260f4756e071e066cb8d7750bb85bc3f`
**Audit date:** 2026-07-20
**Scope of this pass:** Prioritized security audit (secrets, mobile transport, token storage, backend authN/authZ + IDOR spot-check, upload validation, dependency + release-config review). **Not** an exhaustive route-by-route audit of all ~1247 backend routes, nor a full live penetration test.

---

## Severity gate status

| Severity | Count | Gate |
|----------|------:|------|
| P0 | **0** | Must be 0 — **met** |
| P1 | **0** | Must be 0 (or formally risk-accepted) — **met** |
| P2 | 4 | Documented with owners below |
| Open verification items | 6 | **Block a full GO** until owner-resolved |

---

## What was verified as SECURE (positives)

- **Secrets:** No secrets in the tracked source tree and **none in git history**. `.gitignore` correctly excludes `.env`, `.env.*` (except `.env.example`), `coinpilotx.db`, `*.log`, `*.sqlite*`. `.env`, `.env.local`, the 111 MB DB, and 1.6 GB log are all **untracked**. The two `-----BEGIN PRIVATE KEY-----` matches are a test placeholder (`push_provider_configuration_audit.py:49`) and a validation `startswith` check (`native_push_readiness.py:33`) — not real keys.
- **Transport:** Mobile API base URL is `https://pulsesoc.com` (HTTPS). No plaintext `http://`/`ws://` endpoints in app source. No `NSAllowsArbitraryLoads` / ATS bypass in `app.json`.
- **Token storage:** Session cookie stored in iOS Keychain via `expo-secure-store` with `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` (`src/session/sessionStore.ts`). Biometric flow stores only a user-id and **re-validates server-side**, rejecting mismatched users (`src/session/biometricAuth.ts` + tests). AsyncStorage holds only non-secret data (timezone, sync cursor, feed/filter prefs, cache).
- **Backend auth model:** Login (`bot.py:5643`) uses `check_password_hash`, then issues an HMAC-SHA256-signed bearer token validated with `hmac.compare_digest`, expiry check, and a server-side `mobile_security_sessions` row (`status='active'` + `device_hash`) — i.e. **server-side revocable** (`bot.py:2716-2764`). All handlers derive the user from `require_account()`/`account_user_id()`, never a client-supplied id.
- **Authorization / IDOR:** Sampled ~10 sensitive endpoints (reels, messages thread/send/read, account delete, trusted-device delete, profile update, dashboard). **All enforce ownership/membership** (`WHERE id=? AND user_id=?`, `user_is_conversation_member()`, password re-check on account delete). No IDOR found in the sample.
- **Admin isolation:** Admin uses a **separate** session key (`session["admin_user_id"]`) re-queried against `admin_users WHERE status='active'`, with per-permission checks + audit logging (`bot.py:12679`, `15359`). A mobile `account_user_id` does not grant admin.
- **Mass assignment:** Profile update uses a **fixed field whitelist** with a static parameterized UPDATE (`bot.py:84584-84618`). No `**data` / `setattr` mass-assignment against the user record.
- **Login rate limiting:** Strong — IP + email + domain velocity limits, lockouts, escalating challenges after 3 failures (`bot.py:4654`, `4378-4384`).
- **Uploads:** `secure_filename` used (path-traversal protection, 6 sites); per-endpoint `content_type`/`content_length` checks present.
- **Debug:** No `debug=True`; production served via gunicorn (`Procfile`).
- **Build plugin:** `withDevelopmentIosIdentity` only sets `CFBundleDisplayName` from an env var — benign, no security impact.
- **npm audit:** 16 moderate, **0 high/critical** — all in the Expo dev/build toolchain (postcss XSS, uuid bounds via `xcode`), not shipped in the iOS binary.

---

## P2 findings (defense-in-depth / documented)

| # | Finding | Evidence | Owner |
|---|---------|----------|-------|
| P2-1 | **Register endpoint has no rate limiting** — enables account-creation spam / email-bombing (login is throttled, register is not). | `api_mobile_auth_register` `bot.py:5729` | Backend |
| P2-2 | **No global `MAX_CONTENT_LENGTH`** request-body cap on the Flask app (per-endpoint `content_length` checks exist, but no global safety net vs oversized-request memory DoS). | not set in `bot.py` | Backend |
| P2-3 | **Private message/draft caching in AsyncStorage** (unencrypted at rest). Mission classifies private-message cache and private drafts as sensitive. | `ConversationControlCenter.tsx:408`, `ChatScreen.tsx:280`, `StatusCreator.tsx` | Mobile |
| P2-4 | 16 moderate npm advisories in Expo dev/build toolchain (not in shipped binary). Update at convenience. | `npm audit --omit=dev` | Mobile |

---

## Open verification items (owner action required — these prevent a clean GO)

1. ~~**Frozen commit:** Working tree has 238 uncommitted changes...~~ **RESOLVED 2026-07-20.** Working tree committed and frozen. **Frozen RC SHA: `086e0c1c7aa6c4a97e0f3e841d4bac87f16e5480`** (branch `release/undx-nexus-core-v4`). Tree is clean, no active churn. Runtime upload dir `static/uploads/` and large screen recordings added to `.gitignore` and excluded. Build the release ONLY from this SHA. *Note: two late source files (`BuyerOrdersScreen.tsx`, `perfTrace.test.ts`) were folded into the freeze after the main audit pass and were not individually reviewed.* **New finding P2-5:** the runtime media upload target `static/uploads/pulse_media/` had already-tracked files with no ignore rule (privacy risk); newly gitignored, but existing tracked media should be untracked in a deliberate follow-up (history rewrite, not part of freeze).
2. **Expo org ownership:** `app.json` `owner: "hmcroody"` looks like a personal handle. Mission requires the **COINPLOTXAI INC.** Expo org. Confirm via `eas whoami` and project ownership (EAS projectId `03be39d7-db88-43af-af5f-50c267d830f8`).
3. **Apple team / provider:** Confirm the Apple Developer team and App Store Connect provider are **COINPLOTXAI INC.** (not a personal team).
4. **Bundle identifier:** Confirm `com.pulsesoc.nativeapp` matches the **existing** PulseSoc App Store listing (mission forbids creating a new bundle id).
5. **Live CVE scan:** `pip_audit` unavailable this session — backend deps are pinned in `requirements.txt` but not CVE-verified. Run `pip_audit -r requirements.txt` in a connected env.
6. **Release version/build number:** `app.json` version is `0.1.0` with no iOS `buildNumber` and no `autoIncrement` in `eas.json`. Set a build number higher than any prior upload before building.

---

## GO / NO-GO

**Recommendation: NOT-YET-GO (conditional).**

The **code** is in strong shape — no P0/P1 vulnerabilities in the sampled, prioritized audit, and multiple security-critical controls (auth, authz, admin isolation, token storage, secrets hygiene) are well-implemented. However, several mission-mandated GO gates are **not yet satisfiable by code review alone** and require owner action: a frozen clean commit, verified Expo org = COINPLOTXAI, verified Apple team, bundle-id match to the existing listing, and a live dependency CVE scan. The audit was also a prioritized sample (~10 of ~1247 routes), not the exhaustive route-by-route + full pen-test the mission specifies.

**No Expo login, Apple login, production build, or App Store upload should proceed** until the six Open Items are resolved and the owner records a documented GO. Those authentication and upload steps are the owner's to drive directly in a secure terminal — they are outside what this audit performs.
