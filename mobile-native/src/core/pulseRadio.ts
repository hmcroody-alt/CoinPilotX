import { Audio, AVPlaybackStatus, InterruptionModeAndroid, InterruptionModeIOS } from "expo-av";
import { listPulseRadioTracks, PulseRadioTrack, recordPulseRadioPlay } from "../api/radio";
import { claimMediaPlayback, releaseMediaPlayback, subscribeMediaPlayback } from "./mediaPlaybackCoordinator";
import { connectivityState, subscribeConnectivity } from "./connectivity";
import {
  cacheRadioQueue,
  discardCachedRadioTrack,
  loadCachedRadioQueue,
  nextTrackToWarm,
  resolveRadioSource,
  warmRadioTrack
} from "./radio/radioOffline";
import { radioRecoveryPlan, shouldResumeRadio, type RadioFailureReason } from "./radio/radioRecovery";
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
/** True when the current source is a file on disk rather than a stream. */
let playingFromCache = false;
/** Recovery attempts spent on the track currently being played. */
let recoveryAttempts = 0;
/** Set when playback stopped for the network and is owed a resume. */
let awaitingNetwork = false;
/** Where a resumed track should pick up. Consumed by the next `startPlayback`. */
let pendingResumeMillis = 0;

subscribeMediaPlayback((owner) => {
  if (owner?.id && owner.id !== "pulse-radio") lastInterruptionOwner = owner.kind;
  if (!owner && state.userWantsPlayback && state.interruptedBy) scheduleRadioResume();
});

