/**
 * TEMPORARY development-only passcode path for engineer access.
 *
 * ## Why this exists
 *
 * The engineer gate is server-backed: the client posts to
 * `/api/internal/engineer-access/verify` and the server owns the identity
 * check, the passcode hash, the lockout ladder and the signed grant. That
 * design is intact and is not being weakened here.
 *
 * The route, however, is not deployed. Production answers 404 for both
 * `/api/internal/engineer-access/verify` and `.../session`, while
 * `/api/pulse/business/construction-access` answers 401 — so the gate is live
 * but the thing that opens it is not. `verifyEngineerAccess` correctly treats
 * any non-200 as "not authorized", which means no passcode can unlock, and the
 * modal shows the same strict denial it would show for a wrong one. That is the
 * defect: not the UI, not the comparison, not the lockout.
 *
 * This module unblocks internal device testing until the server half ships.
 *
 * ## Why it cannot reach a public build
 *
 * `ENABLED` is resolved once at module load from two build-time constants:
 * `__DEV__`, which Metro inlines as `false` in any Release bundle, and a
 * *statically spelled* `process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK`.
 *
 * The static spelling is load-bearing. Expo's babel plugin only substitutes
 * `process.env.EXPO_PUBLIC_X` when the key is a string literal — a computed
 * `process.env[name]` lookup is left alone and reads `undefined` on device,
 * because nothing populates `process.env` in a release bundle. Writing the name
 * out is what makes the flag exist at runtime at all.
 *
 * The production EAS profile does not define the variable, so it inlines to
 * `undefined`, `ENABLED` folds to `false`, and every entry point below returns
 * before reaching the comparison.
 */

import { isFlagValueOn } from "../core/envFlag";

/**
 * The interim passcode, per the current development directive. It is a
 * placeholder for the server's PBKDF2 hash and must be rotated out the moment
 * `/api/internal/engineer-access/verify` is deployed — at which point this
 * whole module should be deleted rather than reconfigured.
 */
const DEVELOPMENT_PASSCODE = "70041852";

const ENABLED: boolean =
  __DEV__ === true || isFlagValueOn(process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK);

/** Lifetime of a locally-issued grant. Matches the server's 30-minute ceiling. */
export const LOCAL_GRANT_TTL_SECONDS = 1800;

/**
 * Scope a local grant claims. Deliberately narrower than the server's list and
 * marked, so a screen that starts making scope decisions can tell a real
 * capability from this stand-in.
 */
export const LOCAL_GRANT_SCOPE = ["business_os", "marketplace_selling", "marketplace_buying", "local_dev"];

export function engineerDevFallbackEnabled(): boolean {
  return ENABLED;
}

/**
 * Whether the entered digits are the development passcode.
 *
 * `trim()` is applied because the value arrives from a `TextInput`; nothing
 * else is normalised, so a wrong passcode with the right length is still wrong.
 * Returns false — never throws — for null, undefined and non-strings, so a
 * malformed value can never take the error path and be mistaken for a server
 * outage.
 */
export function devFallbackAccepts(entered: unknown): boolean {
  if (!ENABLED) return false;
  return String(entered ?? "").trim() === DEVELOPMENT_PASSCODE;
}
