import { AppState } from "react-native";

export type MediaPlaybackKind = "call" | "recording" | "live" | "voice" | "radio" | "reel" | "status" | "viewer" | "music_preview" | "feed";

type PlaybackOwner = {
  id: string;
  kind: MediaPlaybackKind;
  pause: () => void | Promise<void>;
  stop?: () => void | Promise<void>;
};

let activeOwner: PlaybackOwner | null = null;
const listeners = new Set<(owner: PlaybackOwner | null) => void>();
const PRIORITY: Record<MediaPlaybackKind, number> = { call: 100, recording: 90, live: 70, voice: 60, viewer: 50, status: 40, reel: 40, feed: 35, music_preview: 30, radio: 20 };
const BACKGROUND_RETAINED_KINDS = new Set<MediaPlaybackKind>(["call", "recording", "live", "radio"]);

AppState.addEventListener("change", (next) => {
  if (next === "active") return;
  if (activeOwner && shouldRetainMediaPlaybackOnBackground(activeOwner.kind)) return;
  releaseMediaPlayback(undefined, "backgrounded").catch(() => undefined);
});

export function shouldRetainMediaPlaybackOnBackground(kind: MediaPlaybackKind) {
  return BACKGROUND_RETAINED_KINDS.has(kind);
}

export function getActiveMediaPlayback() {
  return activeOwner ? { id: activeOwner.id, kind: activeOwner.kind } : null;
}

export function subscribeMediaPlayback(listener: (owner: { id: string; kind: MediaPlaybackKind } | null) => void) {
  const wrapped = (owner: PlaybackOwner | null) => listener(owner ? { id: owner.id, kind: owner.kind } : null);
  listeners.add(wrapped);
  wrapped(activeOwner);
  return () => listeners.delete(wrapped);
}

export async function claimMediaPlayback(owner: PlaybackOwner) {
  if (activeOwner?.id === owner.id) {
    activeOwner = owner;
    return true;
  }
  if (activeOwner && PRIORITY[activeOwner.kind] > PRIORITY[owner.kind]) return false;
  const previous = activeOwner;
  activeOwner = owner;
  if (previous) await Promise.resolve(previous.pause()).catch(() => undefined);
  notify();
  return true;
}

export async function releaseMediaPlayback(ownerId?: string, reason = "released") {
  if (!activeOwner || (ownerId && activeOwner.id !== ownerId)) return;
  const previous = activeOwner;
  activeOwner = null;
  if (reason === "backgrounded" && previous.stop) await Promise.resolve(previous.stop()).catch(() => undefined);
  else await Promise.resolve(previous.pause()).catch(() => undefined);
  notify();
}

export async function resetMediaPlayback() {
  await releaseMediaPlayback(undefined, "backgrounded");
}

function notify() {
  listeners.forEach((listener) => listener(activeOwner));
}
