import { Audio, AVPlaybackStatus, InterruptionModeAndroid, InterruptionModeIOS } from "expo-av";
import { listPulseRadioTracks, PulseRadioTrack, recordPulseRadioPlay } from "../api/radio";
import { claimMediaPlayback, releaseMediaPlayback, subscribeMediaPlayback } from "./mediaPlaybackCoordinator";
import { clearNowPlaying, onRemoteCommand, pushNowPlayingInfo, pushNowPlayingProgress, RemoteCommandEvent } from "../native/nowPlayingBridge";
import {
  buildSequentialOrder,
  buildShuffledOrder,
  nextOrderPosition,
  previousOrderPosition,
  nextRepeatMode,
  reindexOrderAfterMove,
  reindexOrderAfterRemoval,
  RepeatMode
} from "./pulseRadioQueueOrder";

export type { RepeatMode };

export type PulseRadioState = {
  status: "paused" | "connecting" | "buffering" | "playing" | "error" | "offline";
  track: PulseRadioTrack | null;
  message: string;
  userWantsPlayback: boolean;
  interruptedBy: string | null;
  queue: PulseRadioTrack[];
  queueIndex: number;
  shuffle: boolean;
  repeatMode: RepeatMode;
  positionMillis: number;
  durationMillis: number;
};

// Rewinding to the start of the current track (instead of jumping to the
// previous one) once playback has progressed this far mirrors standard
// music-player behavior (Spotify, Apple Music, etc.).
const RESTART_INSTEAD_OF_PREVIOUS_MS = 3000;
const SEEK_STEP_MS = 15000;

const listeners = new Set<(state: PulseRadioState) => void>();
let state: PulseRadioState = {
  status: "paused",
  track: null,
  message: "Tap to play",
  userWantsPlayback: false,
  interruptedBy: null,
  queue: [],
  queueIndex: -1,
  shuffle: false,
  repeatMode: "off",
  positionMillis: 0,
  durationMillis: 0
};
let sound: Audio.Sound | null = null;
let order: number[] = [];
let orderPosition = 0;
let intentGeneration = 0;
let resumeScheduled = false;
let lastInterruptionOwner: string | null = null;
let tracksLoaded = false;

subscribeMediaPlayback((owner) => {
  if (owner?.id && owner.id !== "pulse-radio") lastInterruptionOwner = owner.kind;
  if (!owner && state.userWantsPlayback && state.interruptedBy) scheduleRadioResume();
});

onRemoteCommand(handleRemoteCommand);

export function getPulseRadioState() {
  return state;
}

/** Camera-only monitoring control; it never changes queue or playback state. */
export async function setPulseRadioVideoMonitorVolume(value: number) {
  await sound?.setVolumeAsync(Math.max(0, Math.min(1, value)) * 0.72).catch(() => undefined);
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
  pushNowPlayingProgress(state.positionMillis / 1000, false, 0);
  if (releaseOwnership) await releaseMediaPlayback("pulse-radio");
}

export async function playNextTrack() {
  const generation = ++intentGeneration;
  const next = nextOrderPosition(order.length, orderPosition, state.repeatMode);
  if (next === null) {
    await stopAtEndOfQueue();
    return;
  }
  orderPosition = next;
  update({ userWantsPlayback: true, interruptedBy: null });
  const granted = await claimMediaPlayback({ id: "pulse-radio", kind: "radio", pause: () => pausePulseRadio(false), stop: () => pausePulseRadio(false) });
  if (!granted) {
    update({ status: "paused", message: "Pulse Radio is paused for active audio.", interruptedBy: lastInterruptionOwner || "active_audio" });
    return;
  }
  await startPlayback(generation);
}

export async function playPreviousTrack() {
  const generation = ++intentGeneration;
  if (state.positionMillis > RESTART_INSTEAD_OF_PREVIOUS_MS && sound) {
    await sound.setPositionAsync(0).catch(() => undefined);
    update({ positionMillis: 0 });
    pushNowPlayingProgress(0, state.status === "playing", 1);
    return;
  }
  const prev = previousOrderPosition(order.length, orderPosition, state.repeatMode);
  if (prev === null) {
    if (sound) {
      await sound.setPositionAsync(0).catch(() => undefined);
      update({ positionMillis: 0 });
      pushNowPlayingProgress(0, state.status === "playing", 1);
    }
    return;
  }
  orderPosition = prev;
  update({ userWantsPlayback: true, interruptedBy: null });
  const granted = await claimMediaPlayback({ id: "pulse-radio", kind: "radio", pause: () => pausePulseRadio(false), stop: () => pausePulseRadio(false) });
  if (!granted) {
    update({ status: "paused", message: "Pulse Radio is paused for active audio.", interruptedBy: lastInterruptionOwner || "active_audio" });
    return;
  }
  await startPlayback(generation);
}

