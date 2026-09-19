/**
 * Secure storage owner (Phase 46) — the single import point for expo-secure-store.
 *
 * Why this file is a pass-through and not a semantic wrapper
 * ---------------------------------------------------------
 * The other native owners (`clipboard.ts`, `haptics.ts`) can expose an intent
 * -shaped API because there is one sensible way to use them. Keychain storage is
 * not like that: the *options* are the security policy. `session/sessionStore`
 * holds the sign-in credential, `privateOffice/officeLock` holds the office
 * passcode, and `api/push` + `api/installationId` hold the push routing record
 * — and each combination of accessibility class, `keychainService` and
 * `requireAuthentication` means something different. Collapsing them behind a
 * single opinionated helper would either lose that distinction or grow a
 * parameter for every caller. The full inventory, with the reasoning for each
 * item, is in `docs/apple/DEVICE_SECURITY.md`.
 *
 * So the ownership this module enforces is narrower and honest: exactly one
 * module in `src/` names `expo-secure-store`, which is what the Phase 46 guard
 * in `native/__tests__/nativeOwnershipGuard.test.ts` checks. Feature modules
 * keep their own keys and options, and this is the place to add cross-cutting
 * policy (a migration, a platform fallback, an audit hook) when it is needed.
 *
 * What the separate services actually buy
 * ---------------------------------------
 * expo-secure-store already namespaces by authentication: a write lands under
 * `<keychainService>:auth` or `<keychainService>:no-auth`, and a read searches
 * `:no-auth` first, then `:auth`, then the unsuffixed legacy service
 * (`SecureStoreModule.swift` `query(with:options:requireAuthentication:)`). So
 * two *different* keys with different `requireAuthentication` values do not
 * collide even in one service. The hazard the separate services remove is the
 * *same key* written both ways: a successful `SecItemAdd` deletes the opposite
 * alias, so an unauthenticated write silently destroys the Face-ID-protected
 * copy, and while both exist the unauthenticated one always wins the read.
 * `sessionStore`'s v1 → v2 biometric migration is exactly that failure in its
 * cross-key form, and it has to delete the old key by hand because the library
 * only cleans up aliases of the key being written.
 *
 * Adding a new caller: import from here; set `keychainAccessible` explicitly
 * rather than inheriting the `whenUnlocked` default; and if the item is
 * authenticated, give it a `keychainService` nobody else uses.
 */
export * from "expo-secure-store";
