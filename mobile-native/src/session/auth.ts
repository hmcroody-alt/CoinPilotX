import { createContext, useContext } from "react";
import { getSession, login, logout, logoutAll, PulseUser, RegisterResponse, SessionResponse, signup } from "../api/auth";
import { unregisterPushDevice } from "../api/push";
import { PulseApiError, recoverNativeSession } from "../api/pulseApi";
import {
  clearActiveSessionKeepBiometric,
  clearNativeSessionCredentials,
  getBiometricSession,
  getBiometricUserId,
  getCachedSessionUser,
  getSessionCookie,
  getSessionEnvelope,
  NativeSessionEnvelope,
  setBiometricSession,
  setCachedSessionUser,
  setSessionEnvelope
} from "./sessionStore";
import { shouldRejectTemporaryQaUser } from "./qaTemporaryAccount";
import { clearUserScopedMediaState } from "../media/mediaSessionCleanup";
import { rememberAccount } from "./rememberedAccounts";
import { clearEngineerAccess, reconcileEngineerAccessOwner } from "../security/engineerAccessSession";

/**
 * Deterministic session-bootstrap phases. Every restore/sign-in/sign-out path
 * resolves to exactly one of these — no implicit "unknown" state — so the shell
 * can render a single, predictable surface for each outcome.
 *
 * - BOOTSTRAPPING     restore in flight (initial gate only)
 * - AUTHENTICATED     valid server session + user
 * - UNAUTHENTICATED   definitively signed out, no credentials to recover from
 * - SESSION_EXPIRED   had stored credentials but the server rejected/expired them
 * - RECOVERABLE_ERROR transient failure during bootstrap (offline / 5xx); retryable
 * - FATAL_ERROR       unexpected, non-recoverable bootstrap failure
 */
export type SessionPhase =
  | "BOOTSTRAPPING"
  | "AUTHENTICATED"
  | "UNAUTHENTICATED"
  | "SESSION_EXPIRED"
  | "RECOVERABLE_ERROR"
  | "FATAL_ERROR";

/**
 * Back-compat render discriminator. It is always DERIVED from `phase` through
 * `stateFor`, so the two can never desync; existing consumers that only need the
 * coarse loading/signedIn/signedOut distinction keep reading `status` unchanged.
 */
export type AuthStatus = "loading" | "signedIn" | "signedOut";

export type AuthState = {
  phase: SessionPhase;
  status: AuthStatus;
  user: PulseUser | null;
};

function statusForPhase(phase: SessionPhase): AuthStatus {
  if (phase === "BOOTSTRAPPING") return "loading";
  if (phase === "AUTHENTICATED") return "signedIn";
  return "signedOut";
}

/**
 * Single constructor for every AuthState so `status` is always in sync with `phase`.
 *
 * Engineer access is reconciled here rather than in each sign-out helper. Every
 * logout, account switch, expiry, and fatal-bootstrap path already funnels
 * through this function, so binding the grant's lifetime to it means no future
 * auth path can forget to drop it — the failure mode where a stale grant
 * survives an account switch is structurally unreachable instead of merely
 * handled in the branches we remembered.
 */
export function stateFor(phase: SessionPhase, user: PulseUser | null = null): AuthState {
  if (phase === "AUTHENTICATED") reconcileEngineerAccessOwner(Number(user?.user_id || 0));
  else clearEngineerAccess();
  return { phase, status: statusForPhase(phase), user };
}

export const authenticatedState = (user: PulseUser): AuthState => stateFor("AUTHENTICATED", user);
export const unauthenticatedState = (): AuthState => stateFor("UNAUTHENTICATED", null);
export const expiredState = (): AuthState => stateFor("SESSION_EXPIRED", null);
export const recoverableErrorState = (): AuthState => stateFor("RECOVERABLE_ERROR", null);
export const fatalErrorState = (): AuthState => stateFor("FATAL_ERROR", null);

export const AuthContext = createContext<{
  authState: AuthState;
  setAuthState: (state: AuthState) => void;
  requestReauthentication: (redirectTarget?: string) => void;
}>({
  authState: stateFor("BOOTSTRAPPING"),
  setAuthState: () => undefined,
  requestReauthentication: () => undefined
});

