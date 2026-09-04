/**
 * Recently-used emoji (Stage 5): local per device, bounded, deduped,
 * most-recent-first. AsyncStorage-backed; no server round-trip.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { SkinTonePreference } from "./types";

const RECENTS_KEY = "pulsesoc.emoji.recents.v1";
const TONE_KEY = "pulsesoc.emoji.skin_tone.v1";
const MAX_RECENTS = 40;

let cache: string[] | null = null;
let toneCache: SkinTonePreference | null = null;

/** Load recents, most recent first. Resolves to [] on first run or error. */
export async function getRecentEmoji(): Promise<string[]> {
  if (cache) return cache;
  try {
    const raw = await AsyncStorage.getItem(RECENTS_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    cache = Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, MAX_RECENTS) : [];
  } catch {
    cache = [];
  }
  return cache;
}

/** Record a use: dedupe, move to front, cap. Fire-and-forget persistence. */
export async function recordRecentEmoji(emoji: string): Promise<string[]> {
  const current = await getRecentEmoji();
  const next = [emoji, ...current.filter((e) => e !== emoji)].slice(0, MAX_RECENTS);
  cache = next;
  try {
    await AsyncStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    // Persistence is best-effort; in-memory state already updated.
  }
  return next;
}

/** Persisted skin-tone preference (Stage 6). 0 = default yellow. */
export async function getSkinTonePreference(): Promise<SkinTonePreference> {
  if (toneCache !== null) return toneCache;
  try {
    const raw = await AsyncStorage.getItem(TONE_KEY);
    const n = raw === null ? 0 : Number(raw);
    toneCache = (n >= 0 && n <= 5 ? n : 0) as SkinTonePreference;
  } catch {
    toneCache = 0;
  }
  return toneCache;
}

export async function setSkinTonePreference(tone: SkinTonePreference): Promise<void> {
  toneCache = tone;
  try {
    await AsyncStorage.setItem(TONE_KEY, String(tone));
  } catch {
    // best-effort
  }
}

/** Test hook: reset module caches. */
export function __resetEmojiStoreForTests(): void {
  cache = null;
  toneCache = null;
}