export async function playQueueTrackAt(queueIndexToPlay: number) {
  if (queueIndexToPlay < 0 || queueIndexToPlay >= state.queue.length) return;
  const generation = ++intentGeneration;
  const foundPosition = order.indexOf(queueIndexToPlay);
  orderPosition = foundPosition >= 0 ? foundPosition : queueIndexToPlay;
  update({ userWantsPlayback: true, interruptedBy: null });
  const granted = await claimMediaPlayback({ id: "pulse-radio", kind: "radio", pause: () => pausePulseRadio(false), stop: () => pausePulseRadio(false) });
  if (!granted) {
    update({ status: "paused", message: "Pulse Radio is paused for active audio.", interruptedBy: lastInterruptionOwner || "active_audio" });
    return;
  }
  await startPlayback(generation);
}

export async function seekPulseRadioTo(positionMillis: number) {
  if (!sound) return;
  const duration = state.durationMillis;
  const clamped = duration > 0 ? Math.max(0, Math.min(duration, positionMillis)) : Math.max(0, positionMillis);
  await sound.setPositionAsync(clamped).catch(() => undefined);
  update({ positionMillis: clamped });
  pushNowPlayingProgress(clamped / 1000, state.status === "playing", 1);
}

export async function seekPulseRadioBy(deltaMillis: number) {
  await seekPulseRadioTo(state.positionMillis + deltaMillis);
}

export function setPulseRadioShuffle(enabled: boolean) {
  if (enabled === state.shuffle) return;
  if (enabled) {
    order = buildShuffledOrder(state.queue.length, state.queueIndex);
    orderPosition = 0;
  } else {
    order = buildSequentialOrder(state.queue.length);
    orderPosition = Math.max(0, state.queueIndex);
  }
  update({ shuffle: enabled });
}

export function togglePulseRadioShuffle() {
  setPulseRadioShuffle(!state.shuffle);
}

export function setPulseRadioRepeatMode(mode: RepeatMode) {
  update({ repeatMode: mode });
}

export function cyclePulseRadioRepeatMode() {
  update({ repeatMode: nextRepeatMode(state.repeatMode) });
}

export async function moveQueueTrack(fromIndex: number, toIndex: number) {
  const queue = state.queue.slice();
  if (fromIndex < 0 || fromIndex >= queue.length || toIndex < 0 || toIndex >= queue.length || fromIndex === toIndex) return;
  const [moved] = queue.splice(fromIndex, 1);
  queue.splice(toIndex, 0, moved);
  order = reindexOrderAfterMove(order, fromIndex, toIndex);
  const [remappedQueueIndex] = reindexOrderAfterMove([state.queueIndex], fromIndex, toIndex);
  orderPosition = order.indexOf(remappedQueueIndex >= 0 ? remappedQueueIndex : 0);
  if (orderPosition < 0) orderPosition = 0;
  update({ queue, queueIndex: remappedQueueIndex });
}

export async function removeQueueTrackAt(index: number) {
  const queue = state.queue.slice();
  if (index < 0 || index >= queue.length) return;
  const removingCurrent = index === state.queueIndex;
  queue.splice(index, 1);
  order = reindexOrderAfterRemoval(order, index);
  const [remappedQueueIndex] = reindexOrderAfterRemoval([state.queueIndex], index);
  if (removingCurrent) {
    if (sound) {
      const activeSound = sound;
      sound = null;
      await activeSound.unloadAsync().catch(() => undefined);
    }
    if (!queue.length) {
      order = [];
      orderPosition = 0;
      update({
        queue,
        queueIndex: -1,
        status: "paused",
        track: null,
        message: "Tap to play",
        userWantsPlayback: false,
        positionMillis: 0,
        durationMillis: 0
      });
      clearNowPlaying();
      await releaseMediaPlayback("pulse-radio").catch(() => undefined);
      return;
    }
    const nextPosition = Math.min(orderPosition, order.length - 1);
    orderPosition = Math.max(0, nextPosition);
    update({ queue });
    if (state.userWantsPlayback) {
      const generation = ++intentGeneration;
      await startPlayback(generation);
    } else {
      update({ status: "paused", track: null, message: "Tap to play", queueIndex: order[orderPosition] ?? -1 });
    }
    return;
  }
  orderPosition = order.indexOf(remappedQueueIndex >= 0 ? remappedQueueIndex : 0);
  if (orderPosition < 0) orderPosition = 0;
  update({ queue, queueIndex: remappedQueueIndex });
}

