import { createContext, useContext } from "react";
import { getSession, login, logout, logoutAll, PulseUser, SessionResponse, signup } from "../api/auth";
import { unregisterPushDevice } from "../api/push";
import { PulseApiError, recoverNativeSession } from "../api/pulseApi";
import {
  clearNativeSessionCredentials,
  getCachedSessionUser,
  getSessionCookie,
  getSessionEnvelope,
  NativeSessionEnvelope,
  setCachedSessionUser,
  setSessionEnvelope
} from "./sessionStore";
import { shouldRejectTemporaryQaUser } from "./qaTemporaryAccount";

export type AuthState = {
  status: "loading" | "signedIn" | "signedOut";
  user: PulseUser | null;
};

export const AuthContext = createContext<{
  authState: AuthState;
  setAuthState: (state: AuthState) => void;
  requestReauthentication: (redirectTarget?: string) => void;
}>({
  authState: { status: "loading", user: null },
  setAuthState: () => undefined,
  requestReauthentication: () => undefined
});

export function useAuth() {
  return useContext(AuthContext);
}

export async function restoreSession(): Promise<AuthState> {
  try {
    const session = await getSession();
    if (session.authenticated && session.user && Number(session.user.user_id || 0) > 0) {
      if (shouldRejectTemporaryQaUser(session.user)) return clearTemporaryQaSession();
      await setCachedSessionUser(session.user);
      return { status: "signedIn", user: session.user };
    }
    const recovery = await recoverNativeSession();
    if (recovery === "refreshed") {
      const restored = await getSession();
      if (restored.authenticated && restored.user && Number(restored.user.user_id || 0) > 0) {
        if (shouldRejectTemporaryQaUser(restored.user)) return clearTemporaryQaSession();
        await setCachedSessionUser(restored.user);
        return { status: "signedIn", user: restored.user };
      }
    }
    if (recovery === "temporary") return restoreCachedSession();
    await setCachedSessionUser(null);
    return { status: "signedOut", user: null };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 401) {
      const recovery = await recoverNativeSession();
      if (recovery === "refreshed") return restoreSession();
      if (recovery === "invalid" || recovery === "unavailable") return { status: "signedOut", user: null };
    }
    const cached = await restoreCachedSession();
    if (cached.status === "signedIn") return cached;
    throw error;
  }
}

export async function signIn(identifier: string, password: string): Promise<AuthState> {
  const session = await login(identifier, password);
  if (!session.authenticated || !session.user || Number(session.user.user_id || 0) <= 0) return { status: "signedOut", user: null };
  if (shouldRejectTemporaryQaUser(session.user)) return clearTemporaryQaSession();
  await persistSessionEnvelope(session);
  await setCachedSessionUser(session.user);
  return { status: "signedIn", user: session.user };
}

export async function createAccount(payload: { full_name: string; username: string; email: string; password: string }): Promise<AuthState> {
  const session = await signup(payload);
  if (!session.authenticated || !session.user) return { status: "signedOut", user: null };
  await persistSessionEnvelope(session);
  await setCachedSessionUser(session.user);
  return { status: "signedIn", user: session.user };
}

export async function signOut(): Promise<AuthState> {
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  await logout().catch(() => undefined);
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return { status: "signedOut", user: null };
}

export async function signOutEverywhere(): Promise<AuthState> {
  await unregisterPushDevice({ preservePreferences: true, reason: "logout" }).catch(() => undefined);
  await logoutAll();
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return { status: "signedOut", user: null };
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
    return { status: "signedIn", user: cachedUser };
  }
  return { status: "signedOut", user: null };
}

async function clearTemporaryQaSession(): Promise<AuthState> {
  await clearNativeSessionCredentials();
  await setCachedSessionUser(null);
  return { status: "signedOut", user: null };
}
