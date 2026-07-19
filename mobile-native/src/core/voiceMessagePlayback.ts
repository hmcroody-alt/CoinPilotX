import { Audio, AVPlaybackStatus, InterruptionModeAndroid, InterruptionModeIOS } from "expo-av";
import { AppState } from "react-native";
import { pausePulseRadio } from "./pulseRadio";

export type VoicePlaybackStatus = "idle" | "loading" | "playing" | "paused" | "error";

export type VoicePlaybackSnapshot = {
  messageId: string;
  status: VoicePlaybackStatus;
  positionMillis: number;
  durationMillis: number;
  rate: number;
  error: string;
};

type PlayRequest = {
  messageId: string;
  url: string;
  durationMillis?: number;
};

const listeners = new Map<string, Set<(snapshot: VoicePlaybackSnapshot) => void>>();
const snapshots = new Map<string, VoicePlaybackSnapshot>();
let activeMessageId = "";
let sound: Audio.Sound | null = null;
let generation = 0;

AppState.addEventListener("change", (next) => {
  if (next !== "active" && activeMessageId) stopVoiceMessagePlayback("app_backgrounded").catch(() => undefined);
});

export function getVoicePlaybackSnapshot(messageId: string, durationMillis = 0): VoicePlaybackSnapshot {
  return snapshots.get(messageId) || idleSnapshot(messageId, durationMillis);
}

export function subscribeVoicePlayback(messageId: string, durationMillis: number, listener: (snapshot: VoicePlaybackSnapshot) => void) {
  const group = listeners.get(messageId) || new Set<(snapshot: VoicePlaybackSnapshot) => void>();
  group.add(listener);
  listeners.set(messageId, group);
  listener(getVoicePlaybackSnapshot(messageId, durationMillis));
  return () => {
    group.delete(listener);
    if (!group.size) listeners.delete(messageId);
  };
}

export async function toggleVoicePlayback(request: PlayRequest) {
  const current = getVoicePlaybackSnapshot(request.messageId, request.durationMillis);
  if (activeMessageId === request.messageId && sound) {
    const playback = await sound.getStatusAsync();
    if (playback.isLoaded && playback.isPlaying) {
      await sound.pauseAsync();
      return;
    }
    if (playback.isLoaded && playback.durationMillis && playback.positionMillis >= playback.durationMillis - 50) await sound.replayAsync();
    else await sound.playAsync();
    return;
  }
  await startVoicePlayback(request, current.rate);
}

export async function retryVoicePlayback(request: PlayRequest) {
  await startVoicePlayback(request, getVoicePlaybackSnapshot(request.messageId, request.durationMillis).rate);
}

export async function seekVoicePlayback(messageId: string, fraction: number) {
  if (activeMessageId !== messageId || !sound) return;
  const current = getVoicePlaybackSnapshot(messageId);
  const target = Math.max(0, Math.min(1, fraction)) * Math.max(0, current.durationMillis);
  await sound.setPositionAsync(target);
}

export async function seekVoicePlaybackBy(messageId: string, deltaMillis: number) {
  const current = getVoicePlaybackSnapshot(messageId);
  await seekVoicePlayback(messageId, (current.positionMillis + deltaMillis) / Math.max(1, current.durationMillis));
}

export async function cycleVoicePlaybackRate(messageId: string) {
  const current = getVoicePlaybackSnapshot(messageId);
  const rate = current.rate === 1 ? 1.5 : current.rate === 1.5 ? 2 : 1;
  update(messageId, { rate });
  if (activeMessageId === messageId && sound) await sound.setRateAsync(rate, true);
  return rate;
}

export async function releaseVoicePlayback(messageId: string) {
  if (activeMessageId === messageId) await stopVoiceMessagePlayback("row_unmounted");
  snapshots.delete(messageId);
}

export async function stopVoiceMessagePlayback(_reason = "stopped") {
  generation += 1;
  const previousId = activeMessageId;
  const previousSound = sound;
  activeMessageId = "";
  sound = null;
  if (previousSound) await previousSound.unloadAsync().catch(() => undefined);
  if (previousId) {
    const previous = getVoicePlaybackSnapshot(previousId);
    update(previousId, { status: "idle", positionMillis: 0, error: "", durationMillis: previous.durationMillis });
  }
}

async function startVoicePlayback(request: PlayRequest, rate: number) {
  const attempt = ++generation;
  if (sound) await stopCurrentSound();
  activeMessageId = request.messageId;
  update(request.messageId, {
    status: "loading",
    positionMillis: 0,
    durationMillis: Math.max(0, request.durationMillis || 0),
    error: "",
    rate
  });
  try {
    await pausePulseRadio().catch(() => undefined);
    await Audio.setAudioModeAsync({
      allowsRecordingIOS: false,
      staysActiveInBackground: false,
      playsInSilentModeIOS: true,
      interruptionModeIOS: InterruptionModeIOS.DoNotMix,
      interruptionModeAndroid: InterruptionModeAndroid.DoNotMix,
      shouldDuckAndroid: true,
      playThroughEarpieceAndroid: false
    });
    if (attempt !== generation) return;
    const created = await Audio.Sound.createAsync(
      { uri: request.url },
      { shouldPlay: true, rate, shouldCorrectPitch: true, progressUpdateIntervalMillis: 250 },
      (playback) => handleStatus(request.messageId, playback, attempt)
    );
    if (attempt !== generation) {
      await created.sound.unloadAsync().catch(() => undefined);
      return;
    }
    sound = created.sound;
  } catch {
    if (attempt !== generation) return;
    sound = null;
    update(request.messageId, { status: "error", error: "Voice message unavailable. Tap to retry." });
  }
}

async function stopCurrentSound() {
  const previousId = activeMessageId;
  const previousSound = sound;
  sound = null;
  activeMessageId = "";
  if (previousSound) await previousSound.unloadAsync().catch(() => undefined);
  if (previousId) update(previousId, { status: "idle", positionMillis: 0, error: "" });
}

function handleStatus(messageId: string, playback: AVPlaybackStatus, attempt: number) {
  if (attempt !== generation || activeMessageId !== messageId) return;
  if (!playback.isLoaded) {
    if (playback.error) update(messageId, { status: "error", error: "Voice message unavailable. Tap to retry." });
    return;
  }
  const durationMillis = Math.max(0, playback.durationMillis || getVoicePlaybackSnapshot(messageId).durationMillis);
  if (playback.didJustFinish) {
    update(messageId, { status: "paused", positionMillis: 0, durationMillis, error: "" });
    return;
  }
  update(messageId, {
    status: playback.isBuffering && !playback.isPlaying ? "loading" : playback.isPlaying ? "playing" : "paused",
    positionMillis: Math.max(0, playback.positionMillis || 0),
    durationMillis,
    error: ""
  });
}

function idleSnapshot(messageId: string, durationMillis = 0): VoicePlaybackSnapshot {
  return { messageId, status: "idle", positionMillis: 0, durationMillis: Math.max(0, durationMillis), rate: 1, error: "" };
}

function update(messageId: string, patch: Partial<VoicePlaybackSnapshot>) {
  const next = { ...getVoicePlaybackSnapshot(messageId), ...patch, messageId };
  snapshots.set(messageId, next);
  listeners.get(messageId)?.forEach((listener) => listener(next));
}