export function useAuth() {
  return useContext(AuthContext);
}

function isValidSession(session: SessionResponse): session is SessionResponse & { user: PulseUser } {
  return Boolean(session.authenticated && session.user && Number(session.user.user_id || 0) > 0);
}

/** True when the device holds credentials we could refresh, so a rejection means "expired" not "never signed in". */
async function hasStoredCredentials(): Promise<boolean> {
  const [cookie, envelope] = await Promise.all([getSessionCookie(), getSessionEnvelope()]);
  return Boolean(cookie || envelope?.refreshToken);
}

/** Resolve the terminal phase when a signed-out/rejected outcome is reached, keeping expired distinct from never-authed. */
function signedOutPhase(hadCredentials: boolean): AuthState {
  return hadCredentials ? expiredState() : unauthenticatedState();
}

export async function restoreSession(): Promise<AuthState> {
  const hadCredentials = await hasStoredCredentials();
  try {
    const session = await getSession();
    if (isValidSession(session)) {
      if (shouldRejectTemporaryQaUser(session.user)) return clearTemporaryQaSession();
      await setCachedSessionUser(session.user);
      return authenticatedState(session.user);
    }
    const recovery = await recoverNativeSession();
    if (recovery === "refreshed") {
      const restored = await getSession();
      if (isValidSession(restored)) {
        if (shouldRejectTemporaryQaUser(restored.user)) return clearTemporaryQaSession();
        await setCachedSessionUser(restored.user);
        return authenticatedState(restored.user);
      }
      return signedOutPhase(hadCredentials);
    }
    if (recovery === "temporary") {
      const cached = await restoreCachedSession();
      return cached.phase === "AUTHENTICATED" ? cached : recoverableErrorState();
    }
    // recovery === "invalid" | "unavailable": no live session and nothing to recover.
    await setCachedSessionUser(null);
    return signedOutPhase(hadCredentials);
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 401) {
      const recovery = await recoverNativeSession();
      if (recovery === "refreshed") return restoreSession();
      if (recovery === "invalid" || recovery === "unavailable") return signedOutPhase(hadCredentials);
      // recovery === "temporary" falls through to cache / recoverable handling below.
    }
    const cached = await restoreCachedSession();
    if (cached.phase === "AUTHENTICATED") return cached;
    if (isTransientBootstrapError(error)) return recoverableErrorState();
    return fatalErrorState();
  }
}

/** Network/server transience that a retry could clear — vs. a genuine unrecoverable fault. */
function isTransientBootstrapError(error: unknown): boolean {
  if (!(error instanceof PulseApiError)) return false;
  return error.code === "request_unreachable" || error.status === 503 || error.status >= 500;
}

export async function signIn(identifier: string, password: string): Promise<AuthState> {
  const session = await login(identifier, password);
  if (!session.authenticated || !session.user || Number(session.user.user_id || 0) <= 0) return unauthenticatedState();
  if (shouldRejectTemporaryQaUser(session.user)) return clearTemporaryQaSession();
  await persistSessionEnvelope(session);
  await setCachedSessionUser(session.user);
  await rememberAccount(session.user).catch(() => undefined);
  return authenticatedState(session.user);
}

export async function createAccount(payload: { full_name: string; username: string; email: string; password: string }): Promise<AuthState> {
  const session = await signup({ ...payload, age_confirmed: true, email_opt_in: false });
  if (!session.authenticated || !session.user) return unauthenticatedState();
  await persistSessionEnvelope(session);
  await setCachedSessionUser(session.user);
  await rememberAccount(session.user).catch(() => undefined);
  return authenticatedState(session.user);
}

/**
 * Discriminated result of the multi-step registration submit. Kept separate
 * from AuthState so the signup UI can react to the confirmation-required path
 * (the common email flow) without pretending the user is signed in.
 */
export type RegisterOutcome =
  | { kind: "signedIn"; state: AuthState }
  | { kind: "confirmEmail"; email: string; deliveryFailed: boolean; message?: string };

