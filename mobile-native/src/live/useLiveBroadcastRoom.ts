import type { LiveRtcCredentials } from "./liveSession";
import { useAgoraLiveBroadcastRoom } from "./useAgoraLiveBroadcastRoom";

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

/** Agora is PulseSoc's sole native interactive Live provider. */
export function useLiveBroadcastRoom() {
  return useAgoraLiveBroadcastRoom();
}
