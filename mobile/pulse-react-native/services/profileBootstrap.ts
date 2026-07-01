import { pulseApi } from "./apiClient";
import { PulseUser } from "./auth";

export type ProfileBootstrap = {
  user: PulseUser | null;
  avatarUrl: string;
  premiumStatus: string;
  founderStatus: string;
  notificationCount: number;
  preferredLanguage: string;
};

type AccountStatus = {
  ok: boolean;
  plan?: string;
  subscription_status?: string;
  premium_status?: string;
  has_pro_access?: boolean;
  pro_access_type?: string;
  founder_status?: string;
};

type NotificationCount = {
  ok?: boolean;
  unread_count?: number;
  count?: number;
};

export async function loadProfileBootstrap(user: PulseUser | null): Promise<ProfileBootstrap> {
  const [profileResult, accountResult, countResult] = await Promise.allSettled([
    pulseApi<Record<string, unknown>>("/api/pulse/profile/me"),
    pulseApi<AccountStatus>("/api/account/status"),
    pulseApi<NotificationCount>("/api/pulse/notifications/unread-count")
  ]);

  const profile = profileResult.status === "fulfilled" ? profileResult.value : {};
  const account: Partial<AccountStatus> = accountResult.status === "fulfilled" ? accountResult.value : {};
  const count = countResult.status === "fulfilled" ? countResult.value : {};
  const profileUser = readProfileUser(profile) || user;

  return {
    user: profileUser,
    avatarUrl: String(profileUser?.avatar_thumbnail_url || profileUser?.avatar_url || profile.avatar_url || ""),
    premiumStatus: String(account.premium_status || account.subscription_status || profileUser?.premium_status || "free"),
    founderStatus: String(account.founder_status || account.pro_access_type || ""),
    notificationCount: Number(count.unread_count || count.count || 0),
    preferredLanguage: String(profileUser?.preferred_language || "en")
  };
}

function readProfileUser(profile: Record<string, unknown>) {
  const candidate = profile.user || profile.profile;
  return typeof candidate === "object" && candidate !== null ? (candidate as PulseUser) : null;
}
