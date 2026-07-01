import { create } from "zustand";
import {
  PulseUser,
  changeEmailConfirmation,
  checkEmailConfirmationStatus,
  confirmEmailToken,
  createAccount,
  getMobileSession,
  loginWithPassword,
  logoutMobileSession,
  refreshMobileSession,
  requestPasswordRecovery,
  resendEmailConfirmation,
  resetPasswordWithToken
} from "../services/auth";
import { PulseApiError, isOfflineError } from "../services/apiClient";
import { ProfileBootstrap, loadProfileBootstrap } from "../services/profileBootstrap";
import { unregisterPushNotifications } from "../services/push";
import { clearPersistedSessionCookie, clearSecureSession, getPreferredLanguage, getRefreshToken, getSessionCookie, setPreferredLanguage, setRefreshToken } from "../services/secureSession";

type AuthState = {
  ready: boolean;
  signedIn: boolean;
  user: PulseUser | null;
  bootstrap: ProfileBootstrap | null;
  pendingConfirmationEmail: string;
  offline: boolean;
  error: string;
  restoreSession: () => Promise<void>;
  refreshSession: () => Promise<void>;
  login: (identifier: string, password: string, remember: boolean) => Promise<void>;
  signup: (payload: { full_name: string; username: string; email: string; password: string }) => Promise<void>;
  recover: (email: string) => Promise<void>;
  resendConfirmation: (email?: string) => Promise<string>;
  changeConfirmationEmail: (oldEmail: string, newEmail: string, password: string) => Promise<string>;
  refreshConfirmationStatus: (email?: string) => Promise<boolean>;
  confirmEmail: (token: string) => Promise<string>;
  resetPassword: (token: string, password: string) => Promise<string>;
  loadBootstrap: () => Promise<void>;
  logout: () => Promise<void>;
};

async function persistPreferredLanguage(user: PulseUser | null | undefined) {
  if (user?.preferred_language) await setPreferredLanguage(user.preferred_language);
}

export const useAuthStore = create<AuthState>((set) => ({
  ready: false,
  signedIn: false,
  user: null,
  bootstrap: null,
  pendingConfirmationEmail: "",
  offline: false,
  error: "",
  async restoreSession() {
    try {
      const cookie = await getSessionCookie();
      const refreshToken = await getRefreshToken();
      if (!cookie && !refreshToken) {
        set({ ready: true, signedIn: false, user: null });
        return;
      }
      const session = cookie ? await getMobileSession() : await refreshMobileSession(refreshToken);
      if (session.refresh_token) await setRefreshToken(session.refresh_token);
      await persistPreferredLanguage(session.user);
      set({ ready: true, signedIn: session.authenticated, user: session.user, offline: false });
      if (session.authenticated) {
        const bootstrap = await loadProfileBootstrap(session.user);
        await persistPreferredLanguage(bootstrap.user || session.user);
        set({ bootstrap, user: bootstrap.user || session.user });
      }
    } catch (error) {
      const offline = isOfflineError(error);
      if (!offline) await clearSecureSession();
      set({ ready: true, signedIn: false, user: null, offline, error: offline ? "You appear to be offline." : "" });
    }
  },
  async refreshSession() {
    const refreshToken = await getRefreshToken();
    const session = await refreshMobileSession(refreshToken);
    if (session.refresh_token) await setRefreshToken(session.refresh_token);
    await persistPreferredLanguage(session.user);
    set({ signedIn: session.authenticated, user: session.user, error: "", offline: false });
  },
  async login(identifier, password, remember) {
    try {
      const preferredLanguage = await getPreferredLanguage();
      const session = await loginWithPassword(identifier, password, preferredLanguage);
      if (!remember) await clearPersistedSessionCookie();
      if (session.refresh_token) await setRefreshToken(session.refresh_token);
      const bootstrap = session.authenticated ? await loadProfileBootstrap(session.user) : null;
      await persistPreferredLanguage(bootstrap?.user || session.user);
      set({ signedIn: session.authenticated, user: bootstrap?.user || session.user, bootstrap, error: "", offline: false, pendingConfirmationEmail: "" });
    } catch (error) {
      if (error instanceof PulseApiError && error.code === "email_not_confirmed") {
        set({ pendingConfirmationEmail: identifier.includes("@") ? identifier : "", error: error.message });
      }
      throw error;
    }
  },
  async signup(payload) {
    const preferredLanguage = await getPreferredLanguage();
    const session = await createAccount({ ...payload, preferred_language: preferredLanguage });
    if (session.refresh_token) await setRefreshToken(session.refresh_token);
    const bootstrap = session.authenticated ? await loadProfileBootstrap(session.user) : null;
    await persistPreferredLanguage(bootstrap?.user || session.user);
    set({
      signedIn: session.authenticated,
      user: bootstrap?.user || session.user,
      bootstrap,
      pendingConfirmationEmail: payload.email,
      error: "",
      offline: false
    });
  },
  async recover(email) {
    await requestPasswordRecovery(email);
  },
  async resendConfirmation(email) {
    const target = email || useAuthStore.getState().pendingConfirmationEmail;
    const result = await resendEmailConfirmation(target);
    set({ pendingConfirmationEmail: target, error: "" });
    return result.message;
  },
  async changeConfirmationEmail(oldEmail, newEmail, password) {
    const result = await changeEmailConfirmation(oldEmail, newEmail, password);
    const target = result.email || newEmail;
    set({ pendingConfirmationEmail: target, error: "" });
    return result.message;
  },
  async refreshConfirmationStatus(email) {
    const target = email || useAuthStore.getState().pendingConfirmationEmail;
    const result = await checkEmailConfirmationStatus(target);
    set({ pendingConfirmationEmail: target, error: "" });
    return result.confirmed || result.email_verified;
  },
  async confirmEmail(token) {
    const result = await confirmEmailToken(token);
    set({ error: "" });
    return result.message;
  },
  async resetPassword(token, password) {
    const result = await resetPasswordWithToken(token, password);
    await clearSecureSession();
    set({ signedIn: false, user: null, bootstrap: null, error: "" });
    return result.message;
  },
  async loadBootstrap() {
    const current = useAuthStore.getState().user;
    const bootstrap = await loadProfileBootstrap(current);
    await persistPreferredLanguage(bootstrap.user || current);
    set({ bootstrap, user: bootstrap.user || current, offline: false });
  },
  async logout() {
    await unregisterPushNotifications().catch(() => undefined);
    const refreshToken = await getRefreshToken();
    await logoutMobileSession(refreshToken).catch(() => undefined);
    await clearSecureSession();
    set({ signedIn: false, user: null, bootstrap: null, pendingConfirmationEmail: "" });
  }
}));
