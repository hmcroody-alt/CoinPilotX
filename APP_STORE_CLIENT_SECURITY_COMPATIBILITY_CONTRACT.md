# App Store Client Security Compatibility Contract (Stage 1)

Baseline: `d8aaf911` on `main`. Worktree branch `feat/sentinel-server-defense-mesh`.

The shipped App Store binary is **frozen**. Hard Rule #1 forbids any change under
`mobile-native/**`, so every defense built in this mission must express itself in
responses the *already-installed* client understands. This document is the
binding constraint set for Stages 2–35. Every claim is a line I read in the
client source; the citation is the proof.

**Reading rule for later stages:** if a proposed defense cannot be expressed
within this contract, the defense gets redesigned. The client does not.

---

## 1. The one rule that can log real users out

`mobile-native/src/api/pulseApi.ts:345–351`, inside
`performNativeSessionRefresh()`:

```typescript
if (!response.ok) {
  if (response.status === 401 || response.status === 403) {
    await clearNativeSessionCredentials();
    await setCachedSessionUser(null);
    return "invalid";
  }
  return "temporary";
}
```

> ### RULE R1 — `/api/mobile/auth/refresh` may NEVER be answered 401 or 403 by new security enforcement.
>
> A 401 or 403 there **destroys the stored credentials on the device**. The user
> is signed out and must retype a password. There is no recovery path, and
> because the binary is frozen there is no way to ship a fix to affected users.
>
> To throttle, degrade, or refuse a refresh, use **429** (or any 5xx). Both fall
> to `return "temporary"`, which preserves the sign-in.

This is the single highest-consequence finding of Stage 1. A rate limiter that
treats the refresh endpoint like any other authenticated POST, and answers it
403 on breach, would mass-log-out the active user base. `refreshNativeSession`
is called on app foreground, so the blast radius is "everyone who opened the app
during the window", not "everyone who tripped a limit".

**Verification obligation (Stage 30):** every new enforcement path must have a
test asserting it cannot emit 401/403 for path `/api/mobile/auth/refresh`.

---

## 2. Status-code contract

| Code | Client behaviour | Safe for new enforcement? |
|---|---|---|
| **401** on ordinary paths | one refresh, then one retry with `allowRefresh: false` (`pulseApi.ts:201–203`) | **Yes**, with amplification caveat §3 |
| **401/403** on `/api/mobile/auth/refresh` | **credentials wiped, user signed out** | **NO — forbidden.** See R1 |
| **403** on ordinary paths | never clears credentials; but **meaning is overloaded** | **Conditionally** — see §4 |
| **423** | understood as Private Office locked | **Yes**, but only for office-lock semantics |
| **429** | friendly "too many attempts" text; **auto-retried** on two subsystems | **Yes**, with retry caveat §5 |
| **5xx** | generic failure; `"temporary"` on refresh | Yes, but see §6 |
| **404** | `state: "NOT_FOUND"` → absent and foreign are deliberately identical (`capitalGraph.ts:464–470`) | Yes — the existing pattern for hiding cross-tenant existence |

`shouldRefresh()` (`pulseApi.ts:308–314`) excludes `/api/mobile/auth/*`,
`/api/pulse/mobile/auth/*` and `/api/messages/media/*` from the refresh-retry
path, so 401 on those is a plain error — no logout, no retry.

---

## 3. 401 costs three requests, not one

On a 401 the client refreshes and replays the original request. One user-visible
action therefore becomes **request → refresh → request**. A limiter that answers
401 when a *quota* is exceeded turns each breach into three more requests, which
deepens the very overload it is defending against.

> ### RULE R2 — Never use 401 to signal a quota, a rate limit, or a risk decision.
> 401 means "this credential is not valid". Use 429 for volume and 403 for
> authorization.

The already-shipped server honours this: `pulseApi.ts:298–302` notes messenger
media is answered 403/404 rather than 401 *specifically* so a misclassified
response cannot cost a login. New code inherits that discipline.

---

## 4. 403 is overloaded — it is not a generic "denied"

403 never logs the user out, which makes it tempting as the universal denial.
But individual surfaces have already assigned it domain meanings:

| Surface | What 403 renders as | Citation |
|---|---|---|
| Private Office upgrade | **paywall — "upgrade required"** | `src/api/privateOffice.ts:563` |
| Payout onboarding | "needs seller approval" | `src/money/moneyLayers.ts:322` |
| Reels | "account_restricted" | `src/screens/ReelsScreen.tsx:1336` |
| Entitlement checks | `"entitlement"` | `src/api/stateLanguage.ts:102` |
| Capital Graph | `DENIED` + reason, **only if** `details.state === "DENIED"` | `src/api/capitalGraph.ts:455–460`, `488` |
| Social actions | "You do not have permission to do that." | `src/social/actionGuard.ts:168` |
| Login | merged with 401 → "identifier mismatch" | `src/screens/LoginScreen.tsx:421` |