/**
 * Submit a registration. On the phone-less/authenticated path we persist the
 * session immediately; on the (typical) email path we return `confirmEmail`
 * so the caller can drive the verification step. Errors (duplicate handle,
 * duplicate email, weak password, …) propagate as PulseApiError for the UI to
 * map back onto the right field.
 */
export async function registerAccount(payload: {
  full_name: string;
  username: string;
  email: string;
  password: string;
  age_confirmed: boolean;
  email_opt_in: boolean;
}): Promise<RegisterOutcome> {
  const response: RegisterResponse = await signup(payload);
  if (response.authenticated && response.user) {
    await persistSessionEnvelope(response);
    await setCachedSessionUser(response.user);
    await rememberAccount(response.user).catch(() => undefined);
    return { kind: "signedIn", state: authenticatedState(response.user) };
  }
  return {
    kind: "confirmEmail",
    email: response.email || payload.email,
    deliveryFailed: Boolean(response.email_delivery_failed),
    message: response.message
  };
}

/**
 * After the email link is confirmed, exchange the held credentials for a real
 * session. Thin wrapper over signIn so the completion step reuses the exact
 * production login path (token persistence, remembered-account, QA guards).
 */
export async function finalizeConfirmedSignup(email: string, password: string): Promise<AuthState> {
  return signIn(email, password);
}

export async function signOut(): Promise<AuthState> {
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  await clearUserScopedMediaState();

  if (await shouldRetainBiometricLogin()) {
    // Ordinary sign-out for an enrolled device: keep a Face-ID-gated refresh
    // token so the user can return with Face ID. We intentionally do NOT call
    // the server logout here — that would revoke the token we just preserved.
    await clearActiveSessionKeepBiometric();
    return unauthenticatedState();
  }

  await logout().catch(() => undefined);
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return unauthenticatedState();
}

async function shouldRetainBiometricLogin(): Promise<boolean> {
  const enabledUserId = await getBiometricUserId();
  if (!enabledUserId) return false;
  const envelope = await getSessionEnvelope();
  if (envelope?.refreshToken && envelope.userId === enabledUserId) {
    await setBiometricSession({
      userId: enabledUserId,
      refreshToken: envelope.refreshToken,
      refreshTokenExpiresAt: envelope.refreshTokenExpiresAt
    });
    return true;
  }
  // Fall back to an already-stashed biometric token (e.g. session already expired).
  const saved = await getBiometricSession();
  return Boolean(saved?.refreshToken && saved.userId === enabledUserId);
}

export async function signOutEverywhere(): Promise<AuthState> {
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  await logoutAll();
  await clearUserScopedMediaState();
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return unauthenticatedState();
}

async function persistSessionEnvelope(session: SessionResponse) {
  const userId = Number(session.user?.user_id || 0);
  if (!userId || !session.refresh_token) return;
  const now = Date.now();
  const envelope: NativeSessionEnvelope = {
    version: 1,
    userId,
    accessToken: String(session.access_token || ""),
    accessTokenExpiresAt: now + Number(session.access_token_expires_in || 0) * 1000,
    refreshToken: session.refresh_token,
    refreshTokenExpiresAt: now + Number(session.refresh_token_expires_in || 0) * 1000
  };
  await setSessionEnvelope(envelope);
}

async function restoreCachedSession(): Promise<AuthState> {
  const [cookie, envelope, cachedUser] = await Promise.all([getSessionCookie(), getSessionEnvelope(), getCachedSessionUser<PulseUser>()]);
  const userId = Number(cachedUser?.user_id || 0);
  if (shouldRejectTemporaryQaUser(cachedUser)) return clearTemporaryQaSession();
  if ((cookie || envelope?.refreshToken) && cachedUser && userId > 0 && (!envelope?.userId || envelope.userId === userId)) {
    return authenticatedState(cachedUser);
  }
  return unauthenticatedState();
}

async function clearTemporaryQaSession(): Promise<AuthState> {
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return unauthenticatedState();
}
