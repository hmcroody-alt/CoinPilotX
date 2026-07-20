import type { EventSubscription } from "expo-modules-core";

export type NowPlayingInfo = {
  title: string;
  artist: string;
  artworkUrl?: string | null;
  durationSeconds?: number;
  positionSeconds?: number;
  isPlaying?: boolean;
};

export type RemoteCommandEvent =
  | { command: "play" | "pause" | "toggle" | "next" | "previous" }
  | { command: "seek"; positionSeconds: number }
  | { command: "skipForward" | "skipBackward"; intervalSeconds: number };

export declare const isNowPlayingSupported: boolean;

export declare function setNowPlayingInfo(info: NowPlayingInfo): void;
export declare function updatePlaybackProgress(positionSeconds: number, isPlaying: boolean, rate?: number): void;
export declare function clearNowPlayingInfo(): void;
export declare function subscribeRemoteCommand(
  listener: (event: RemoteCommandEvent) => void
): EventSubscription | null;
