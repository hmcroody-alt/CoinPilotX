import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { clearPulseSession, pulseApi } from "../api/client";

export type PulseUser = {
  user_id?: number;
  id?: number;
  email?: string;
  username?: string;
  full_name?: string;
  display_name?: string;
  avatar_url?: string;
  premium_status?: string;
};

type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  user: PulseUser | null;
};

type AuthContextValue = {
  ready: boolean;
  user: PulseUser | null;
  signedIn: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  recover: (email: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
};

type RegisterPayload = {
  full_name: string;
  username: string;
  email: string;
  password: string;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<PulseUser | null>(null);

  const refreshSession = useCallback(async () => {
    const session = await pulseApi<SessionResponse>("/api/mobile/auth/session");
    setUser(session.authenticated ? session.user : null);
  }, []);

  useEffect(() => {
    refreshSession()
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, [refreshSession]);

  const value = useMemo<AuthContextValue>(() => ({
    ready,
    user,
    signedIn: Boolean(user),
    async login(identifier, password) {
      const session = await pulseApi<SessionResponse>("/api/mobile/auth/login", {
        method: "POST",
        body: JSON.stringify({ identifier, password })
      });
      setUser(session.authenticated ? session.user : null);
    },
    async register(payload) {
      const session = await pulseApi<SessionResponse>("/api/mobile/auth/register", {
        method: "POST",
        body: JSON.stringify({ ...payload, age_confirmed: true, email_opt_in: true })
      });
      setUser(session.authenticated ? session.user : null);
    },
    async recover(email) {
      await pulseApi("/api/mobile/auth/recover", {
        method: "POST",
        body: JSON.stringify({ email })
      });
    },
    async logout() {
      await pulseApi("/api/mobile/auth/logout", { method: "POST" }).catch(() => undefined);
      await clearPulseSession();
      setUser(null);
    },
    refreshSession
  }), [ready, refreshSession, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider.");
  }
  return context;
}
