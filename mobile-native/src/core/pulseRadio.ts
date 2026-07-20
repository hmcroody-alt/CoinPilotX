import { Audio, AVPlaybackStatus, InterruptionModeAndroid, InterruptionModeIOS } from "expo-av";
import { listPulseRadioTracks, PulseRadioTrack, recordPulseRadioPlay } from "../api/radio";
import { claimMediaPlayback, releaseMediaPlayback, subscribeMediaPlayback } from "./mediaPlaybackCoordinator";

export type PulseRadioState = {
  status: "paused" | "connecting" | "buffering" | "playing" | "error" | "offline";
  track: PulseRadioTrack | null;
  message: string;
  userWantsPlayback: boolean;
  interruptedBy: string | null;
};

const listeners = new Set<(state: PulseRadioState) => void>();
let state: PulseRadioState = { status: "paused", track: null, message: "Tap to play", userWantsPlayback: false, interruptedBy: null };
let sound: Audio.Sound | null = null;
let tracks: PulseRadioTrack[] = [];
let trackIndex = 0;
let intentGeneration = 0;
let resumeScheduled = false;
let lastInterruptionOwner: string | null = null;

subscribeMediaPlayback((owner) => {
  if (owner?.id && owner.id !== "pulse-radio") lastInterruptionOwner = owner.kind;
  if (!owner && state.userWantsPlayback && state.interruptedBy) scheduleRadioResume();
});

export function getPulseRadioState() {
  return state;
}

export function subscribePulseRadio(listener: (next: PulseRadioState) => void) {
  listeners.add(listener);
  listener(state);
  return () => {
    listeners.delete(listener);
  };
}

export async function togglePulseRadio() {
  if (state.status === "playing" || state.status === "connecting" || state.status === "buffering") return pausePulseRadio();
  if (state.userWantsPlayback && state.interruptedBy) return pausePulseRadio();
  return playPulseRadio();
}

export async function playPulseRadio() {
  if (state.status === "playing" || state.status === "connecting" || state.status === "buffering") return;
  const generation = ++intentGeneration;
  update({ userWantsPlayback: true, interruptedBy: null });
  const granted = await claimMediaPlayback({ id: "pulse-radio", kind: "radio", pause: () => pausePulseRadio(false), stop: () => pausePulseRadio(false) });
  if (!granted) {
    update({ status: "paused", message: "Pulse Radio is paused for active audio.", interruptedBy: lastInterruptionOwner || "active_audio" });
    return;
  }
  await startPlayback(generation);
}

export async function pausePulseRadio(releaseOwnership = true) {
  intentGeneration += 1;
  const interruptedBy = releaseOwnership ? null : lastInterruptionOwner || "active_audio";
  const activeSound = sound;
  sound = null;
  if (activeSound) await activeSound.unloadAsync().catch(() => undefined);
  update({
    status: "paused",
    message: interruptedBy ? "Pulse Radio paused for active audio." : state.track ? "Paused" : "Tap to play",
    userWantsPlayback: releaseOwnership ? false : state.userWantsPlayback,
    interruptedBy
  });
  if (releaseOwnership) await releaseMediaPlayback("pulse-radio");
}

async function startPlayback(generation: number) {
  update({ status: "connecting", message: "Connecting…" });
  try {
    if (!tracks.length) tracks = await listPulseRadioTracks();
    if (generation !== intentGeneration) return;
    if (!tracks.length) throw new Error("Pulse Radio has no playable tracks right now.");
    const track = tracks[trackIndex % tracks.length];
    await configureAudio();
    if (generation !== intentGeneration) return;
    if (sound) await sound.unloadAsync().catch(() => undefined);
    const created = await Audio.Sound.createAsync(
      { uri: track.audioUrl },
      { shouldPlay: true, progressUpdateIntervalMillis: 1000 },
      (playback) => handlePlaybackStatus(playback, generation)
    );
    if (generation !== intentGeneration) {
      await created.sound.unloadAsync().catch(() => undefined);
      return;
    }
    sound = created.sound;
    update({ status: "playing", track, message: `${track.title} · ${track.artist}` });
    recordPulseRadioPlay(track.id).catch(() => undefined);
  } catch (error) {
    if (generation !== intentGeneration) return;
    const detail = error instanceof Error ? error.message : "";
    const offline = /reach|network|connection|offline|internet/i.test(detail);
    update({
      status: offline ? "offline" : "error",
      message: offline ? "Connect to the internet to play Pulse Radio." : "Pulse Radio is unavailable. Tap to retry."
    });
    await releaseMediaPlayback("pulse-radio").catch(() => undefined);
  }
}

function handlePlaybackStatus(playback: AVPlaybackStatus, generation: number) {
  if (generation !== intentGeneration) return;
  if (!playback.isLoaded) {
    if (playback.error) update({ status: "error", message: "This track could not be played." });
    return;
  }
  if (playback.isBuffering && !playback.isPlaying) {
    update({ status: "buffering", message: "Buffering…" });
  } else if (playback.isPlaying && state.status === "buffering") {
    const track = state.track || tracks[trackIndex % tracks.length];
    update({ status: "playing", track, message: track ? `${track.title} · ${track.artist}` : "Now Playing" });
  }
  if (playback.didJustFinish) {
    trackIndex = (trackIndex + 1) % tracks.length;
    sound?.unloadAsync().catch(() => undefined);
    sound = null;
    update({ status: "paused", message: "Loading next track…" });
    playPulseRadio().catch(() => undefined);
  }
}

async function configureAudio() {
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: false,
    staysActiveInBackground: true,
    playsInSilentModeIOS: true,
    interruptionModeIOS: InterruptionModeIOS.DoNotMix,
    interruptionModeAndroid: InterruptionModeAndroid.DoNotMix,
    shouldDuckAndroid: false,
    playThroughEarpieceAndroid: false
  });
}

function scheduleRadioResume() {
  if (resumeScheduled) return;
  resumeScheduled = true;
  setTimeout(() => {
    resumeScheduled = false;
    if (!state.userWantsPlayback || !state.interruptedBy) return;
    playPulseRadio().catch(() => undefined);
  }, 180);
}

function update(patch: Partial<PulseRadioState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener(state));
}
