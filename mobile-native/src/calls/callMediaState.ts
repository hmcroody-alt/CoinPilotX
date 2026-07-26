import { Platform } from "react-native";

export type CallMediaSummary = {
  localAudioPublished: boolean;
  localAudioMuted: boolean;
  localVideoPublished: boolean;
  remoteAudioSubscribed: boolean;
  remoteAudioMuted: boolean;
  remoteVideoSubscribed: boolean;
  remoteAudioParticipantCount: number;
  remoteVideoParticipantCount: number;
  localVideoTrack: any | null;
  remoteVideoTrack: any | null;
};

function values(collection: any): any[] {
  if (!collection) return [];
  if (typeof collection.values === "function") return Array.from(collection.values());
  if (Array.isArray(collection)) return collection;
  if (typeof collection === "object") return Object.values(collection);
  return [];
}

function hasTrack(publication: any): boolean {
  return Boolean(publication?.track || publication?.trackSid || publication?.sid);
}

function isSubscribed(publication: any): boolean {
  return publication?.isSubscribed !== false && hasTrack(publication);
}

function isMuted(publication: any): boolean {
  return Boolean(publication?.isMuted || publication?.muted || publication?.track?.isMuted);
}

export function summarizeCallMediaState(room: any): CallMediaSummary {
  const localAudioPublications = values(room?.localParticipant?.audioTrackPublications);
  const localVideoPublications = values(room?.localParticipant?.videoTrackPublications);
  const localAudio = localAudioPublications.find(hasTrack);
  const localVideo = localVideoPublications.find(hasTrack);
  const summary: CallMediaSummary = {
    localAudioPublished: Boolean(localAudio),
    localAudioMuted: Boolean(localAudio) ? isMuted(localAudio) : true,
    localVideoPublished: Boolean(localVideo),
    remoteAudioSubscribed: false,
    remoteAudioMuted: true,
    remoteVideoSubscribed: false,
    remoteAudioParticipantCount: 0,
    remoteVideoParticipantCount: 0,
    localVideoTrack: localVideo?.track || null,
    remoteVideoTrack: null
  };

  for (const participant of values(room?.remoteParticipants)) {
    const remoteAudio = values(participant?.audioTrackPublications).find(isSubscribed);
    if (remoteAudio) {
      summary.remoteAudioSubscribed = true;
      summary.remoteAudioMuted = isMuted(remoteAudio);
      summary.remoteAudioParticipantCount += 1;
    }
    const remoteVideo = values(participant?.videoTrackPublications).find(isSubscribed);
    if (remoteVideo) {
      summary.remoteVideoSubscribed = true;
      summary.remoteVideoParticipantCount += 1;
      summary.remoteVideoTrack = remoteVideo.track || summary.remoteVideoTrack;
    }
  }

  return summary;
}

export function shouldSurfaceVideoAudioWarning(input: {
  callType: "audio" | "video";
  connected: boolean;
  localAudioPublished: boolean;
  remoteParticipantCount: number;
  remoteAudioSubscribed: boolean;
}): boolean {
  if (input.callType !== "video" || !input.connected) return false;
  if (!input.localAudioPublished) return true;
  return input.remoteParticipantCount > 0 && !input.remoteAudioSubscribed;
}

export function callAudioSessionConfiguration(callType: "audio" | "video") {
  void callType;
  return {
    audioCategory: "playAndRecord" as const,
    // Preserve the existing working audio-call path while making video calls use
    // the same call-compatible session setup before camera publication.
    audioMode: "videoChat" as const,
    audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"] as Array<
      "allowBluetooth" | "allowBluetoothA2DP" | "allowAirPlay" | "defaultToSpeaker"
    >
  };
}

export function nativeAudioOutput(enabled: boolean) {
  return Platform.OS === "ios" ? (enabled ? "force_speaker" : "default") : (enabled ? "speaker" : "earpiece");
}
