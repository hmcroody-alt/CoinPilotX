import type { LiveRtcCredentials } from "./liveSession";
import { useAgoraLiveBroadcastRoom } from "./useAgoraLiveBroadcastRoom";
import type { LiveMusicMixingState, LiveMusicMixingTrack } from "./liveMusicMixing";

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
  startLiveMusicMixing: (track: LiveMusicMixingTrack) => Promise<void>;
  pauseLiveMusicMixing: () => Promise<void>;
  resumeLiveMusicMixing: () => Promise<void>;
  stopLiveMusicMixing: () => Promise<void>;
  setLiveMusicVolume: (level: number) => Promise<void>;
  setLiveMicVolume: (level: number) => Promise<void>;
};

/** Agora is PulseSoc's sole native interactive Live provider. */
export function useLiveBroadcastRoom() {
  return useAgoraLiveBroadcastRoom();
}
