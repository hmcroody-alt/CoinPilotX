/**
 * Livestream recovery policy.
 *
 * Pure decision logic, kept free of Agora and React Native imports so the
 * whole matrix is unit-testable without a device.
 *
 * Two problems this solves:
 *
 * 1. TERMINAL vs RECOVERABLE. The previous broadcast hook set state on
 *    `Disconnected` and stopped. It never retried a recoverable drop, and had no
 *    concept of a terminal one, so a network blip and a host-ended room were
 *    indistinguishable. Retrying a terminal state forever is just as wrong as
 *    not retrying a recoverable one.
 *
 * 2. TOKEN EXPIRY. Agora reuses the join token on reconnect. Guest tokens are
 *    minted with a 30 minute TTL (bot.py: `ttl_seconds=1800 if is_guest_request`),
 *    so a guest in a 40 minute broadcast who hits a blip reconnects with an
 *    already-expired token and can never rejoin. Hosts get 2h, viewers 1h - the
 *    same failure, just later. The client must refresh before expiry.
 */

export type DisconnectClassification = "recoverable" | "terminal" | "unknown";

/**
 * Agora disconnect reasons plus PulseSoc's own internal reasons.
 * Terminal reasons represent an authorization or lifecycle decision that
 * retrying cannot reverse.
 */
const TERMINAL_REASONS = new Set([
  "room_closed",
  "roomclosed",
  "participant_removed",
  "participantremoved",
  "duplicate_identity",
  "duplicateidentity",
  "server_shutdown",
  "join_failure",
  "joinfailure",
  "host_ended",
  "authorization_revoked",
  "token_expired",
  "account_logout",
  "live_ended",
  "user_rejected",
  "user_unavailable"
]);

const RECOVERABLE_REASONS = new Set([
  "signal_close",
  "signalclose",
  "client_initiated_reconnect",
  "state_mismatch",
  "statemismatch",
  "network_error",
  "connection_lost",
  "migration",
  "buffer_full",
  "unknown_reason"
]);

export function classifyDisconnect(reason: unknown): DisconnectClassification {
  const normalized = String(reason ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  if (!normalized) return "unknown";
  if (TERMINAL_REASONS.has(normalized)) return "terminal";
  if (RECOVERABLE_REASONS.has(normalized)) return "recoverable";
  return "unknown";
}

export function isTerminalDisconnect(reason: unknown): boolean {
  return classifyDisconnect(reason) === "terminal";
}

export const LIVE_MAX_RECONNECT_ATTEMPTS = 6;
const BASE_RETRY_DELAY_MS = 500;
const MAX_RETRY_DELAY_MS = 15_000;

/**
 * Bounded exponential backoff. Returns null once the attempt budget is spent,
 * which the caller must treat as terminal - this is what stops the endless
 * reconnect loop the mission calls out.
 */
export function nextReconnectDelayMs(attempt: number): number | null {
  if (!Number.isFinite(attempt) || attempt < 1) return BASE_RETRY_DELAY_MS;
  if (attempt > LIVE_MAX_RECONNECT_ATTEMPTS) return null;
  return Math.min(BASE_RETRY_DELAY_MS * 2 ** (attempt - 1), MAX_RETRY_DELAY_MS);
}

export function shouldAttemptReconnect(reason: unknown, attempt: number): boolean {
  if (isTerminalDisconnect(reason)) return false;
  return nextReconnectDelayMs(attempt) !== null;
}

/**
 * Refresh margin. A token is refreshed this long before it actually expires so
 * a reconnect that happens near the boundary still carries a valid token.
 */
export const TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000;

export function parseTokenExpiry(expiresAt: unknown): number | null {
  if (typeof expiresAt === "number" && Number.isFinite(expiresAt)) {
    // Accept both seconds and milliseconds since epoch.
    return expiresAt > 1e11 ? expiresAt : expiresAt * 1000;
  }
  const parsed = Date.parse(String(expiresAt ?? ""));
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * True when the token is within the refresh margin of expiry, or already
 * expired, or carries no usable expiry at all (fail safe: refresh rather than
 * reconnect with something unusable).
 */
export function shouldRefreshToken(expiresAt: unknown, now: number = Date.now()): boolean {
  const expiry = parseTokenExpiry(expiresAt);
  if (expiry === null) return true;
  return expiry - now <= TOKEN_REFRESH_MARGIN_MS;
}

export function millisecondsUntilRefresh(expiresAt: unknown, now: number = Date.now()): number {
  const expiry = parseTokenExpiry(expiresAt);
  if (expiry === null) return 0;
  return Math.max(0, expiry - now - TOKEN_REFRESH_MARGIN_MS);
}

/**
 * Audio route changes worth reacting to. `oldDeviceUnavailable` (Bluetooth
 * headphones powering off) is the one that silently killed Live audio: iOS
 * moves output to the receiver and PulseSoc never reapplied the speaker route.
 */
export type RouteChangeReason =
  | "unknown"
  | "newDeviceAvailable"
  | "oldDeviceUnavailable"
  | "categoryChange"
  | "override"
  | "wakeFromSleep"
  | "noSuitableRouteForCategory"
  | "routeConfigurationChange";

const ROUTE_REASONS_REQUIRING_REAPPLY = new Set<RouteChangeReason>([
  "oldDeviceUnavailable",
  "newDeviceAvailable",
  "categoryChange",
  "noSuitableRouteForCategory",
  "wakeFromSleep"
]);

export function shouldReapplyAudioRoute(reason: unknown): boolean {
  return ROUTE_REASONS_REQUIRING_REAPPLY.has(String(reason ?? "unknown") as RouteChangeReason);
}

/**
 * After an AVAudioSession interruption ends, iOS only permits resuming when it
 * sets the `shouldResume` option. Resuming without it throws and leaves the
 * session in an inconsistent state.
 */
export function shouldResumeAfterInterruption(options: { shouldResume?: boolean } | null | undefined): boolean {
  return Boolean(options?.shouldResume);
}
