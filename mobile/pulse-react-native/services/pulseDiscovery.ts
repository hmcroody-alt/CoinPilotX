import { pulseApi } from "./apiClient";

export type PulseStatusRailItem = {
  id?: number | string;
  status_id?: number | string;
  title?: string;
  body?: string;
  text?: string;
  caption?: string;
  author_name?: string;
  display_name?: string;
  username?: string;
  created_at?: string;
};

export type PulseLiveItem = {
  id?: number | string;
  live_id?: number | string;
  title?: string;
  topic?: string;
  creator_name?: string;
  host_name?: string;
  display_name?: string;
  viewers_count?: number;
  viewer_count?: number;
  started_at?: string;
  status?: string;
};

type StatusRailResponse = {
  ok?: boolean;
  statuses?: PulseStatusRailItem[];
  items?: PulseStatusRailItem[];
  rail?: PulseStatusRailItem[];
};

type LiveNowResponse = {
  ok?: boolean;
  lives?: PulseLiveItem[];
  live?: PulseLiveItem[];
  sessions?: PulseLiveItem[];
  items?: PulseLiveItem[];
};

export async function loadStatusRail() {
  const data = await pulseApi<StatusRailResponse>("/api/pulse/status/rail?lane=for_you&limit=6");
  return data.statuses || data.items || data.rail || [];
}

export async function loadLiveNow() {
  const data = await pulseApi<LiveNowResponse>("/api/pulse/live-now?limit=4");
  return data.lives || data.live || data.sessions || data.items || [];
}

export function readStatusTitle(item: PulseStatusRailItem) {
  return String(item.title || item.body || item.text || item.caption || "PulseSoc status").trim();
}

export function readStatusOwner(item: PulseStatusRailItem) {
  return String(item.author_name || item.display_name || item.username || "PulseSoc member").trim();
}

export function readLiveTitle(item: PulseLiveItem) {
  return String(item.title || item.topic || "Realtime Pulse discovery").trim();
}

export function readLiveHost(item: PulseLiveItem) {
  return String(item.creator_name || item.host_name || item.display_name || "PulseSoc creator").trim();
}
