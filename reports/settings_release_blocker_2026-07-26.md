# PulseSoc native — Settings release blocker

**Date:** 26 July 2026
**Build under test:** PulseSoc 1.0.1 (5), TestFlight
**Repository:** `CoinPilotX`, branch `main`
**Final judgment: PARTIAL**

---

## 1. Root cause

The reported symptom — every Settings control showing *"The requested PulseSoc
service was not found."* — has **more than one cause**, and they are independent
of each other. That matters, because fixing the loudest one would have left the
other two in place and the surface would still have failed for some users.

### 1.1 The screens were never static

The premise worth testing first was the one in the defect report: that Settings
is visual only. It is not. All nineteen settings screens call real handlers, all
twenty destinations are registered in both `AppNavigator.tsx` and
`navigation/types.ts`, and `services/pulse_settings_routes.py` is a complete,
1,191-line implementation of twelve endpoints with real persistence, revision
checking and row caps.

What made it *look* static is the preference store's optimistic design. A switch
flips immediately, a PATCH goes out 400 ms later, and — this is the part that
matters — `toSyncError` classified a 404 as a **permanent** failure. Permanent
means roll back. So the switch flipped, the request failed, the value reverted,
and the user saw the server's raw prose. Identical behaviour on thirteen screens
from a single line of classification logic reads exactly like a dead UI.

### 1.2 Branch deployment mismatch (closed at the git level, unconfirmed in production)

This was the original hypothesis and it was correct at the time it was written.
`services/pulse_settings_routes.py` lived only on
`release/undx-nexus-core-v4`, and Railway builds `main`.