async function stopAtEndOfQueue() {
  const activeSound = sound;
  sound = null;
  if (activeSound) await activeSound.unloadAsync().catch(() => undefined);
  update({ status: "paused", userWantsPlayback: false, message: state.track ? "End of queue" : "Tap to play", positionMillis: 0 });
  pushNowPlayingProgress(0, false, 0);
  await releaseMediaPlayback("pulse-radio").catch(() => undefined);
}

async function startPlayback(generation: number) {
  update({ status: "connecting", message: "Connecting…" });
  try {
    if (!tracksLoaded) {
      const fetched = await listPulseRadioTracks();
      tracksLoaded = true;
      if (generation !== intentGeneration) return;
      order = buildSequentialOrder(fetched.length);
      orderPosition = Math.min(orderPosition, Math.max(0, fetched.length - 1));
      update({ queue: fetched });
    }
    if (!state.queue.length) throw new Error("Pulse Radio has no playable tracks right now.");
    if (!order.length) order = buildSequentialOrder(state.queue.length);
    const queueIndex = order[orderPosition] ?? 0;
    const track = state.queue[queueIndex];
    if (!track) throw new Error("Pulse Radio has no playable tracks right now.");
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
    update({ status: "playing", track, queueIndex, message: `${track.title} · ${track.artist}`, positionMillis: 0, durationMillis: 0 });
    pushNowPlayingInfo({
      title: track.title,
      artist: track.artist,
      artworkUrl: track.coverArtUrl || null,
      durationSeconds: 0,
      positionSeconds: 0,
      isPlaying: true
    });
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
  const positionMillis = playback.positionMillis ?? state.positionMillis;
  const durationMillis = playback.durationMillis ?? state.durationMillis;
  if (positionMillis !== state.positionMillis || durationMillis !== state.durationMillis) {
    update({ positionMillis, durationMillis });
  }
  if (playback.isBuffering && !playback.isPlaying) {
    update({ status: "buffering", message: "Buffering…" });
  } else if (playback.isPlaying && state.status === "buffering") {
    const queueIndex = order[orderPosition] ?? state.queueIndex;
    const track = state.track || state.queue[queueIndex];
    update({ status: "playing", track, message: track ? `${track.title} · ${track.artist}` : "Now Playing" });
  }
  if (playback.isPlaying) {
    pushNowPlayingProgress(positionMillis / 1000, true, 1);
    if (durationMillis && durationMillis !== state.durationMillis) {
      pushNowPlayingInfo({
        title: state.track?.title || "",
        artist: state.track?.artist || "",
        artworkUrl: state.track?.coverArtUrl || null,
        durationSeconds: durationMillis / 1000,
        positionSeconds: positionMillis / 1000,
        isPlaying: true
      });
    }
  }
  if (playback.didJustFinish) {
    const next = nextOrderPosition(order.length, orderPosition, state.repeatMode);
    sound?.unloadAsync().catch(() => undefined);
    sound = null;
    if (next === null) {
      stopAtEndOfQueue().catch(() => undefined);
      return;
    }
    orderPosition = next;
    update({ status: "paused", message: "Loading next track…" });
    const nextGeneration = ++intentGeneration;
    startPlayback(nextGeneration).catch(() => undefined);
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

function handleRemoteCommand(event: RemoteCommandEvent) {
  switch (event.command) {
    case "play":
      playPulseRadio().catch(() => undefined);
      break;
    case "pause":
      pausePulseRadio().catch(() => undefined);
      break;
    case "toggle":
      togglePulseRadio().catch(() => undefined);
      break;
    case "next":
      playNextTrack().catch(() => undefined);
      break;
    case "previous":
      playPreviousTrack().catch(() => undefined);
      break;
    case "seek":
      seekPulseRadioTo(event.positionSeconds * 1000).catch(() => undefined);
      break;
    case "skipForward":
      seekPulseRadioBy((event.intervalSeconds || SEEK_STEP_MS / 1000) * 1000).catch(() => undefined);
      break;
    case "skipBackward":
      seekPulseRadioBy(-(event.intervalSeconds || SEEK_STEP_MS / 1000) * 1000).catch(() => undefined);
      break;
    default:
      break;
  }
}

function update(patch: Partial<PulseRadioState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener(state));
}
