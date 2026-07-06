import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const CREATOR_STATE_CACHE_KEY = "pulsesoc.native.creator.state";

export type CreatorMetricMap = Record<string, number | string | boolean | null | undefined>;

export type CreatorCard = {
  key: string;
  subsystem_key?: string;
  label: string;
  route?: string;
  state?: string;
  count?: number | string;
  detail?: string;
  action?: string;
};

export type CreatorRecentItem = {
  id?: number;
  title?: string;
  body?: string;
  caption?: string;
  status?: string;
  created_at?: string;
  engagement_score?: number;
};

export type CreatorState = {
  user_id?: number;
  metrics?: CreatorMetricMap;
  intelligence?: CreatorMetricMap & {
    recommended_next_actions?: string[];
    community_guideline_status?: string;
    monetization_status?: string;
    best_time_to_post?: string;
  };
  cards?: CreatorCard[];
  event_bus?: Array<{ event?: string; effect?: string }>;
  posts?: { total?: number; in_review?: number; recent?: CreatorRecentItem[] };
  reels?: { total?: number; in_review?: number; processing?: number; completion_rate?: number; recent?: CreatorRecentItem[] };
  videos?: { total?: number; in_review?: number; processing?: number; views?: number; recent?: CreatorRecentItem[] };
  statuses?: { total?: number; active?: number; in_review?: number; views?: number; completion_rate?: number; recent?: CreatorRecentItem[] };
  live?: { total?: number; active?: number; reports_open?: number; ready?: boolean };
  privacy?: Record<string, boolean | string | number>;
};

export type CreatorStateResponse = {
  ok?: boolean;
  creator?: CreatorState;
  message?: string;
};

export type CreatorAiTool = "hook" | "caption" | "virality" | "live-title";

export type CreatorAiResponse = {
  ok?: boolean;
  tool?: CreatorAiTool;
  output?: string;
  score?: number;
  retention_tip?: string;
  risk_note?: string;
  safety?: string;
  message?: string;
};

export type ContentPlannerItemPayload = {
  title?: string;
  caption: string;
  content_type?: string;
  hashtags?: string;
  audience?: string;
  scheduled_at?: string;
  alt_text?: string;
  status?: string;
  stage?: string;
  media_attached?: boolean;
  thumbnail_selected?: boolean;
  links_validated?: boolean;
  final_preview_reviewed?: boolean;
};

export async function getCreatorState() {
  const data = await pulseApi<CreatorStateResponse>("/api/dashboard/creator/state");
  const state = normalizeCreatorState(data.creator || {});
  await cacheCreatorState(state).catch(() => undefined);
  return state;
}

export async function loadCachedCreatorState() {
  return readJsonCache<CreatorState>(CREATOR_STATE_CACHE_KEY, normalizeCreatorState);
}

export async function cacheCreatorState(state: CreatorState) {
  await writeJsonCache(CREATOR_STATE_CACHE_KEY, normalizeCreatorState(state));
}

export async function runCreatorAiTool(tool: CreatorAiTool, text: string, topic = "PulseSoc creator studio") {
  return pulseApi<CreatorAiResponse>(`/api/pulse/creator-ai/${tool}`, {
    method: "POST",
    body: JSON.stringify({ text, topic, source: "mobile_native_creator_studio" })
  });
}

export async function saveContentPlannerItem(payload: ContentPlannerItemPayload) {
  return pulseApi<{ ok?: boolean; message?: string; item?: Record<string, unknown> }>("/api/dashboard/content-planner/item", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      status: payload.status || "draft",
      stage: payload.stage || "planning",
      source: "mobile_native_creator_studio"
    })
  });
}

export function plannerWebRoute(mode: "planner" | "scheduler" | "drafts" | "ai" = "planner") {
  if (mode === "scheduler") return "/dashboard/creator/post-scheduler";
  if (mode === "drafts") return "/dashboard/creator/draft-studio";
  if (mode === "ai") return "/dashboard/creator/ai-creator-assistant";
  return "/dashboard/creator/content-planner";
}

export async function openCreatorWebFallback(path = "/pulse/creator-studio") {
  const target = /^https?:\/\//i.test(path) ? path : `${PULSE_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  await Linking.openURL(target).catch(() => undefined);
}

export function normalizeCreatorState(input: CreatorState): CreatorState {
  const metrics = input.metrics || {};
  return {
    ...input,
    user_id: Number(input.user_id || 0),
    metrics,
    intelligence: input.intelligence || {},
    cards: normalizeCreatorCards(input.cards || []),
    event_bus: input.event_bus || [],
    posts: normalizeContentSection(input.posts),
    reels: normalizeContentSection(input.reels),
    videos: normalizeContentSection(input.videos),
    statuses: normalizeContentSection(input.statuses),
    live: {
      total: Number(input.live?.total || 0),
      active: Number(input.live?.active || 0),
      reports_open: Number(input.live?.reports_open || 0),
      ready: Boolean(input.live?.ready)
    },
    privacy: input.privacy || {}
  };
}

export function creatorScore(state: CreatorState | null) {
  return Number(state?.metrics?.creator_score || state?.intelligence?.creator_score || 0);
}

export function creatorRecommendations(state: CreatorState | null) {
  const items = state?.intelligence?.recommended_next_actions;
  return Array.isArray(items) ? items.map(String).filter(Boolean) : [];
}

export function creatorWebRoute(route?: string) {
  return route && route.startsWith("/") ? route : "/pulse/creator-studio";
}

function normalizeCreatorCards(cards: CreatorCard[]) {
  return cards.map((card, index) => ({
    ...card,
    key: String(card.key || card.subsystem_key || `creator-card-${index}`),
    label: String(card.label || card.subsystem_key || "Creator tool"),
    state: String(card.state || "BETA"),
    detail: String(card.detail || ""),
    action: String(card.action || "Open")
  }));
}

function normalizeContentSection<T extends { recent?: CreatorRecentItem[]; [key: string]: unknown }>(section?: T) {
  const source = section || ({} as T);
  return {
    ...source,
    total: Number(source.total || 0),
    in_review: Number(source.in_review || 0),
    processing: Number(source.processing || 0),
    views: Number(source.views || 0),
    active: Number(source.active || 0),
    recent: Array.isArray(source.recent) ? source.recent : []
  };
}