// §84. The radio survives a temporary loss rather than reporting one. When the
// authority says the network is back and the listener never pressed pause, the
// track resumes from where it stopped — not from zero, which is what made a
// three-second tunnel cost the whole song.
subscribeConnectivity((snapshot) => {
  if (!shouldResumeRadio({ userWantsPlayback: state.userWantsPlayback, connectivity: snapshot.state, awaitingNetwork })) {
    return;
  }
  awaitingNetwork = false;
  recoveryAttempts = 0;
  playPulseRadio().catch(() => undefined);
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
  // A resume owed from a network drop picks the track up where it stopped. Any
  // other play starts at zero, because `pendingResumeMillis` is consumed here
  // and belongs to exactly one attempt.
  const resumeAt = pendingResumeMillis;
  pendingResumeMillis = 0;
  await startPlayback(generation, resumeAt);
}

export async function pausePulseRadio(releaseOwnership = true) {
  intentGeneration += 1;
  // An explicit pause settles the question the recovery loop was asking.
  if (releaseOwnership) {
    awaitingNetwork = false;
    pendingResumeMillis = 0;
  }
  recoveryAttempts = 0;
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
  resetRecoveryForNewTrack();
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
  resetRecoveryForNewTrack();
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
  resetRecoveryForNewTrack();
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

async function startPlayback(generation: number, resumeAtMillis = 0) {
  update({ status: "connecting", message: "Connecting…" });
  try {
    if (!tracksLoaded && !(await fillQueue(generation))) return;
    if (!state.queue.length) throw new Error("Pulse Radio has no playable tracks right now.");
    if (!order.length) order = buildSequentialOrder(state.queue.length);
    const queueIndex = order[orderPosition] ?? 0;
    const track = state.queue[queueIndex];
    if (!track) throw new Error("Pulse Radio has no playable tracks right now.");
    // Disk first, always. A track already on the device starts instantly, costs
    // no data, and cannot be cut off halfway by a tunnel.
    const source = await resolveRadioSource(track);
    if (generation !== intentGeneration) return;
    if (!source) throw new Error("Pulse Radio has no playable tracks right now.");
    await configureAudio();
    if (generation !== intentGeneration) return;
    if (sound) await sound.unloadAsync().catch(() => undefined);
    const created = await Audio.Sound.createAsync(
      { uri: source.uri },
      { shouldPlay: true, positionMillis: resumeAtMillis, progressUpdateIntervalMillis: 1000 },
      (playback) => handlePlaybackStatus(playback, generation)
    );
    if (generation !== intentGeneration) {
      await created.sound.unloadAsync().catch(() => undefined);
      return;
    }
    sound = created.sound;
    playingFromCache = source.offline;
    awaitingNetwork = false;
    update({
      status: "playing",
      track,
      queueIndex,
      message: `${track.title} · ${track.artist}`,
      positionMillis: resumeAtMillis,
      durationMillis: 0
    });
    pushNowPlayingInfo({
      title: track.title,
      artist: track.artist,
      artworkUrl: track.coverArtUrl || null,
      durationSeconds: 0,
      positionSeconds: resumeAtMillis / 1000,
      isPlaying: true
    });
    recordPulseRadioPlay(track.id).catch(() => undefined);
    // One track ahead, never the whole queue: the only track whose availability
    // changes the next few minutes of listening is the one after this one.
    warmRadioTrack(nextTrackToWarm(state.queue, order, orderPosition)).catch(() => undefined);
  } catch {
    if (generation !== intentGeneration) return;
    // Was: a regex over the error's message text. The platform is free to reword
    // "The network connection was lost" at any OS release, and when it did, an
    // offline radio started calling itself broken. The connectivity authority
    // already knows the answer and is the only thing that should be asked.
    const offline = connectivityState() === "offline";
    awaitingNetwork = offline;
    update({
      status: offline ? "offline" : "error",
      message: offline
        ? "Offline — Pulse Radio resumes when you reconnect."
        : "Pulse Radio is unavailable. Tap to retry."
    });
    // Ownership is released either way. Holding the audio session while silent
    // and waiting would block every other sound on the device for the length of
    // the outage.
    await releaseMediaPlayback("pulse-radio").catch(() => undefined);
  }
}

/**
 * Fill the queue, cache first.
 *
 * Returns false when this load has been superseded and the caller must stop.
 *
 * The cached queue is painted before the request goes out, so opening Radio in
 * a tunnel offers last session's tracks instead of "Pulse Radio has no playable
 * tracks right now." — which was never true; it was the app describing its own
 * failed request as a property of the music library.
 */
async function fillQueue(generation: number): Promise<boolean> {
  const cached = await loadCachedRadioQueue().catch(() => null);
  if (generation !== intentGeneration) return false;
  if (cached?.tracks.length) applyQueue(cached.tracks);

  try {
    const fetched = await listPulseRadioTracks();
    if (generation !== intentGeneration) return false;
    if (fetched.length) {
      applyQueue(fetched);
      cacheRadioQueue(fetched).catch(() => undefined);
    }
  } catch (error) {
    if (generation !== intentGeneration) return false;
    // A failed refresh over a queue we already have is not a failure. It only
    // becomes one when there is nothing to play.
    if (!state.queue.length) throw error;
  }

  // Set only once a queue actually exists. The previous code set this beside the
  // request, so a first load that was superseded mid-flight left the flag true
  // and the queue empty — and every later play took the `tracksLoaded` shortcut
  // and reported "no playable tracks" forever, until the app was restarted.
  tracksLoaded = state.queue.length > 0;
  return true;
}

function applyQueue(tracks: PulseRadioTrack[]) {
  order = buildSequentialOrder(tracks.length);
  orderPosition = Math.min(orderPosition, Math.max(0, tracks.length - 1));
  update({ queue: tracks });
}

function handlePlaybackStatus(playback: AVPlaybackStatus, generation: number) {
  if (generation !== intentGeneration) return;
  if (!playback.isLoaded) {
    // Was: straight to `error` with "This track could not be played." A tunnel
    // and a corrupt file produced the same dead end, and both threw the
    // listener's place in the song away.
    if (playback.error) recoverFromFailure("load_failed", generation);
    return;
  }
  const positionMillis = playback.positionMillis ?? state.positionMillis;
  const durationMillis = playback.durationMillis ?? state.durationMillis;
  if (positionMillis !== state.positionMillis || durationMillis !== state.durationMillis) {
    update({ positionMillis, durationMillis });
  }
  if (playback.isBuffering && !playback.isPlaying) {
    // Buffering with no network is not buffering, it is waiting. "Buffering…"
    // over a dead radio is the app claiming progress it is not making, and it
    // hides the one fact that would let the listener act.
    if (!playingFromCache && connectivityState() === "offline") {
      recoverFromFailure("stall", generation);
      return;
    }
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

/**
 * Turn a playback failure into the right next move.
 *
 * The classification lives in `radio/radioRecovery` and is pure; this is only
 * the part that has to touch the player. Every branch carries `positionMillis`
 * forward — a resume that restarts the song is the failure this replaced.
 */
function recoverFromFailure(reason: RadioFailureReason, generation: number) {
  if (generation !== intentGeneration) return;
  const plan = radioRecoveryPlan({
    reason,
    connectivity: connectivityState(),
    attempt: recoveryAttempts,
    positionMillis: state.positionMillis,
    fromCache: playingFromCache
  });
  pendingResumeMillis = plan.resumeAtMillis;

  // A local file that fails is damaged, not delayed. Forget it so the next
  // attempt re-fetches instead of replaying the same broken bytes.
  if (plan.discardCachedCopy) discardCachedRadioTrack(state.track).catch(() => undefined);

  if (plan.action === "await_network") {
    awaitingNetwork = true;
    update({ status: plan.status, message: "Offline — Pulse Radio resumes when you reconnect." });
    return;
  }

  if (plan.action === "give_up") {
    awaitingNetwork = false;
    pendingResumeMillis = 0;
    update({ status: plan.status, message: "This track could not be played. Tap to retry." });
    releaseMediaPlayback("pulse-radio").catch(() => undefined);
    return;
  }

  recoveryAttempts += 1;
  update({ status: plan.status, message: "Reconnecting…" });
  const retryGeneration = ++intentGeneration;
  setTimeout(() => {
    if (retryGeneration !== intentGeneration) return;
    pendingResumeMillis = 0;
    startPlayback(retryGeneration, plan.resumeAtMillis).catch(() => undefined);
  }, plan.delayMs);
}

/** A track the listener chose is a fresh start, with its own retry budget. */
function resetRecoveryForNewTrack() {
  recoveryAttempts = 0;
  pendingResumeMillis = 0;
  awaitingNetwork = false;
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
