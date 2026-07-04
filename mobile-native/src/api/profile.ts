import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { listFeed, PulsePost } from "./feed";
import { pulseApi } from "./pulseApi";

const PROFILE_CACHE_PREFIX = "pulsesoc.native.profile.";
const PROFILE_THEME_CACHE_KEY = "pulsesoc.native.profile.theme";

export type PulseProfileTheme = {
  theme_key?: string;
  accent_color?: string;
  background_style?: string;
  active?: boolean;
};

export type PulseProfile = {
  user_id: number;
  email?: string;
  username?: string;
  display_name: string;
  full_name?: string;
  public_player_id?: string;
  avatar_url?: string;
  avatar_thumbnail_url?: string;
  cover_url?: string;
  banner_url?: string;
  bio?: string;
  social_links?: string;
  social_links_json?: string;
  expertise_tags?: string;
  expertise_tags_json?: string;
  profile_visibility?: "public" | "private";
  account_status?: string;
  premium_status?: string;
  verification_status?: string;
  verified_badge?: boolean | number;
  follower_count?: number;
  following_count?: number;
  post_count?: number;
  media_count?: number;
  badges?: string[];
  theme?: PulseProfileTheme;
};

export type ProfileUpdatePayload = {
  display_name: string;
  username?: string;
  bio?: string;
  social_links?: string;
  expertise_tags?: string;
  profile_visibility?: "public" | "private";
};

export type ProfileMediaUploadInput = {
  uri: string;
  name: string;
  mimeType: string;
};

export async function getMyProfile() {
  const data = await pulseApi<{ ok?: boolean; user?: PulseProfile; items?: PulseProfile[] }>("/api/pulse/profile/me");
  const profile = normalizeProfile(data.user || data.items?.[0] || {});
  const theme = await getProfileTheme().catch(() => null);
  const next = { ...profile, theme: theme || profile.theme };
  await cacheProfile("me", next);
  return next;
}

export async function updateProfile(payload: ProfileUpdatePayload) {
  const data = await pulseApi<{ ok?: boolean; message?: string } & Partial<PulseProfile>>("/api/pulse/profile/update", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, ...payload, ...data });
  await cacheProfile("me", profile);
  return profile;
}

export async function uploadProfileAvatar(input: ProfileMediaUploadInput) {
  const form = new FormData();
  form.append("avatar", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  const data = await pulseApi<{ ok?: boolean; avatar_url?: string; avatar_url_cache_busted?: string; avatar_thumbnail_url?: string }>(
    "/api/pulse/profile/avatar",
    { method: "POST", body: form }
  );
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({
    ...current,
    avatar_url: data.avatar_url_cache_busted || data.avatar_url || current.avatar_url,
    avatar_thumbnail_url: data.avatar_thumbnail_url || data.avatar_url_cache_busted || data.avatar_url || current.avatar_thumbnail_url
  });
  await cacheProfile("me", profile);
  return profile;
}

export async function uploadProfileCover(input: ProfileMediaUploadInput) {
  const form = new FormData();
  form.append("cover", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  const data = await pulseApi<{ ok?: boolean; cover_url?: string; cover_url_cache_busted?: string; banner_url?: string }>(
    "/api/pulse/profile/cover",
    { method: "POST", body: form }
  );
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({
    ...current,
    cover_url: data.cover_url_cache_busted || data.cover_url || data.banner_url || current.cover_url,
    banner_url: data.banner_url || data.cover_url_cache_busted || data.cover_url || current.banner_url
  });
  await cacheProfile("me", profile);
  return profile;
}

export async function removeProfileAvatar() {
  await pulseApi<{ ok?: boolean; avatar_url?: string }>("/api/pulse/profile/avatar/remove", {
    method: "POST",
    body: JSON.stringify({})
  });
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, avatar_url: "", avatar_thumbnail_url: "" });
  await cacheProfile("me", profile);
  return profile;
}

