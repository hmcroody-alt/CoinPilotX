import { createContext, useContext } from "react";
import { getSession, login, logout, logoutAll, PulseUser, signup } from "../api/auth";
import { getCachedSessionUser, getSessionCookie, setCachedSessionUser, setSessionCookie } from "./sessionStore";

export type AuthState = {
  status: "loading" | "signedIn" | "signedOut";
  user: PulseUser | null;
};

export const AuthContext = createContext<{
  authState: AuthState;
  setAuthState: (state: AuthState) => void;
}>({
  authState: { status: "loading", user: null },
  setAuthState: () => undefined
});

export function useAuth() {
  return useContext(AuthContext);
}

export async function restoreSession(): Promise<AuthState> {
  try {
    const session = await getSession();
    if (session.authenticated && session.user && Number(session.user.user_id || 0) > 0) {
      await setCachedSessionUser(session.user);
      return { status: "signedIn", user: session.user };
    }
    await setCachedSessionUser(null);
    return { status: "signedOut", user: null };
  } catch (error) {
    const [cookie, cachedUser] = await Promise.all([getSessionCookie(), getCachedSessionUser<PulseUser>()]);
    if (cookie && cachedUser && Number(cachedUser.user_id || 0) > 0) return { status: "signedIn", user: cachedUser };
    throw error;
  }
}

export async function signIn(identifier: string, password: string): Promise<AuthState> {
  const session = await login(identifier, password);
  if (!session.authenticated || !session.user || Number(session.user.user_id || 0) <= 0) return { status: "signedOut", user: null };
  await setCachedSessionUser(session.user);
  return { status: "signedIn", user: session.user };
}

export async function createAccount(payload: { full_name: string; username: string; email: string; password: string }): Promise<AuthState> {
  const session = await signup(payload);
  if (!session.authenticated || !session.user) return { status: "signedOut", user: null };
  await setCachedSessionUser(session.user);
  return { status: "signedIn", user: session.user };
}

export async function signOut(): Promise<AuthState> {
  await logout().catch(() => undefined);
  await setSessionCookie("");
  await setCachedSessionUser(null);
  return { status: "signedOut", user: null };
}

export async function signOutEverywhere(): Promise<AuthState> {
  await logoutAll();
  await setSessionCookie("");
  await setCachedSessionUser(null);
  return { status: "signedOut", user: null };
}
