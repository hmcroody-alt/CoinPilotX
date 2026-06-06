import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import { clearStoredSession, getStoredSession, pulseApi } from "../api/client";

type AuthContextValue = {
  ready: boolean;
  signedIn: boolean;
  login(email: string, password: string): Promise<void>;
  register(payload: { full_name: string; username: string; email: string; password: string }): Promise<void>;
  recover(email: string): Promise<void>;
  logout(): Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    getStoredSession()
      .then(async session => {
        if (!session) return setSignedIn(false);
        try {
          const data = await pulseApi<{ authenticated: boolean }>("/api/mobile/auth/session");
          setSignedIn(!!data.authenticated);
        } catch {
          await clearStoredSession();
          setSignedIn(false);
        }
      })
      .finally(() => setReady(true));
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    ready,
    signedIn,
    async login(email, password) {
      await pulseApi("/api/mobile/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setSignedIn(true);
    },
    async register(payload) {
      await pulseApi("/api/mobile/auth/register", {
        method: "POST",
        body: JSON.stringify({ ...payload, age_confirmed: true }),
      });
      setSignedIn(true);
    },
    async recover(email) {
      await pulseApi("/api/mobile/auth/recover", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
    },
    async logout() {
      await pulseApi("/api/mobile/auth/logout", { method: "POST" }).catch(() => undefined);
      await clearStoredSession();
      setSignedIn(false);
    },
  }), [ready, signedIn]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider.");
  return value;
}