export async function removeProfileCover() {
  await pulseApi<{ ok?: boolean; cover_url?: string }>("/api/pulse/profile/cover/remove", {
    method: "POST",
    body: JSON.stringify({})
  });
  const current = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, cover_url: "", banner_url: "" });
  await cacheProfile("me", profile);
  return profile;
}

export async function getProfileTheme() {
  const data = await pulseApi<{ ok?: boolean; theme?: PulseProfileTheme }>("/api/pulse/premium/profile-theme");
  const theme = data.theme || {};
  await AsyncStorage.setItem(PROFILE_THEME_CACHE_KEY, JSON.stringify(theme));
  return theme;
}

export async function updateProfileTheme(theme: PulseProfileTheme) {
  const data = await pulseApi<{ ok?: boolean; theme_key?: string; message?: string }>("/api/pulse/premium/profile-theme", {
    method: "POST",
    body: JSON.stringify({
      theme_key: theme.theme_key || "midnight_elite",
      accent_color: theme.accent_color || "#ffd166"
    })
  });
  const next = { ...theme, theme_key: data.theme_key || theme.theme_key };
  await AsyncStorage.setItem(PROFILE_THEME_CACHE_KEY, JSON.stringify(next));
  return next;
}

export async function listPublicProfilePosts(profileKey: string) {
  const data = await listFeed({ feed: "for_you", profile: profileKey, limit: 20, offset: 0 });
  return data.posts || [];
}

export async function loadCachedProfile(cacheKey = "me") {
  try {
    const cached = await AsyncStorage.getItem(`${PROFILE_CACHE_PREFIX}${cacheKey}`);
    if (!cached) return null;
    return normalizeProfile(JSON.parse(cached) as PulseProfile);
  } catch {
    await AsyncStorage.removeItem(`${PROFILE_CACHE_PREFIX}${cacheKey}`).catch(() => undefined);
    return null;
  }
}

export async function cacheProfile(cacheKey: string, profile: PulseProfile) {
  await AsyncStorage.setItem(`${PROFILE_CACHE_PREFIX}${cacheKey}`, JSON.stringify(profile));
}

export function normalizeProfile(input: Partial<PulseProfile>): PulseProfile {
  const profile = input || {};
  const display = profile.display_name || profile.full_name || profile.username || profile.public_player_id || "PulseSoc member";
  return {
    ...profile,
    user_id: Number(profile.user_id || 0),
    display_name: display,
    username: String(profile.username || "").replace(/^@/, ""),
    avatar_url: absoluteProfileUrl(profile.avatar_url || profile.avatar_thumbnail_url || ""),
    avatar_thumbnail_url: absoluteProfileUrl(profile.avatar_thumbnail_url || profile.avatar_url || ""),
    cover_url: absoluteProfileUrl(profile.cover_url || profile.banner_url || ""),
    banner_url: absoluteProfileUrl(profile.banner_url || profile.cover_url || ""),
    bio: profile.bio || "",
    social_links: profile.social_links || profile.social_links_json || "",
    expertise_tags: profile.expertise_tags || profile.expertise_tags_json || "",
    profile_visibility: profile.profile_visibility === "private" ? "private" : "public",
    follower_count: Number(profile.follower_count || 0),
    following_count: Number(profile.following_count || 0),
    post_count: Number(profile.post_count || 0),
    media_count: Number(profile.media_count || 0),
    badges: profile.badges || [],
    theme: profile.theme || {}
  };
}

export function profileKeyFromPost(post: PulsePost) {
  return post.author?.public_player_id || post.author?.username || post.author_username || "";
}

export function profileWebUrl(profileKey?: string) {
  return profileKey ? `${PULSE_API_BASE_URL}/pulse/profile/${encodeURIComponent(profileKey)}` : `${PULSE_API_BASE_URL}/pulse/profile`;
}

function absoluteProfileUrl(url: string) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("/")) return `${PULSE_API_BASE_URL}${url}`;
  return `${PULSE_API_BASE_URL}/${url}`;
}
