import AsyncStorage from "@react-native-async-storage/async-storage";
import { pulseApi } from "./pulseApi";

const SEARCH_CACHE_PREFIX = "pulsesoc.native.search.results.";
const RECENT_SEARCHES_KEY = "pulsesoc.native.search.recent";

export type PulseSearchGroupKey =
  | "posts"
  | "creators"
  | "presences"
  | "videos"
  | "reels"
  | "statuses"
  | "marketplace"
  | "music"
  | "groups"
  | "rooms"
  | "comments";

export type PulseSearchResult = {
  id?: number | string;
  /** Presence rows carry their entity type so an artist or business is never
   * read as a personal account. */
  presence_type?: string;
  handle?: string;
  verified?: boolean;
  user_id?: number | string;
  profile_id?: number | string;
  public_player_id?: string;
  public_pulse_id?: string;
  username?: string;
  title?: string;
  description?: string;
  type?: string;
  url?: string;
  meta?: string;
  avatar_url?: string;
};

export type PulseSearchResults = Record<PulseSearchGroupKey, PulseSearchResult[]>;

export type PulseSearchResponse = {
  ok?: boolean;
  query?: string;
  results?: PulseSearchResults;
  total?: number;
  trending?: string[];
  recent?: string[];
  limit?: number;
};

export const SEARCH_GROUPS: Array<{ key: PulseSearchGroupKey; label: string }> = [
  { key: "posts", label: "Posts" },
  { key: "creators", label: "People" },
  { key: "presences", label: "Artists & Businesses" },
  { key: "videos", label: "Videos" },
  { key: "reels", label: "Reels" },
  { key: "statuses", label: "Status" },
  { key: "marketplace", label: "Marketplace" },
  { key: "music", label: "Music" },
  { key: "groups", label: "Communities" },
  { key: "rooms", label: "Rooms" },
  { key: "comments", label: "Comments" }
];

export async function searchPulse(params: { query: string; limit?: number }) {
  const clean = normalizeQuery(params.query);
  const query = new URLSearchParams({ q: clean, limit: String(params.limit || 20) });
  const data = await pulseApi<PulseSearchResponse>(`/api/pulse/search?${query.toString()}`);
  const normalized = normalizeSearchResponse(data);
  if (clean) {
    await Promise.all([cachePulseSearch(clean, normalized), saveRecentSearch(clean)]).catch(() => undefined);
  }
  return normalized;
}

export async function loadCachedPulseSearch(query: string) {
  const clean = normalizeQuery(query);
  if (!clean) return null;
  try {
    const cached = await AsyncStorage.getItem(`${SEARCH_CACHE_PREFIX}${clean.toLowerCase()}`);
    if (!cached) return null;
    return normalizeSearchResponse(JSON.parse(cached) as PulseSearchResponse);
  } catch {
    await AsyncStorage.removeItem(`${SEARCH_CACHE_PREFIX}${clean.toLowerCase()}`).catch(() => undefined);
    return null;
  }
}

export async function cachePulseSearch(query: string, data: PulseSearchResponse) {
  const clean = normalizeQuery(query);
  if (!clean) return;
  await AsyncStorage.setItem(`${SEARCH_CACHE_PREFIX}${clean.toLowerCase()}`, JSON.stringify(data));
}

export async function loadRecentSearches() {
  try {
    const cached = await AsyncStorage.getItem(RECENT_SEARCHES_KEY);
    if (!cached) return [];
    return normalizeRecentSearches(JSON.parse(cached) as string[]);
  } catch {
    await AsyncStorage.removeItem(RECENT_SEARCHES_KEY).catch(() => undefined);
    return [];
  }
}

export async function saveRecentSearch(query: string) {
  const clean = normalizeQuery(query);
  if (!clean) return [];
  const current = await loadRecentSearches();
  const next = normalizeRecentSearches([clean, ...current.filter((item) => item.toLowerCase() !== clean.toLowerCase())]);
  await AsyncStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next));
  return next;
}

export function normalizeSearchResponse(data: PulseSearchResponse): PulseSearchResponse {
  const results = emptyResults();
  SEARCH_GROUPS.forEach(({ key }) => {
    results[key] = normalizeResultList(data.results?.[key] || []);
  });
  return {
    ...data,
    query: normalizeQuery(data.query || ""),
    results,
    total: Number(data.total ?? SEARCH_GROUPS.reduce((sum, group) => sum + results[group.key].length, 0)),
    trending: normalizeRecentSearches(data.trending || defaultTrendingSearches()),
    recent: normalizeRecentSearches(data.recent || []),
    limit: Number(data.limit || 20)
  };
}

export function defaultTrendingSearches() {
  return ["scam alerts", "AI builders", "creator economy", "wallet safety", "reels", "live rooms", "marketplace", "music"];
}

function normalizeResultList(items: PulseSearchResult[]) {
  return (items || []).map((item) => ({
    ...item,
    user_id: item.user_id || (isCreatorResult(item) ? item.id : undefined),
    public_player_id: String(item.public_player_id || item.public_pulse_id || "").replace(/^@/, ""),
    public_pulse_id: item.public_pulse_id || (item.public_player_id ? `@${String(item.public_player_id).replace(/^@/, "")}` : undefined),
    username: item.username ? String(item.username).replace(/^@/, "") : undefined,
    title: String(item.title || "PulseSoc result"),
    description: String(item.description || item.meta || ""),
    type: String(item.type || "PulseSoc"),
    url: String(item.url || "/pulse")
  }));
}

function isCreatorResult(item: PulseSearchResult) {
  const type = String(item.type || "").toLowerCase();
  return type === "creator" || type === "person" || type === "people" || type === "user";
}

function normalizeRecentSearches(items: string[]) {
  return (items || [])
    .map((item) => normalizeQuery(item))
    .filter(Boolean)
    .filter((item, index, list) => list.findIndex((candidate) => candidate.toLowerCase() === item.toLowerCase()) === index)
    .slice(0, 8);
}

function normalizeQuery(query: string) {
  return String(query || "").replace(/\s+/g, " ").trim().slice(0, 120);
}

function emptyResults(): PulseSearchResults {
  return {
    posts: [],
    creators: [],
    presences: [],
    videos: [],
    reels: [],
    statuses: [],
    marketplace: [],
    music: [],
    groups: [],
    rooms: [],
    comments: []
  };
}
