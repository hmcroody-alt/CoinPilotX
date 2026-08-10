import { useAgoraCallRoom } from "./useAgoraCallRoom";

/** Agora is PulseSoc's sole native call media provider. */
export function useNativeCallRoom() {
  return useAgoraCallRoom();
}
