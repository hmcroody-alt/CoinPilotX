import { Platform } from "react-native";

export type RealtimeAudioRole = "interactive" | "listener";

export type AppleRealtimeAudioConfiguration = {
  audioCategory: string;
  audioMode: string;
  audioCategoryOptions: string[];
};

export type RealtimeAudioRuntime = {
  session: any;
  configuration: AppleRealtimeAudioConfiguration;
};

export type RealtimeRoomAudioState = {
  publishMicrophone: boolean;
  microphoneEnabled: boolean;
  remoteAudioEnabled: boolean;
};

let globalsRegistered = false;

export function resolveRealtimeAudioConfiguration(role: RealtimeAudioRole): AppleRealtimeAudioConfiguration {
  if (role === "interactive") {
    return {
      audioCategory: "playAndRecord",
      audioMode: "videoChat",
      audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"]
    };
  }
  return {
    audioCategory: "playback",
    audioMode: "moviePlayback",
    audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"]
  };
}

export function registerRealtimeAudioGlobals(livekitNative: any) {
  if (globalsRegistered) return;
  livekitNative.registerGlobals({ autoConfigureAudioSession: false });
  globalsRegistered = true;
}

export async function resumeRealtimeAudioSession(
  session: any,
  configuration: AppleRealtimeAudioConfiguration,
  platform = Platform.OS
): Promise<void> {
  if (!session) throw new Error("Realtime audio session is not available.");
  if (platform === "ios" && typeof session.setAppleAudioConfiguration === "function") {
    await session.setAppleAudioConfiguration(configuration).catch(() => undefined);
  }
  if (typeof session.configureAudio === "function") {
    await session.configureAudio({ ios: { defaultOutput: "speaker" } }).catch(() => undefined);
  }
  await session.startAudioSession();
}

export async function startRealtimeAudioSession(
  livekitNative: any,
  role: RealtimeAudioRole
): Promise<RealtimeAudioRuntime> {
  registerRealtimeAudioGlobals(livekitNative);
  const runtime = {
    session: livekitNative.AudioSession,
    configuration: resolveRealtimeAudioConfiguration(role)
  };
  await resumeRealtimeAudioSession(runtime.session, runtime.configuration);
  return runtime;
}

export async function stopRealtimeAudioSession(runtime: RealtimeAudioRuntime | null | undefined): Promise<void> {
  await runtime?.session?.stopAudioSession?.();
}

export function realtimeRoomOptions() {
  return {
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    },
    publishDefaults: {
      simulcast: true,
      dtx: true,
      red: true,
      stopMicTrackOnMute: false
    }
  };
}

export function audioPublications(participant: any): any[] {
  return Array.from(participant?.audioTrackPublications?.values?.() || []) as any[];
}

export function publicationHasSubscribedTrack(publication: any): boolean {
  return Boolean(publication?.track && publication?.isSubscribed !== false);
}

export function countPublishedAudioTracks(participant: any): number {
  return audioPublications(participant).filter(publicationHasSubscribedTrack).length;
}

export function countSubscribedRemoteAudioTracks(room: any): number {
  return Array.from(room?.remoteParticipants?.values?.() || []).reduce(
    (total: number, participant: any) => total + countPublishedAudioTracks(participant),
    0
  );
}

export async function applyRemoteAudioEnabled(room: any, enabled: boolean): Promise<number> {
  let touched = 0;
  const tasks: Promise<unknown>[] = [];
  for (const remote of Array.from(room?.remoteParticipants?.values?.() || []) as any[]) {
    for (const publication of audioPublications(remote)) {
      const track = publication?.track;
      if (!track || publication?.isSubscribed === false) continue;
      if (typeof track.setEnabled === "function") {
        tasks.push(Promise.resolve(track.setEnabled(enabled)));
        touched += 1;
      } else if (track.mediaStreamTrack) {
        track.mediaStreamTrack.enabled = enabled;
        touched += 1;
      }
    }
  }
  await Promise.all(tasks);
  return touched;
}

export async function ensureMicrophonePublished(room: any, retryDelayMs = 150): Promise<number> {
  const localParticipant = room?.localParticipant;
  if (!localParticipant) return 0;
  await localParticipant.setMicrophoneEnabled(true);
  let count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
  count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await localParticipant.setMicrophoneEnabled(false).catch(() => undefined);
  await localParticipant.setMicrophoneEnabled(true);
  await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
  return countPublishedAudioTracks(localParticipant);
}

export async function setLocalMicrophoneEnabled(room: any, enabled: boolean): Promise<number> {
  if (!room?.localParticipant) throw new Error("Realtime microphone is not connected.");
  if (!enabled) {
    await room.localParticipant.setMicrophoneEnabled(false);
    return countPublishedAudioTracks(room.localParticipant);
  }
  return ensureMicrophonePublished(room);
}

export async function restoreRealtimeRoomAudio(
  room: any,
  state: RealtimeRoomAudioState
): Promise<{ localAudioTrackCount: number; remoteAudioTrackCount: number }> {
  const localAudioTrackCount = state.publishMicrophone
    ? await setLocalMicrophoneEnabled(room, state.microphoneEnabled)
    : 0;
  await applyRemoteAudioEnabled(room, state.remoteAudioEnabled);
  return {
    localAudioTrackCount,
    remoteAudioTrackCount: countSubscribedRemoteAudioTracks(room)
  };
}

export async function selectRealtimeSpeakerOutput(
  runtime: RealtimeAudioRuntime | null | undefined,
  enabled: boolean,
  platform = Platform.OS
): Promise<void> {
  if (!runtime?.session) throw new Error("Realtime audio session is not available.");
  const output = platform === "ios" ? (enabled ? "force_speaker" : "default") : enabled ? "speaker" : "earpiece";
  await runtime.session.selectAudioOutput(output);
}

export async function showRealtimeAudioRoutePicker(
  runtime: RealtimeAudioRuntime | null | undefined,
  platform = Platform.OS
): Promise<void> {
  if (!runtime?.session) throw new Error("Realtime audio session is not available.");
  if (platform === "ios") await runtime.session.showAudioRoutePicker();
}
