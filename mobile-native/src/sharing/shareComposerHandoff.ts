import AsyncStorage from "@react-native-async-storage/async-storage";
import { PulseShareKind } from "./nativeShare";

export type ShareComposerMode = "status" | "reel";

export type ShareComposerHandoff = {
  id: string;
  mode: ShareComposerMode;
  body: string;
  url: string;
  kind: PulseShareKind;
  createdAt: string;
};

const STORAGE_KEY = "pulsesoc.native.share.composer-handoff.v1";
const MAX_HANDOFF_AGE_MS = 15 * 60 * 1000;
const MAX_BODY_LENGTH = 3000;

export async function saveShareComposerHandoff(input: {
  mode: ShareComposerMode;
  body: string;
  url: string;
  kind: PulseShareKind;
}) {
  const now = Date.now();
  const handoff: ShareComposerHandoff = {
    id: `share-composer-${now}-${Math.random().toString(36).slice(2, 10)}`,
    mode: input.mode,
    body: cleanBody(input.body),
    url: String(input.url || "").trim().slice(0, 1000),
    kind: input.kind,
    createdAt: new Date(now).toISOString()
  };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(handoff));
  return handoff;
}

export async function consumeShareComposerHandoff(expectedId?: string) {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ShareComposerHandoff>;
    if (expectedId && parsed.id !== expectedId) return null;
    await AsyncStorage.removeItem(STORAGE_KEY).catch(() => undefined);
    if (!isValidHandoff(parsed)) return null;
    return {
      ...parsed,
      body: cleanBody(parsed.body || ""),
      url: String(parsed.url || "").trim().slice(0, 1000)
    } as ShareComposerHandoff;
  } catch {
    await AsyncStorage.removeItem(STORAGE_KEY).catch(() => undefined);
    return null;
  }
}

export function mergeShareIntoComposerBody(current: string, incoming: string) {
  const existing = cleanBody(current);
  const shared = cleanBody(incoming);
  if (!shared) return existing;
  if (!existing) return shared;
  if (existing.includes(shared)) return existing;
  return `${existing}\n\n${shared}`.slice(0, MAX_BODY_LENGTH);
}

function cleanBody(value: string) {
  return String(value || "").replace(/\r\n/g, "\n").trim().slice(0, MAX_BODY_LENGTH);
}

function isValidHandoff(value: Partial<ShareComposerHandoff>) {
  if (!value.id || !value.body || !value.url || !value.createdAt) return false;
  if (value.mode !== "status" && value.mode !== "reel") return false;
  const createdAt = Date.parse(value.createdAt);
  if (!Number.isFinite(createdAt) || Date.now() - createdAt > MAX_HANDOFF_AGE_MS || createdAt - Date.now() > 60_000) return false;
  try {
    const url = new URL(value.url);
    return url.protocol === "https:" && (url.hostname === "pulsesoc.com" || url.hostname.endsWith(".pulsesoc.com"));
  } catch {
    return false;
  }
}
