/**
 * Agora media adapter hook. PulseSoc backend signaling remains authoritative.
 *
 * The engine and its state now live in `callSessionStore` (module scope), so a
 * call survives in-app navigation: this hook is a thin subscription over that
 * store. There is deliberately NO unmount cleanup here — the pre-store version
 * called `disconnect("unmounted")` when the Call screen unmounted, which is
 * exactly what killed a live call the moment the user navigated away. The
 * engine is released only on explicit hang-up/decline, a terminal backend
 * status, or an authoritative failure (see the store).
 */
import {
  connectCallMedia,
  disconnectCallMedia,
  setCallCameraEnabled,
  setCallMicrophoneEnabled,
  setCallSpeakerEnabled,
  showCallAudioRoutePicker,
  switchCallCamera,
  useCallSession
} from "./callSessionStore";

export { addAgoraRemoteUid, removeAgoraRemoteUid } from "./callSessionStore";

export function useAgoraCallRoom() {
  const session = useCallSession();
  return {
    ...session,
    lifecycle: null,
    connect: connectCallMedia,
    disconnect: disconnectCallMedia,
    setMicrophoneEnabled: setCallMicrophoneEnabled,
    setCameraEnabled: setCallCameraEnabled,
    setSpeakerEnabled: setCallSpeakerEnabled,
    showAudioRoutePicker: showCallAudioRoutePicker,
    switchCamera: switchCallCamera
  };
}
