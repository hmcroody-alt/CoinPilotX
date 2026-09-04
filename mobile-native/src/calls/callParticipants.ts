/**
 * Canonical call participant registry.
 *
 * Single source of participant identity for every call surface. The backend
 * `participants[]` list is authoritative for WHO is on a call; Agora's
 * `remoteUids[]` is authoritative for WHOSE MEDIA is currently in the room.
 * Because the backend maps `rtc_uid == user_id` (`_agora_uid` is the identity
 * mapping), the two lists join directly on the user id — no lookup table, and
 * no UI is ever allowed to infer identity on its own.
 *
 * This module also owns the transient per-participant media state that only
 * Agora knows (active speaker, remote audio/video availability). The session
 * store feeds those in from engine events; UIs read the merged view.
 *
 * NOT a protected realtime-audio path: this file performs no audio-session,
 * engine, or track work of any kind. It is a pure derivation + tiny substore.
 */

import { useSyncExternalStore } from "react";
import type { PulseCall, PulseCallParticipant } from "../api/calls";
import { useCallSession } from "./callSessionStore";

export type CallParticipantView = {
  userId: number;
  /** Agora uid — identical to userId by backend contract (rtc_uid == user_id). */
  rtcUid: number;
  displayName: string;
  username: string;
  avatarUrl: string;
  role: string;
  /** Backend participant status: ringing | joined | left | declined | missed. */
  backendStatus: string;
  isLocal: boolean;
  /** True when this participant's media is currently present in the Agora room. */
  rtcConnected: boolean;
  speaking: boolean;
  audioMuted: boolean;
  videoMuted: boolean;
  joinedAt?: string;
};

/* ------------------------------------------------------------------ */
/* Transient per-uid media state, fed by callSessionStore engine hooks. */
/* ------------------------------------------------------------------ */

type MediaStateSnapshot = {
  speakingUids: number[];
  audioMutedUids: number[];
  videoMutedUids: number[];
};

let mediaState: MediaStateSnapshot = { speakingUids: [], audioMutedUids: [], videoMutedUids: [] };
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getParticipantMediaState(): MediaStateSnapshot {
  return mediaState;
}