> ### RULE R3 — A security 403 must carry the discriminator the target surface already reads, or it will be misrendered.
>
> A bare security 403 on a Private Office route renders to the user as **"you
> need to upgrade"** — an invented billing prompt for what was actually a
> security decision. On Capital Graph it renders as a generic refusal because
> `details.state` is absent.
>
> Where a security denial must reach one of these surfaces, emit the envelope
> that surface parses (e.g. `{"state": "DENIED", "reason": …}` for Capital
> Graph). Where no such envelope exists, prefer 429 or 404 over a 403 that will
> be mistranslated.

This is a Hard Rule #4 concern, not merely cosmetic: a security control whose
user-visible effect is a false upsell is a control that lies about itself.

### 4.1 The login throttle already misreports itself — pre-existing defect

`bot.py:6426,6435,6452` answers a throttled or challenged login with **403** and
`"error": "login_challenge_required"` / `"login_rate_limited"`.

The shipped client **never reads those codes.** `describeLoginError()`
(`src/screens/LoginScreen.tsx:414–424`) branches on status alone, in order:

```typescript
if (error.code === "request_unreachable" || error.status === 503) …
if (error.status === 429) return translate("errors:auth.tooManyAttempts");
if (error.status === 401 || error.status === 403) return translate("errors:auth.identifierMismatch");
```

So a user who is rate-limited is told **their email or password is wrong.** They
will retry with different credentials, deepening the lockout, and the honest
message — "too many attempts" — is sitting unused one branch above, reachable
only by a **429**.

> ### RULE R8 — Login throttling must answer **429**, not 403.
> The 429 branch already renders the correct string with no client change. This
> is a server-only fix to an existing mismatch, squarely inside the mission's
> "zero new build" mandate.

Recorded as a **candidate remediation**, not yet a change: `bot.py:6426–6452` is
outside the Sentinel package and altering a live login response is a behavioural
change to the shipped auth path. It is carried into Stage 21 (detection vs
enforcement flags) to be made behind a flag with the 403 path retained as the
default until deliberately flipped.

---

## 5. 429 is safe for the session but is auto-retried

429 never touches credentials. Every surface that names it produces a "try again
in a moment" message. Two subsystems retry it *automatically*:

* `src/media/MediaUploadManager.ts:66,71–79` — `transientStatus()` includes 429;
  `MAX_RETRIES = 5`, backoff `min(8000, 500·2^attempt)` + up to 350 ms jitter.
  **One user upload can become 6 server requests.**
* `src/settings/api.ts:113–115` — 429 is explicitly *not* permanent, so the
  preference sync queue re-sends.
* `src/social/actionGuard.ts:347` — `isRecoverableSocialError()` treats 429 as
  recoverable, so queued offline actions replay.

> ### RULE R4 — 429 is the default enforcement code, but limits on upload,
> settings-sync and social-action paths must be sized for up to 6 attempts per
> user action, and the limiter must be idempotent under replay.

A limiter that counts the retries it caused will escalate a single user into a
lockout. The counter must key on the logical action, or explicitly tolerate the
retry envelope.

Note also `settings/api.ts:115`: **any 4xx other than 408/429 is treated as
permanent** and the pending settings change is dropped. A 403 on the settings
path silently discards a user's edit.

---

## 6. Transport failures are already modelled — do not invent new ones

`pulseApi.ts:186–196` converts unreachable/timeout into synthetic
`PulseApiError`s with status **503 `request_unreachable`** and **504
`request_timeout`**. `pulseApi.ts:206` synthesises **503
`session_refresh_temporary`**. None of these clear credentials.

**Correction to an earlier assumption.** These four are *synthesised by the
client itself* and are **not** codes the server can send: `session_expired`
(`pulseApi.ts:211`), `request_unreachable` and `request_timeout`
(`pulseApi.ts:186–196`), `session_refresh_temporary` (`pulseApi.ts:206`).
Emitting them from the server achieves nothing.

The codes the client genuinely reads off the wire — via
`data.error_code || data.error` (`pulseApi.ts:221`) or `details.code` — are:

| Code | Read at | Effect |
|---|---|---|
| `premium_required` | `api/cryptoPremium.ts:294`, `screens/VerificationCenterScreen.tsx:120` | paywall; triggers a refetch |
| `feature_disabled`, `not_entitled` | `privateOffice/meetings/api.ts:35–36` | ends the meeting session locally (`meetingSession.ts:149–150`) |
| `office_locked`, `not_found`, `not_admitted`, `meeting_locked`, `meeting_over`, `not_live`, `blocked`, `forbidden` | `privateOffice/meetings/api.ts:37–44` | meeting refusal classes |
| marketplace `error_code` | `api/marketplaceErrors.ts:4` | stable rejection taxonomy |
| live `error_code` | `api/live.ts:279` | surfaced as diagnostics |

> ### RULE R5 — New security codes are permitted only as *additive* fields, and
> must not collide with the meetings `CODE_MAP` vocabulary above.
>
> Reusing `feature_disabled` or `not_entitled` for a security denial on a
> meetings route will **terminate the user's live meeting** (`meetingSession.ts:149`).
>
> Unknown codes degrade safely: the client falls back to `data.message`
> (`pulseApi.ts:217–223`). But that means **every new security message is
> user-visible copy rendered to whoever tripped the control**, so denials must be
> terse and must not leak detection internals.

