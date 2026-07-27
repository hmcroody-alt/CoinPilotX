import { Audio, InterruptionModeIOS, InterruptionModeAndroid } from "expo-av";
import * as Haptics from "expo-haptics";
import { Platform } from "react-native";

// Single owner of call ring audio + haptics so the incoming-call layer and the
// in-call screen can never stack overlapping ringtones/ringback tones.

type LoopTone = "ringback" | "ringtone";
type CueTone = "connect" | "disconnect";

const SOURCES = {
  ringback: require("../assets/sounds/ringback.wav"),
  ringtone: require("../assets/sounds/ringtone.wav"),
  connect: require("../assets/sounds/connect.wav"),
  disconnect: require("../assets/sounds/disconnect.wav")
} as const;

let loopSound: Audio.Sound | null = null;
let loopKey: LoopTone | null = null;
let vibrationTimer: ReturnType<typeof setInterval> | null = null;
let modeReady = false;
// Monotonic token so overlapping start/stop calls can't leak a second looping
// Sound. `Audio.Sound.createAsync` is async: if a newer startCallTone or any
// stopCallTone runs while an older start is still loading, the older load must
// be discarded instead of becoming a second, orphaned ringtone that never stops.
let toneGeneration = 0;

async function ensureAudioMode() {
  if (modeReady) return;
  modeReady = true;
  await Audio.setAudioModeAsync({
    playsInSilentModeIOS: true,
    allowsRecordingIOS: false,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.DoNotMix,
    interruptionModeAndroid: InterruptionModeAndroid.DoNotMix,
    shouldDuckAndroid: true,
    playThroughEarpieceAndroid: false
  }).catch(() => {
    modeReady = false;
  });
}

function startIncomingVibration() {
  if (Platform.OS === "web") return;
  stopIncomingVibration();
  const buzz = () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => undefined);
  buzz();
  vibrationTimer = setInterval(buzz, 2400);
}

function stopIncomingVibration() {
  if (vibrationTimer) {
    clearInterval(vibrationTimer);
    vibrationTimer = null;
  }
}

export async function startCallTone(tone: LoopTone) {
  if (loopKey === tone && loopSound) return;
  // Supersede any prior/in-flight tone FIRST (stopCallTone bumps the counter),
  // then claim our own generation. Capturing it before stopCallTone would make
  // this load look stale against its own stop and always discard itself.
  await stopCallTone();
  const generation = ++toneGeneration;
  await ensureAudioMode();
  try {
    const { sound } = await Audio.Sound.createAsync(SOURCES[tone], {
      isLooping: true,
      shouldPlay: true,
      volume: tone === "ringback" ? 0.85 : 1.0
    });
    if (generation !== toneGeneration) {
      // A newer start or a stop superseded this load; discard it so it can never
      // become a second looping tone with no owner to stop it.
      await sound.stopAsync().catch(() => undefined);
      await sound.unloadAsync().catch(() => undefined);
      return;
    }
    loopSound = sound;
    loopKey = tone;
    if (tone === "ringtone") startIncomingVibration();
  } catch {
    if (generation === toneGeneration) {
      loopSound = null;
      loopKey = null;
    }
  }
}

export async function stopCallTone() {
  // Supersede any in-flight startCallTone load so it discards itself.
  toneGeneration++;
  stopIncomingVibration();
  const sound = loopSound;
  loopSound = null;
  loopKey = null;
  if (!sound) return;
  try {
    await sound.stopAsync();
  } catch {
    // ignore
  }
  await sound.unloadAsync().catch(() => undefined);
}

export async function playCallCue(cue: CueTone) {
  await stopCallTone();
  await ensureAudioMode();
  try {
    const { sound } = await Audio.Sound.createAsync(SOURCES[cue], { shouldPlay: true, volume: 0.9 });
    sound.setOnPlaybackStatusUpdate((status) => {
      if (status.isLoaded && status.didJustFinish) sound.unloadAsync().catch(() => undefined);
    });
  } catch {
    // ignore
  }
}

export function callHaptic(kind: "place" | "answer" | "decline" | "end") {
  if (Platform.OS === "web") return;
  if (kind === "place") {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
  } else if (kind === "answer") {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
  } else {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Rigid).catch(() => undefined);
  }
}