export function subscribeParticipantMediaState(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Replace the active-speaker set (from onAudioVolumeIndication). */
export function setSpeakingUids(uids: number[]) {
  const next = dedupeUids(uids);
  if (sameUids(next, mediaState.speakingUids)) return;
  mediaState = { ...mediaState, speakingUids: next };
  emit();
}

/** Per-uid remote audio mute display state (from onRemoteAudioStateChanged). */
export function setRemoteAudioMuted(uid: number, muted: boolean) {
  const next = toggleUid(mediaState.audioMutedUids, uid, muted);
  if (next === mediaState.audioMutedUids) return;
  mediaState = { ...mediaState, audioMutedUids: next };
  emit();
}

/** Per-uid remote video off display state (from onRemoteVideoStateChanged). */
export function setRemoteVideoMuted(uid: number, muted: boolean) {
  const next = toggleUid(mediaState.videoMutedUids, uid, muted);
  if (next === mediaState.videoMutedUids) return;
  mediaState = { ...mediaState, videoMutedUids: next };
  emit();
}

/** Clear all transient media state — call on session teardown. */
export function resetParticipantMediaState() {
  if (
    !mediaState.speakingUids.length &&
    !mediaState.audioMutedUids.length &&
    !mediaState.videoMutedUids.length
  ) {
    return;
  }
  mediaState = { speakingUids: [], audioMutedUids: [], videoMutedUids: [] };
  emit();
}

/* ------------------------------------------------------------------ */
/* Pure derivation: backend participants ⋈ remoteUids (uid == user_id). */
/* ------------------------------------------------------------------ */

export function buildCallParticipants(
  call: PulseCall | null | undefined,
  remoteUids: number[],
  localUid: number,
  media: MediaStateSnapshot = mediaState
): CallParticipantView[] {
  const backend = (call?.participants || []).filter((p) => participantUserId(p) > 0);
  const rtcSet = new Set((remoteUids || []).map((uid) => Number(uid) || 0).filter((uid) => uid > 0));
  const speaking = new Set(media.speakingUids);
  const audioMuted = new Set(media.audioMutedUids);
  const videoMuted = new Set(media.videoMutedUids);
  const seen = new Set<number>();

  const views: CallParticipantView[] = backend.map((p) => {
    const userId = participantUserId(p);
    seen.add(userId);
    const isLocal = localUid > 0 && userId === localUid;
    return {
      userId,
      rtcUid: userId,
      displayName: String(p.display_name || p.username || `User ${userId}`),
      username: String(p.username || ""),
      avatarUrl: String(p.avatar_url || ""),
      role: String(p.role || "callee"),
      backendStatus: String(p.status || ""),
      isLocal,
      rtcConnected: isLocal || rtcSet.has(userId),
      speaking: speaking.has(userId),
      audioMuted: isLocal ? false : p.muted_audio === true || audioMuted.has(userId),
      videoMuted: isLocal ? false : p.muted_video === true || videoMuted.has(userId),
      joinedAt: p.joined_at
    };
  });

  // Media present for a uid the backend hasn't reported yet (poll lag): show it
  // as a provisional participant rather than dropping real audio on the floor.
  rtcSet.forEach((uid) => {
    if (seen.has(uid) || (localUid > 0 && uid === localUid)) return;
    views.push({
      userId: uid,
      rtcUid: uid,
      displayName: `User ${uid}`,
      username: "",
      avatarUrl: "",
      role: "callee",
      backendStatus: "joined",
      isLocal: false,
      rtcConnected: true,
      speaking: speaking.has(uid),
      audioMuted: audioMuted.has(uid),
      videoMuted: videoMuted.has(uid),
      joinedAt: undefined
    });
  });

  return views;
}

/** Participants whose media should occupy a tile (local + connected remotes). */
export function connectedParticipants(views: CallParticipantView[]): CallParticipantView[] {
  return views.filter((view) => view.rtcConnected && !isTerminalParticipantStatus(view.backendStatus));
}

/** Invitees still ringing — rendered as pending chips, never as media tiles. */
export function ringingParticipants(views: CallParticipantView[]): CallParticipantView[] {
  return views.filter((view) => !view.isLocal && view.backendStatus === "ringing");
}

export function isTerminalParticipantStatus(status: string): boolean {
  return status === "left" || status === "declined" || status === "missed";
}

/* ------------------------------------------------------------------ */
/* React binding.                                                      */
/* ------------------------------------------------------------------ */

export function useParticipantMediaState(): MediaStateSnapshot {
  return useSyncExternalStore(
    subscribeParticipantMediaState,
    getParticipantMediaState,
    getParticipantMediaState
  );
}

/** Merged canonical view: backend registry + RTC presence + media state. */
export function useCallParticipants(): CallParticipantView[] {
  const session = useCallSession();
  const media = useParticipantMediaState();
  return buildCallParticipants(session.call, session.remoteUids, session.localUid, media);
}

/* ------------------------------------------------------------------ */
/* Helpers.                                                            */
/* ------------------------------------------------------------------ */

function participantUserId(p: PulseCallParticipant): number {
  return Number(p.user_id || p.participant_id || 0) || 0;
}

function dedupeUids(uids: number[]): number[] {
  return Array.from(new Set((uids || []).map((uid) => Number(uid) || 0).filter((uid) => uid > 0)));
}

function toggleUid(list: number[], uid: number, present: boolean): number[] {
  const id = Number(uid) || 0;
  if (id <= 0) return list;
  const has = list.includes(id);
  if (present && !has) return [...list, id];
  if (!present && has) return list.filter((entry) => entry !== id);
  return list;
}

function sameUids(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((uid) => set.has(uid));
}

export function __resetParticipantRegistryForTests() {
  mediaState = { speakingUids: [], audioMutedUids: [], videoMutedUids: [] };
}
