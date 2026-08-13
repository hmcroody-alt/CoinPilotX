import type { LiveRtcCredentials } from "./liveSession";
import { useAgoraLiveBroadcastRoom } from "./useAgoraLiveBroadcastRoom";
import type { LiveMusicMixingState, LiveMusicMixingTrack } from "./liveMusicMixing";
import type { CreatorMixerSettings } from "../audio/creatorMixer";

export type LiveParticipant = {
  identity: string;
  name: string;
  isLocal: boolean;
  isHost: boolean;
  videoTrack: any | null;
  audioTrack: any | null;
  hasVideo: boolean;
  hasAudio: boolean;
  audioMuted: boolean;
  speaking: boolean;
};

export type LiveConnectOptions = {
  publish?: boolean;
  video?: boolean;
  refreshCredentials?: () => Promise<LiveRtcCredentials | null>;
};

export type LiveBroadcastRoom = ReturnType<typeof useAgoraLiveBroadcastRoom> & {
  liveMusic: LiveMusicMixingState;
  startLiveMusicMixing: (track: LiveMusicMixingTrack, options?: { startOffsetSeconds?: number; mixer?: CreatorMixerSettings }) => Promise<void>;
  pauseLiveMusicMixing: () => Promise<void>;
  resumeLiveMusicMixing: () => Promise<void>;
  stopLiveMusicMixing: () => Promise<void>;
  setLiveMusicVolume: (level: number) => Promise<void>;
  setLiveMicVolume: (level: number) => Promise<void>;
  /** Apply the whole shared mixer — both faders and the ducking settings — at once. */
  setLiveMusicMixer: (settings: CreatorMixerSettings) => Promise<void>;
  /** Move the cue point of the running track without restarting the mix. */
  setLiveMusicPosition: (seconds: number) => Promise<void>;
};

/** Agora is PulseSoc's sole native interactive Live provider. */
export function useLiveBroadcastRoom() {
  return useAgoraLiveBroadcastRoom();
}
