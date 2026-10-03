import { cancelMessageReconciliation } from "../core/messageNotificationReconciliation";
import { createContext, useContext } from "react";
import {
  acceptLegalDocuments,
  getSession,
  LegalAcceptanceChallenge,
  login,
  logout,
  logoutAll,
  PulseUser,
  RegisterResponse,
  SessionResponse,
  signup
} from "../api/auth";
import { unregisterPushDevice } from "../api/push";
import { revokeVoipPushRegistration } from "../calls/callKitBridge";
import { PulseApiError, recoverNativeSession } from "../api/pulseApi";
import {
  clearActiveSessionKeepBiometric,
  clearNativeSessionCredentials,
  getBiometricUserId,
  getCachedSessionUser,
  getSessionCookie,
  getSessionEnvelope,
  NativeSessionEnvelope,
  setCachedSessionUser,
  setSessionEnvelope,
  writeBiometricCredential
} from "./sessionStore";
import { shouldRejectTemporaryQaUser } from "./qaTemporaryAccount";
import { setMediaCacheScope } from "../media/mediaCache";
import { setOutboxScope } from "../core/mutations/outbox";
import { clearUserScopedMediaState } from "../media/mediaSessionCleanup";
import { rememberAccount } from "./rememberedAccounts";
import { loadCanonicalTier, resetCanonicalTier } from "../entitlements/useCanonicalTier";

/**
 * Deterministic session-bootstrap phases. Every restore/sign-in/sign-out path
 * resolves to exactly one of these — no implicit "unknown" state — so the shell
 * can render a single, predictable surface for each outcome.
 *
 * - BOOTSTRAPPING     restore in flight (initial gate only)
 * - AUTHENTICATED     valid server session + user
 * - UNAUTHENTICATED   definitively signed out, no credentials to recover from
 * - SESSION_EXPIRED   had stored credentials but the server rejected/expired them
 * - LEGAL_ACCEPTANCE_REQUIRED
 *                     credentials are good and the account is in good standing,
 *                     but the server has not recorded this account's acceptance
 *                     of the document versions now in force
 * - RECOVERABLE_ERROR transient failure during bootstrap (offline / 5xx); retryable
 * - FATAL_ERROR       unexpected, non-recoverable bootstrap failure
 */
export type SessionPhase =
  | "BOOTSTRAPPING"
  | "AUTHENTICATED"
  | "UNAUTHENTICATED"
  | "SESSION_EXPIRED"
  | "LEGAL_ACCEPTANCE_REQUIRED"
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
  /** Set only on LEGAL_ACCEPTANCE_REQUIRED; what the acceptance step must show. */
  legalAcceptance?: LegalAcceptanceChallenge | null;
};

function statusForPhase(phase: SessionPhase): AuthStatus {
  if (phase === "BOOTSTRAPPING") return "loading";
  if (phase === "AUTHENTICATED") return "signedIn";
  return "signedOut";
}

/**
 * Single constructor for every AuthState so `status` is always in sync with `phase`.
 *
 * Because it is the *only* constructor, it is also the one place that observes
 * every identity transition — sign-in, restore-from-keychain, expiry, sign-out —
 * which is why the media cache scope is set here rather than at the six call
 * sites that produce states. Missing one of those would silently write the next
 * user's downloads into the previous user's cache directory, and the failure
 * would be invisible until someone went looking for it (Stage 35).
 *
 * The mutation outbox is scoped here for the same reason and a sharper one: a
 * queue carried across an account switch would not merely expose stale data, it
 * would send the previous user's unsent words from the new user's account.
 */
export function stateFor(
  phase: SessionPhase,
  user: PulseUser | null = null,
  legalAcceptance: LegalAcceptanceChallenge | null = null
): AuthState {
  const userId = Number((user as { user_id?: number; id?: number } | null)?.user_id ?? (user as { id?: number } | null)?.id ?? 0);
  const scopeId = phase === "AUTHENTICATED" && userId > 0 ? userId : null;
  setMediaCacheScope(scopeId);
  setOutboxScope(scopeId);
  cancelMessageReconciliation();
  return { phase, status: statusForPhase(phase), user, legalAcceptance };
}

