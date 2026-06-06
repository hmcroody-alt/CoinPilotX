import { create } from "zustand";
import {
  PulseUser,
  createAccount,
  getMobileSession,
  loginWithPassword,
  logoutMobileSession,
  requestPasswordRecovery
} from "../services/auth";
import { clearSessionCookie, getSessionCookie } from "../services/secureSession";

type AuthState = {
  ready: boolean;
  signedIn: boolean;
  user: PulseUser | null;
  error: string;
  restoreSession: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  signup: (payload: { full_name: string; username: string; email: string; password: string }) => Promise<void>;
  recover: (email: string) => Promise<void>;
  logout: () => Promise<void>;
};

export const useAuthStore = create<AuthState>((set) => ({
  ready: false,
  signedIn: false,
  user: null,
  error: "",
  async restoreSession() {
    try {
      const cookie = await getSessionCookie();
      if (!cookie) {
        set({ ready: true, signedIn: false, user: null });
        return;
      }
      const session = await getMobileSession();
      set({ ready: true, signedIn: session.authenticated, user: session.user });
    } catch {
      await clearSessionCookie();
      set({ ready: true, signedIn: false, user: null });
    }
  },
  async login(email, password) {
    const session = await loginWithPassword(email, password);
    set({ signedIn: session.authenticated, user: session.user, error: "" });
  },
  async signup(payload) {
    const session = await createAccount(payload);
    set({ signedIn: session.authenticated, user: session.user, error: "" });
  },
  async recover(email) {
    await requestPasswordRecovery(email);
  },
  async logout() {
    await logoutMobileSession().catch(() => undefined);
    await clearSessionCookie();
    set({ signedIn: false, user: null });
  }
}));