That last point is a security constraint in its own right: `PulseApiError` is
constructed from `data.message || data.error`, so any diagnostic string a new
control returns is rendered to the attacker. Denials must be terse.

---

## 7. Session invalidation is global, not per-screen

`App.tsx:118–120`:

```typescript
useEffect(() => registerSessionInvalidationHandler(({ path }) => {
  requestReauthentication(path.includes("/reels") ? "/pulse/reels" : "");
}), [requestReauthentication]);
```

A single `sessionInvalidationHandler` call tears down the whole authenticated
shell and pushes the reauth screen. It fires from exactly one place —
`pulseApi.ts:208–211`, when a 401 was met and the subsequent refresh returned
`invalid` or `unavailable`.

> ### RULE R6 — Revoking a session server-side is a *global* client event.
> There is no partial or per-feature logout in the shipped binary. Any control
> that revokes must be certain; a false positive costs the user their entire
> session and any unsaved state. Revocation therefore belongs behind the
> deterministic authority path, never behind a heuristic or an AI signal
> (mission final directive).

---

## 8. 423 is reserved

423 is understood by `privateFeatures.ts:110`, `privateRecords.ts:155`,
`privateOffice.ts:360,452`, `capitalGraph.ts:429`, and
`privateOffice/meetings/api.ts:55,58` — in every case as *Private Office is
locked, prompt for the second lock*. `services/private_office/security.py` lock
semantics are on the Stage 0 do-not-touch list.

> ### RULE R7 — 423 means "Private Office locked" and nothing else. New controls
> must not borrow it, or they will send users to an unlock prompt that cannot
> resolve their actual block.

---

## 9. Summary — the enforcement palette

What new server-side security is actually allowed to do to the shipped client:

1. **429** — the workhorse. Rate limits, quotas, shed load, throttle refresh.
   Size for the ×6 retry envelope on upload/settings/social paths.
2. **403 + the surface's own discriminator** — authorization denials, where the
   target surface parses a state envelope. Never bare, on paywalled surfaces.
3. **404 with `state: "NOT_FOUND"`** — cross-tenant reads, matching the existing
   "absent and foreign arrive identically" pattern.
4. **401** — only for genuinely invalid credentials, never for quota or risk.
5. **5xx** — honest failure. Safe for the session on every path including
   refresh.

Forbidden:

* 401/403 on `/api/mobile/auth/refresh` (**R1** — mass logout).
* 401 as a quota signal (**R2** — 3× amplification).
* Bare 403 on Private Office / payout / entitlement surfaces (**R3** — false
  paywall).
* 403 for login throttling (**R8** — renders as "wrong password").
* `feature_disabled` / `not_entitled` on meetings routes (**R5** — ends a live
  meeting).
* 423 for anything but the office lock (**R7**).
* Verbose denial messages (**R5** — rendered to the attacker).

---

## 10. Provenance — which client is this contract actually about?

The contract must describe the **shipped** binary, not `main`. Which build is
live in the App Store is an App Store Connect fact, not a repository fact, and I
did not have console access to read it. Rather than assume, I bounded the risk.

`mobile-native/app.json` at baseline is version **1.0.1, buildNumber 22**
(`1cc2dc01`, 2026-09-04). Candidate submitted builds in the repo run **16 → 22**
(2026-08-16 → 2026-09-04). I re-checked each rule's underlying code at every one
of those commits:

| Invariant | b16 | b18 | b20 | b21 | b22 | HEAD |
|---|---|---|---|---|---|---|
| Refresh wipes creds on 401/403 (**R1**) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Login maps 401/403 → "identifier mismatch" (**R8**) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Login has a working 429 branch (**R8**) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Media upload auto-retries 429 (**R4**) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Meetings `CODE_MAP` exists (**R5**) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

**R1, R4 and R8 hold for every build that could plausibly be live.** They are
safe to treat as binding regardless of which one Apple is serving.

**R5's meetings clause does not apply to the shipped app.**
`mobile-native/src/privateOffice/meetings/api.ts` was added by `3b2a2334`, after
build 22, and is absent from every submitted build. The "do not reuse
`feature_disabled` / `not_entitled`" warning is therefore a constraint on the
*next* release, not a live hazard today. It stays in the contract because the
mission's server changes will outlive build 22, and a rule that becomes true at
the next submission is cheaper to honour now than to retrofit.

The remaining rules (R2, R3, R6, R7) rest on files I verified byte-identical to
the baseline commit, but I did not walk them back through all seven builds; they
are lower-consequence (misrendered copy, not logout or data loss) and each is
re-checkable by the same method above if a later stage depends on one.

## 11. What this contract does not cover

Two things I could not settle from the client source and which later stages must
treat as open:

* **`Retry-After` is not read anywhere in the client.** A 429 with a
  `Retry-After` header will be retried on the client's own backoff schedule
  regardless. Server-side backoff advice cannot be delivered to this binary.
* **No client-side circuit breaker exists** beyond `MAX_RETRIES = 5` on uploads.
  A server that starts 429-ing broadly will see sustained load from settings
  sync and queued social actions until those queues drain.