It is no longer true of the repository. Commit `14261891` ("deploy: align
production backend with native release") brought the settings and seller backends
onto `main`, and all three refs now agree:

| Ref | `pulse_settings_routes.py` | Settings routes declared | Registered in `bot.py` |
|---|---|---|---|
| `origin/release/undx-nexus-core-v4` @ `984e0371` | present | 12 | yes |
| `origin/main` @ `d57d6661` | present | 12 | yes |
| local `HEAD` @ `d57d6661` | present | 12 | yes |

What remains unproven is whether Railway has actually **built** `d57d6661`. That
cannot be established from this environment — there is no network egress to
`pulsesoc.com` from the sandbox this work was done in. It is the single reason
this report says PARTIAL rather than PASS, and section 4 is the two commands that
settle it in about thirty seconds.

### 1.3 Silent blueprint registration failure (fixed)

This is the cause that would have survived the deploy, and it is the more
dangerous of the two because it produces the identical symptom from a completely
different failure.

`bot.py` registered three route packs — communications v2, presence, and mobile
settings — each inside a bare `except Exception: logging.exception(...)` that
logged and continued. If `services.pulse_settings_routes` had raised on import or
on `register()`, all twelve endpoints would have been absent from the URL map,
every Settings write would have 404'd, and `/health` would have gone on answering
`200 OK` because the process was running perfectly well. The only evidence would
have been one stack trace in the boot log, hours earlier, in a log nobody greps
during an incident.

That is now impossible to miss: registration records its outcome in
`ROUTE_PACK_STATUS`, logs `CRITICAL` with an explicit "every endpoint in this
pack will 404" on failure, and is readable without shell access through a new
unauthenticated `GET /health/routes`.

### 1.4 Device-local controls calling APIs they should never need (fixed)

The defect report's closing line predicted this, and it was there. Four
preferences describe *the handset*, not the account:

- `security.biometricUnlock` — whether Face ID is enrolled **on this phone**
- `storage.cacheLimitMb` and `storage.autoClearCache` — how much space **this
  phone** lends the media cache
- the whole `developer` group — a debugging affordance for **this build**

All four were being PATCHed to the account. That is not a harmless extra field.
Syncing `biometricUnlock` makes a hardware claim on behalf of devices that cannot
support it, and it creates a two-device loop: each phone hydrates, sees the
other's answer, and writes its own back, on every launch, forever. A user with a
phone and a tablet would watch the Face ID switch change by itself.

### 1.5 A fifth cause, found while fixing the fourth

Stripping the device-local leaves was not sufficient on its own. The store
flushes whole groups, so a change to `storage.cacheLimitMb` still shipped the
rest of the `storage` group — including `autoDownloadVideos`, which the account
does own. Under last-write-wins, dragging this phone's cache slider would
silently revert a download-policy change made on a tablet a second earlier.

The queueing decision now asks whether any **synced** leaf actually moved, not
whether the group changed. This was found by a test written for the fourth cause,
not by review.

---

## 2. Complete settings matrix

`Local` means the value never leaves the device. `Server` means it is stored on
the account. `Real API` means the control performs a real mutation through a
dedicated endpoint before any preference is written.

### Account & security

| Screen | Control | Local / server | Endpoint or action | Persistence |
|---|---|---|---|---|
| Security | Two-factor authentication | Server (real API) | `/api/account/security` | Account, mirrored into `security.twoFactorEnabled` after success |
| Security | Biometric unlock | **Local** | `expo-secure-store` keychain | This device only — never PATCHed |
| Security | Login alerts | Server | `PATCH /api/pulse/mobile/settings` | Account |
| Security | Require password for sensitive changes | Server | `PATCH /api/pulse/mobile/settings` | Account |
| Sessions & devices | Active session list | Server | `GET .../settings/sessions` | Read; throws on failure rather than showing an empty list |
| Sessions & devices | Revoke a session | Server (real API) | `POST .../settings/sessions/revoke` | Account |
| Sessions & devices | Sign out everywhere | Server (real API) | `logoutAll()` via `session/auth.ts:249` | Account; errors surface, are not swallowed |

### Notifications

| Screen | Control | Local / server | Endpoint or action | Persistence |
|---|---|---|---|---|
| Notifications | Push / email / SMS master switches | Server | `PATCH .../settings` | Account |
| Notifications | Sound, vibration, preview text | Server | `PATCH .../settings` | Account |
| Notifications | Quiet hours enable / start / end | Server | `PATCH .../settings` | Account, validated server-side (422 on an impossible window) |
| Notifications | 12 categories × 3 channels (36 switches) | Server | `PATCH .../settings` | Account |
| Notifications | Open iOS notification settings | Local | `Linking.openSettings()` | OS — no request made |

### Appearance, accessibility, language

| Screen | Control | Local / server | Endpoint or action | Persistence |
|---|---|---|---|---|
| Appearance | Theme (system / light / dark) | Server | `PATCH .../settings` | Account — follows the user to a new phone |
| Appearance | Font scale, reduce transparency, compact density | Server | `PATCH .../settings` | Account |
| Accessibility | Reduce motion, bold text, high contrast, captions, haptics, screen-reader hints | Server | `PATCH .../settings` | Account |
| Language & region | App language (11 locales incl. Arabic RTL) | Server | `PATCH .../settings` | Account |
| Language & region | Content languages, auto-translate, region, time format | Server | `PATCH .../settings` | Account |

### Storage, permissions, privacy

| Screen | Control | Local / server | Endpoint or action | Persistence |
|---|---|---|---|---|
| Storage & data | Cache limit (MB) | **Local** | AsyncStorage | This device only — never PATCHed |
| Storage & data | Auto-clear cache | **Local** | AsyncStorage | This device only — never PATCHed |
| Storage & data | Auto-download photos / videos / audio | Server | `PATCH .../settings` | Account — states an intent about the user's data plan, not a capacity of one handset |
| Storage & data | Media quality | Server | `PATCH .../settings` | Account |
| Device permissions | Camera, photos, microphone, notifications status | Local | `expo-camera`, `expo-image-picker`, `expo-notifications` | OS-owned; read-only display |
| Device permissions | Open system settings | Local | `Linking.openSettings()` | OS — no request made |
| Privacy | Account visibility, last seen, online status, read receipts | Server | `PATCH .../settings` | Account |
| Privacy | Story / live audience, tagging, mentions, direct messages | Server | `PATCH .../settings` | Account |
| Privacy | Searchable by email / phone | Server | `PATCH .../settings` | Account (both default off for a new account) |

### Relationships, data, legal

| Screen | Control | Local / server | Endpoint or action | Persistence |
|---|---|---|---|---|
| Blocked accounts | List | Server | `GET .../settings/blocked` | Read; throws rather than claiming "you have blocked nobody" |
| Blocked accounts | Unblock | Server (real API) | `DELETE .../settings/blocked` | Account |
| Muted accounts | List | Server | `GET .../settings/muted` | Read; throws on failure |
| Muted accounts | Unmute | Server (real API) | `DELETE .../settings/muted` | Account |
| Data & personalization | Request data export | Server (real API) | `POST .../settings/data-export` | Account |
| Data & personalization | Delete account | Server (real API) | `POST .../settings/delete-account` | Account, 30-day grace |
| Help | Contact support, report a problem | Server (real API) | `api/support` | Account |
| About | Version, build, API base URL | Local | `expo-constants` | Display only |
| Legal | Terms, privacy policy, licences | Local | Bundled content in `legalContent.ts` | Display only — no network at all |
| Developer | Enable, perf overlay, verbose API logging | **Local** | AsyncStorage | This device only — never PATCHed |

**Physical result** is the one column this report cannot fill in. Every row above
is established from the source and from the test suite; none of it is a
substitute for running the matrix on the device. Section 4 says how.

---

## 3. Device QA matrix — to be run on the physical device

Run after `./SETTINGS_HANDOVER.sh confirm` reports every endpoint green. For each
row: change the control, force-quit the app, relaunch, and confirm the value
held.

1. **Security** — enable login alerts; relaunch; still on. No banner.
2. **Security / biometrics** — enrol Face ID on the phone; sign in on a second
   device; relaunch both. The second device must **not** show biometrics enabled,
   and neither device may flip the other's switch.
3. **Sessions** — the list shows at least the current device. Revoke another
   session; it disappears and does not return after a pull-to-refresh.
4. **Sessions** — sign out everywhere; the app returns to sign-in.
5. **Notifications** — toggle three category channels; relaunch; all three held.
6. **Notifications** — set a quiet-hours window; relaunch; held.
7. **Appearance** — switch to dark; relaunch; still dark. Sign in on a second
   device; it is dark there too.
8. **Appearance** — drag the font scale; the whole app rescales; relaunch; held.
9. **Accessibility** — toggle all six; relaunch; all six held.
10. **Language** — switch to Arabic; the layout mirrors to RTL; relaunch; held.
11. **Storage** — drag the cache limit; relaunch; held. On a second device the
    cache limit is **unchanged**.
12. **Storage** — change auto-download to Wi-Fi only on the tablet; the phone
    shows Wi-Fi only after a pull-to-refresh.
13. **Permissions** — deny camera in iOS Settings; the row reports denied on
    return; the button opens iOS Settings.
14. **Privacy** — change all four audience selectors; relaunch; held.
15. **Blocked** — unblock somebody; they disappear; relaunch; still gone.
16. **Muted** — as above.
17. **Data** — request an export; a confirmation appears; no 404 banner.
18. **Legal** — every document opens with no network (enable airplane mode).
19. **Developer** — enable it on the phone; the second device does **not** show
    developer options.
20. **Offline** — enable airplane mode, change five settings across three
    screens, confirm the banner reads "Offline — will retry", restore the
    network, confirm all five save without further interaction.
21. **Error text** — no screen, in any state, shows the string "The requested
    PulseSoc service was not found."

---

## 4. Deployment

| | |
|---|---|
| Original SHA on `main` at the start of this mission | `a281bcbb2791acad65400d782578b8f92e791aa3` |
| SHA on `main` now (local `HEAD` == `origin/main`) | `d57d666179c4a614e0a9fc278a7aa4c74f3bb0d7` |
| SHA carrying this repair | *not yet committed — `./SETTINGS_HANDOVER.sh commit` creates it* |
| Final deployed SHA | *to be recorded after `push`* |
| Railway deployment ID | *to be recorded after `push`* |
| Migration result | none required — `pulse_settings_routes.py` creates its tables on first use and no schema change was made by this repair |

### Endpoint probes

Not run. The sandbox this work was performed in has no network egress to
`pulsesoc.com`, so no claim is made here about what production currently answers.
The probe is scripted and read-only:

```
./SETTINGS_HANDOVER.sh probe
```

It requests all twelve Settings endpoints unauthenticated and colours the result.
`401`, `403` and `405` are **passes** — each means the rule is in the URL map and
refused an anonymous caller, which is the correct answer. `404` is the failure,
and it is the exact status that produced the banner.

It also reads `GET /health/routes`, added by this change. A 404 from *that*
endpoint is itself informative: it means the running deploy predates this commit.

---

## 5. Files changed

| File | Change |
|---|---|
| `mobile-native/src/settings/schema.ts` | `DEVICE_LOCAL_KEYS`, `isDeviceLocalGroup`, `stripDeviceLocal`, `withDeviceLocal` — one place that decides what the account owns |
| `mobile-native/src/settings/store.tsx` | Queue a group only when a synced leaf moved; strip device-local leaves from every patch; restore them on every reconcile and every rollback; return early rather than sending an empty patch the server would answer 400 |
| `mobile-native/src/settings/api.ts` | `settingsMessageFor` — a distinct, actionable message per status; 404 additionally logs itself as a deployment defect |
| `mobile-native/src/screens/settings/SecuritySettingsScreen.tsx` | Corrected a comment that claimed the biometric preference was a synced mirror; it is now device-local and the comment says why |
| `bot.py` | `ROUTE_PACK_STATUS` and observable registration for all three route packs; new `GET /health/routes` |
| `mobile-native/src/settings/__tests__/schema.test.ts` | 21 tests over the device-local split, including the strip/restore round trip |
| `mobile-native/src/settings/__tests__/store.test.tsx` | 12 tests: no request for a local-only change, the mixed-group patch, the empty-patch hazard, reconcile, refresh, and rollback that must not touch biometrics |
| `mobile-native/src/settings/__tests__/api.test.ts` | 11 tests pinning the user-facing text, including one asserting the old 404 prose is gone |

Nothing outside Settings was touched. No Live, calls, seller, Business OS or
social file appears in the diff.

---

## 6. Validation

```
npx tsc --noEmit                 no errors
npx jest --silent                99 suites, 1738 tests, all passing
python3 -c "ast.parse(bot.py)"   bot.py parses OK
```

Settings specifically: 4 suites, 258 tests. 44 of those are new.

Two of the new tests failed on first run and were **not** adjusted to pass. Both
were reporting real defects in code written earlier in this mission — the whole-
group flush described in §1.5, and a mid-flight reconcile that could still
overwrite a device-local value. The store was changed; the assertions stand as
written.

The 21 pre-existing store tests were run unmodified against the changed store.
Three of them failed against a first attempt at the fix, which had compared the
outgoing patch against `confirmed.current`. Those failures were correct:
`confirmed` absorbs mid-flight edits and offline snapshots, so it is not a record
of what the server has accepted. The fix moved to the point where the touched
leaves are actually known.

---

## 7. TestFlight

No new TestFlight build was created or submitted during this repair mission.

The iOS build number is unchanged at 5. Nothing in this work requires a new
binary: the client changes ship in the next build whenever one is cut, and the
server changes take effect on deploy for the build already in testers' hands —
including the corrected error text, which is client-side and therefore the one
fix that does need a build to reach anyone.

---

## 8. Release status

PulseSoc 1.0.1 (5) must not be submitted to App Store review until all visible Settings controls are functional and physically validated.

---

## 9. Final judgment

**PARTIAL**

What is finished: every defect reachable from the source is fixed and covered by
tests. Device-owned preferences no longer touch the account. Blueprint
registration can no longer fail silently. Settings errors no longer show a
developer's 404 string to a user who tapped a switch, and a 404 now announces
itself as a deployment defect in the logs. The repository is at parity across all
three refs.

What is not: nobody has confirmed that production answers these twelve
endpoints, and nobody has run the twenty-one-row matrix on the device. Both are
blocking, both are scripted, and neither can be done from here — the first needs
network access this environment does not have, the second needs the phone.

Calling this PASS would repeat the mistake this mission was written to correct:
claiming a surface works because the code says it should.
