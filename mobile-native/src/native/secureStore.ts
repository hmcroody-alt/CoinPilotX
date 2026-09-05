/**
 * Secure storage owner (Phase 46) — the single import point for expo-secure-store.
 *
 * Why this file is a pass-through and not a semantic wrapper
 * ---------------------------------------------------------
 * The other native owners (`clipboard.ts`, `haptics.ts`) can expose an intent
 * -shaped API because there is one sensible way to use them. Keychain storage is
 * not like that: the *options* are the security policy. `session/sessionStore`
 * holds the sign-in credential, `api/push` holds the push token, and
 * `privateOffice/officeLock` holds the office passcode — each under its own
 * `keychainService`, and the office biometric item additionally sets
 * `requireAuthentication: true`. Those services are deliberately NOT shared:
 * iOS will not return an authenticated item without a live biometric match, and
 * mixing authenticated and unauthenticated items in one service breaks the
 * unauthenticated reads. Collapsing them behind a single opinionated helper
 * would either lose that distinction or grow a parameter for every caller.
 *
 * So the ownership this module enforces is narrower and honest: exactly one
 * module in `src/` names `expo-secure-store`, which is what the Phase 46 guard
 * in `native/__tests__/nativeOwnershipGuard.test.ts` checks. Feature modules
 * keep their own keys and options, and this is the place to add cross-cutting
 * policy (a migration, a platform fallback, an audit hook) when it is needed.
 *
 * Adding a new caller: import from here, choose a `keychainService` nobody else
 * uses, and never put an authenticated and an unauthenticated item in the same
 * service.
 */
export * from "expo-secure-store";