export const authenticatedState = (user: PulseUser): AuthState => stateFor("AUTHENTICATED", user);
export const unauthenticatedState = (): AuthState => stateFor("UNAUTHENTICATED", null);
export const expiredState = (): AuthState => stateFor("SESSION_EXPIRED", null);

/**
 * Credentials accepted, admission withheld until the account's acceptance of
 * the current document versions is on file.
 *
 * `status` resolves to "signedOut" through `statusForPhase`'s default branch,
 * and that is the design rather than a side effect: the shell renders
 * `AuthNavigator` for every non-AUTHENTICATED phase, so the application is
 * structurally unreachable here instead of merely unrouted. `scopeId` stays
 * null for the same reason — a member held at this step gets no cache scope and
 * no outbox, so nothing of theirs can be written before they are admitted.
 *
 * `user` is carried for display only. On the restored-session path the device
 * still holds a valid bearer token, which is what the acceptance call
 * authenticates with; on the login path it holds only the server's ticket.
 */
export const legalAcceptanceState = (challenge: LegalAcceptanceChallenge, user: PulseUser | null = null): AuthState =>
  stateFor("LEGAL_ACCEPTANCE_REQUIRED", user, challenge);
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

function normalizeSessionUser(user: unknown): PulseUser | null {
  if (!user || typeof user !== "object") return null;
  const input = user as Record<string, unknown>;
  const userId = Number(input.user_id ?? input.id ?? 0);
  if (!Number.isFinite(userId) || userId <= 0) return null;
  return {
    ...(input as PulseUser),
    user_id: userId,
    username: String(input.username || "").replace(/^@/, ""),
    display_name: String(input.display_name || input.full_name || input.username || "PulseSoc member"),
    full_name: String(input.full_name || input.display_name || ""),
    email: String(input.email || ""),
    avatar_url: String(input.avatar_url || input.avatar_thumbnail_url || ""),
    // Carried verbatim, with no fallback to `subscription_status`. The two
    // fields speak different vocabularies — `subscription_status` is Stripe's
    // ("trialing", "past_due", "canceled") while `premium_status` is the
    // platform's ({active, founder, lifetime, trial}) — so the old `||` wrote
    // values into this field that the server never puts there, and every reader
    // downstream then had to guess at them. An absent status stays empty;
    // capability decisions come from `entitlements/canonicalTier`, not here.
    premium_status: String(input.premium_status || ""),
    account_status: String(input.account_status || "active")
  };
}

function sessionUser(session: SessionResponse): PulseUser | null {
  if (!session.authenticated) return null;
  return normalizeSessionUser(session.user);
}

