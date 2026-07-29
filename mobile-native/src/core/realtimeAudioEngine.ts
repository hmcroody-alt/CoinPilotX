import { Platform } from "react-native";

export type RealtimeAudioMode =
  | "none"
  | "audio_call"
  | "video_call"
  | "live_host"
  | "live_guest"
  | "live_viewer"
  | "voice_message"
  | "music_playback";

export type AppleAudioConfiguration = {
  audioCategory: string;
  audioMode: string;
  audioCategoryOptions: string[];
};

export type RealtimeAudioOwner = {
  ownerId: string;
  mode: RealtimeAudioMode;
  startedAt: number;
};

type LiveKitAudioSession = {
  setAppleAudioConfiguration?: (config: any) => Promise<void>;
  configureAudio?: (config: Record<string, unknown>) => Promise<void>;
  startAudioSession?: () => Promise<void>;
  stopAudioSession?: () => Promise<void>;
  selectAudioOutput?: (deviceId: string) => Promise<void>;
  showAudioRoutePicker?: () => Promise<void>;
};

let activeRealtimeAudioOwner: RealtimeAudioOwner | null = null;

/**
 * Canonical PulseSoc realtime audio profile.
 *
 * Calls are the known-good path, so Live host/guest/viewer sessions intentionally
 * use the same playAndRecord/videoChat AVAudioSession profile. Viewers still use
 * playAndRecord because LiveKit remote audio needs the WebRTC/call route to stay
 * compatible with speaker, Bluetooth, AirPlay, interruptions, and reconnects.
 */
export function resolveRealtimeAudioConfiguration(mode: RealtimeAudioMode | boolean): AppleAudioConfiguration {
  const normalizedMode: RealtimeAudioMode = typeof mode === "boolean" ? (mode ? "live_host" : "live_viewer") : mode;
  if (
    normalizedMode === "audio_call" ||
    normalizedMode === "video_call" ||
    normalizedMode === "live_host" ||
    normalizedMode === "live_guest" ||
    normalizedMode === "live_viewer" ||
    normalizedMode === "voice_message"
  ) {
    return {
      audioCategory: "playAndRecord",
      audioMode: "videoChat",
      audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"]
    };
  }
  return {
    audioCategory: "playback",
    audioMode: "default",
    audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"]
  };
}

export function getActiveRealtimeAudioOwner(): RealtimeAudioOwner | null {
  return activeRealtimeAudioOwner ? { ...activeRealtimeAudioOwner } : null;
}

export function claimRealtimeAudioSession(mode: RealtimeAudioMode, ownerId: string): RealtimeAudioOwner {
  activeRealtimeAudioOwner = {
    ownerId,
    mode,
    startedAt: Date.now()
  };
  return getActiveRealtimeAudioOwner() as RealtimeAudioOwner;
}

export async function activateRealtimeAudioSession(
  audioSession: LiveKitAudioSession,
  mode: RealtimeAudioMode,
  ownerId: string,
  options: { speaker?: boolean } = {}
): Promise<RealtimeAudioOwner> {
  const owner = claimRealtimeAudioSession(mode, ownerId);
  const config = resolveRealtimeAudioConfiguration(mode);
  if (Platform.OS === "ios" && typeof audioSession.setAppleAudioConfiguration === "function") {
    await audioSession.setAppleAudioConfiguration(config).catch(() => undefined);
  }
  if (typeof audioSession.configureAudio === "function") {
    await audioSession.configureAudio({ ios: { defaultOutput: options.speaker === false ? "default" : "speaker" } }).catch(() => undefined);
  }
  if (typeof audioSession.startAudioSession === "function") {
    await audioSession.startAudioSession();
  }
  if (options.speaker !== false) {
    await selectRealtimeAudioOutput(audioSession, true).catch(() => undefined);
  }
  return owner;
}

export async function releaseRealtimeAudioSession(audioSession: LiveKitAudioSession | null | undefined, ownerId: string): Promise<boolean> {
  if (!activeRealtimeAudioOwner || activeRealtimeAudioOwner.ownerId !== ownerId) return false;
  activeRealtimeAudioOwner = null;
  await audioSession?.stopAudioSession?.().catch(() => undefined);
  return true;
}

export async function selectRealtimeAudioOutput(audioSession: LiveKitAudioSession, speakerEnabled: boolean): Promise<void> {
  const output = Platform.OS === "ios" ? (speakerEnabled ? "force_speaker" : "default") : speakerEnabled ? "speaker" : "earpiece";
  await audioSession.selectAudioOutput?.(output);
}

export const PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION = {
  width: 720,
  height: 1280,
  frameRate: 30,
  aspectRatio: 9 / 16
};

export const PULSE_LIVE_VIDEO_CAPTURE_OPTIONS = {
  facingMode: "user" as const,
  frameRate: 30,
  resolution: PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION
};

export const PULSE_LIVE_VIDEO_PUBLISH_OPTIONS = {
  videoEncoding: {
    maxBitrate: 2_300_000,
    maxFramerate: 30,
    priority: "medium" as const
  },
  simulcast: true
};

export function audioPublications(participant: any): any[] {
  return Array.from(participant?.audioTrackPublications?.values?.() || []) as any[];
}

export function videoPublications(participant: any): any[] {
  return Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
}

export function publicationHasTrack(publication: any): boolean {
  return Boolean(publication?.track && publication?.isSubscribed !== false);
}

export function countPublishedAudioTracks(participant: any): number {
  return audioPublications(participant).filter(publicationHasTrack).length;
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
  await Promise.all(tasks).catch(() => undefined);
  return touched;
}

export async function ensureMicrophonePublished(room: any): Promise<number> {
  const localParticipant = room?.localParticipant;
  if (!localParticipant) return 0;
  await localParticipant.setMicrophoneEnabled(true);
  let count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await new Promise((resolve) => setTimeout(resolve, 150));
  count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await localParticipant.setMicrophoneEnabled(false).catch(() => undefined);
  await localParticipant.setMicrophoneEnabled(true);
  await new Promise((resolve) => setTimeout(resolve, 150));
  return countPublishedAudioTracks(localParticipant);
}
