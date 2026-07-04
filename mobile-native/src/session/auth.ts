import { createContext, useContext } from "react";
import { getSession, login, logout, PulseUser, signup } from "../api/auth";
import { setSessionCookie } from "./sessionStore";

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
  const session = await getSession();
  if (session.authenticated && session.user) return { status: "signedIn", user: session.user };
  return { status: "signedOut", user: null };
}

export async function signIn(identifier: string, password: string): Promise<AuthState> {
  const session = await login(identifier, password);
  if (!session.authenticated || !session.user) return { status: "signedOut", user: null };
  return { status: "signedIn", user: session.user };
}

export async function createAccount(payload: { full_name: string; username: string; email: string; password: string }) {
  const session = await signup(payload);
  if (!session.authenticated || !session.user) return { status: "signedOut", user: null };
  return { status: "signedIn", user: session.user };
}

export async function signOut(): Promise<AuthState> {
  await logout().catch(() => undefined);
  await setSessionCookie("");
  return { status: "signedOut", user: null };
}