function isValidSession(session: SessionResponse): session is SessionResponse & { user: PulseUser } {
  return Boolean(sessionUser(session));
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

/**
 * Turn a valid server session into the phase it actually earns.
 *
 * A session can be entirely valid and still not admitted: `/session` reports
 * `legal_acceptance_required` when the documents in force have moved past what
 * this account has on file, which is how a member who was signed in before a
 * revision finds out. The server informs here rather than revoking, so this is
 * the one place that decides what the app does about it — and it holds the
 * member at the acceptance step rather than signing them out, because their
 * credentials are fine and a sign-out would make them retype a password to
 * answer a question that has nothing to do with their password.
 *
 * A session reporting the flag without any documents is treated as admitted.
 * The flag alone is not actionable: there would be nothing to show and no way
 * to proceed, so refusing on it would strand the member with no exit.
 */
async function admitLiveSession(session: SessionResponse, user: PulseUser): Promise<AuthState> {
  await setCachedSessionUser(user);
  const challenge = session.legal_acceptance;
  if (session.legal_acceptance_required === true && challenge?.documents?.length) {
    return legalAcceptanceState(challenge, user);
  }
  return authenticatedState(user);
}

export async function restoreSession(): Promise<AuthState> {
  const hadCredentials = await hasStoredCredentials();
  try {
    const session = await getSession();
    const liveUser = sessionUser(session);
    if (liveUser) {
      if (shouldRejectTemporaryQaUser(liveUser)) return clearTemporaryQaSession();
      return admitLiveSession(session, liveUser);
    }
    const recovery = await recoverNativeSession();
    if (recovery === "refreshed") {
      const restored = await getSession();
      const restoredUser = sessionUser(restored);
      if (restoredUser) {
        if (shouldRejectTemporaryQaUser(restoredUser)) return clearTemporaryQaSession();
        return admitLiveSession(restored, restoredUser);
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

/**
 * The acceptance refusal, read off a rejected request.
 *
 * Lives here rather than in LoginScreen so it covers every way this app reaches
 * `signIn` — the password form, the Face ID path, and the post-confirmation
 * finalize — instead of the one call site somebody remembered. A gate that each
 * caller has to opt into is a gate that a fourth caller silently skips.
 *
 * Returns null unless the server both named this rejection and described what is
 * outstanding. A 403 we cannot render has to stay an error the user sees, not a
 * blank acceptance screen with no documents and no way forward.
 */
function legalAcceptanceRefusal(error: unknown): LegalAcceptanceChallenge | null {
  if (!(error instanceof PulseApiError)) return null;
  if (error.code !== "legal_acceptance_required") return null;
  const challenge = error.details?.legal_acceptance as LegalAcceptanceChallenge | undefined;
  if (!challenge?.ticket || !Array.isArray(challenge.documents) || challenge.documents.length === 0) return null;
  return challenge;
}

export async function signIn(identifier: string, password: string): Promise<AuthState> {
  // A cached tier from the previous account must not survive into this one.
  resetCanonicalTier();
  let session: SessionResponse;
  try {
    session = await login(identifier, password);
  } catch (error) {
    const challenge = legalAcceptanceRefusal(error);
    if (challenge) return legalAcceptanceState(challenge);
    throw error;
  }
  const user = sessionUser(session);
  if (!user) return unauthenticatedState();
  if (shouldRejectTemporaryQaUser(user)) return clearTemporaryQaSession();
  await persistSessionEnvelope({ ...session, user });
  await setCachedSessionUser(user);
  await rememberAccount(user).catch(() => undefined);
  // Ask for THIS member's entitlement now that the envelope is persisted, so
  // the request carries the new token. Without it the reset above leaves the
  // shared answer at "unavailable" until some surface happens to mount and ask,
  // and a premium member's first seconds after signing in are spent looking at
  // a product that cannot confirm they paid for it.
  void refreshEntitlementAfterSignIn();
  return authenticatedState(user);
}

/**
 * Record acceptance and finish admission in one server round trip.
 *
 * The server does both: it writes the ledger row and, on the login path, issues
 * the session the gate withheld. That ordering is the point — there is no moment
 * where this app is admitted and the record is not yet written, because the app
 * learns it is admitted from the same response that wrote it.
 *
 * Throws on refusal, including the one refusal worth distinguishing: if the
 * documents were revised while the member was reading them, the server rejects
 * the stale ticket and sends a fresh challenge. The caller re-enters the step
 * against the new versions rather than recording agreement to superseded text.
 */
export async function completeLegalAcceptance(challenge: LegalAcceptanceChallenge): Promise<AuthState> {
  const session = await acceptLegalDocuments(challenge.ticket);
  const user = sessionUser(session);
  if (!user) return unauthenticatedState();
  if (shouldRejectTemporaryQaUser(user)) return clearTemporaryQaSession();
  // Present on the login path and absent on the restored-session path, where the
  // device already holds a live envelope that this call did not replace.
  await persistSessionEnvelope({ ...session, user });
  await setCachedSessionUser(user);
  await rememberAccount(user).catch(() => undefined);
  void refreshEntitlementAfterSignIn();
  return authenticatedState(user);
}

export async function createAccount(payload: { full_name: string; username: string; email: string; password: string }): Promise<AuthState> {
  resetCanonicalTier();
  const session = await signup({ ...payload, age_confirmed: true, email_opt_in: false });
  const user = sessionUser(session);
  if (!user) return unauthenticatedState();
  await persistSessionEnvelope({ ...session, user });
  await setCachedSessionUser(user);
  await rememberAccount(user).catch(() => undefined);
  // A new account starts on the signup trial grant, which is a real
  // entitlement the server has already written. Not asking for it would show a
  // brand-new member the upsell for something they already hold.
  void refreshEntitlementAfterSignIn();
  return authenticatedState(user);
}

/**
 * Re-read the canonical tier for the member who just authenticated.
 *
 * Failures are swallowed on purpose: the shared cache already holds the honest
 * "unavailable" answer from the reset, every premium gate re-asks on mount and
 * on foreground, and a rejected promise here would surface as an unhandled
 * rejection during sign-in — noise about a condition the app already renders
 * truthfully.
 */
function refreshEntitlementAfterSignIn(): Promise<unknown> {
  return loadCanonicalTier().catch(() => undefined);
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
  const user = sessionUser(response);
  if (user) {
    await persistSessionEnvelope({ ...response, user });
    await setCachedSessionUser(user);
    await rememberAccount(user).catch(() => undefined);
    return { kind: "signedIn", state: authenticatedState(user) };
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

export type SignOutOptions = {
  /**
   * Fully clear biometric enrollment and every stored credential instead of
   * keeping a Face-ID-gated refresh token. Required when the account is going
   * away (account deletion): keeping a live refresh token bound to Face ID for
   * an account pending deletion would let a biometric unlock silently resume —
   * and thereby cancel — the deletion.
   */
  clearBiometrics?: boolean;
};

export async function signOut(options: SignOutOptions = {}): Promise<AuthState> {
  // Drop the entitlement answer with the session: keeping it would hand this
  // member's tier to whoever signs in next on this device.
  resetCanonicalTier();
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  // The VoIP token is a *separate* credential in a separate table, so dropping the alert
  // registration above does not touch it. Left behind it does active harm rather than
  // nothing: the backend suppresses the incoming-call alert push for any device holding an
  // active VoIP token, and it will still ring this handset through CallKit — showing a
  // stranger's name and photo on the lock screen of a phone that has been signed out.
  await revokeVoipPushRegistration("logout").catch(() => undefined);
  await clearUserScopedMediaState();

  if (!options.clearBiometrics && (await shouldRetainBiometricLogin())) {
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
    // Re-store the token we are about to drop from the live envelope. If the
    // keychain refuses, say so: the caller then falls through to a full clear,
    // which is the honest outcome — better a password sign-in than a Face ID
    // button backed by nothing.
    return writeBiometricCredential({
      userId: enabledUserId,
      refreshToken: envelope.refreshToken,
      refreshTokenExpiresAt: envelope.refreshTokenExpiresAt
    });
  }
  // No live token to re-stash, so the already-stored credential is what Face ID
  // will use. We check the enrollment marker rather than reading the credential
  // itself: that read is biometrically gated, and a Face ID prompt in the middle
  // of "Sign out" would be both baffling and cancellable. The marker is only
  // ever written alongside a confirmed credential write, so it is a truthful
  // stand-in here.
  return true;
}

export async function signOutEverywhere(): Promise<AuthState> {
  resetCanonicalTier();
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  // Ordered before `logoutAll()` for the same reason the alert revoke is: both calls need a
  // live session to authenticate, and `logoutAll` invalidates it.
  await revokeVoipPushRegistration("logout_everywhere").catch(() => undefined);
  await logoutAll();
  await clearUserScopedMediaState();
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return unauthenticatedState();
}

/**
 * Exported so the QA simulator sign-in finishes the same way the real one does.
 * It is the only place the wire field names are mapped onto the envelope, and a
 * second copy of that mapping is how the two would drift apart.
 */
export async function persistSessionEnvelope(session: SessionResponse) {
  const userId = Number(session.user?.user_id ?? (session.user as Record<string, unknown> | undefined)?.id ?? 0);
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
  const user = normalizeSessionUser(cachedUser);
  const userId = Number(user?.user_id || 0);
  if (shouldRejectTemporaryQaUser(user)) return clearTemporaryQaSession();
  if ((cookie || envelope?.refreshToken) && user && userId > 0 && (!envelope?.userId || envelope.userId === userId)) {
    return authenticatedState(user);
  }
  return unauthenticatedState();
}

async function clearTemporaryQaSession(): Promise<AuthState> {
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  // Same reason as every other session end: the credential clear is only half of
  // a sign-out, and the caches this leaves behind are stored under bare keys that
  // the next account reads straight back. A QA account's leftovers reaching a
  // real one is a smaller blast radius than the reverse, not a different bug.
  await clearUserScopedMediaState();
  return unauthenticatedState();
}
