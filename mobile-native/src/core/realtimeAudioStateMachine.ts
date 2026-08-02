export type RealtimeRoomState =
  | "idle"
  | "authorizing"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnecting"
  | "disconnected"
  | "failed";

export type RealtimeLocalAudioState =
  | "idle"
  | "requestingPermission"
  | "acquiringSession"
  | "creatingTrack"
  | "publishing"
  | "published"
  | "muted"
  | "recovering"
  | "unpublishing"
  | "released"
  | "failed";

export type RealtimeRemoteAudioState =
  | "waiting"
  | "publicationAvailable"
  | "subscribing"
  | "subscribed"
  | "playing"
  | "interrupted"
  | "recovering"
  | "ended"
  | "failed";

export type RealtimeAudioStateSnapshot = {
  room: RealtimeRoomState;
  local: RealtimeLocalAudioState;
  remote: RealtimeRemoteAudioState;
  terminal: boolean;
};

export type RealtimeAudioStateDomain = "room" | "local" | "remote";

const ROOM_TRANSITIONS: Record<RealtimeRoomState, RealtimeRoomState[]> = {
  idle: ["authorizing", "connecting", "disconnected", "failed"],
  authorizing: ["connecting", "disconnected", "failed"],
  connecting: ["connected", "reconnecting", "disconnecting", "disconnected", "failed"],
  connected: ["reconnecting", "disconnecting", "disconnected", "failed"],
  reconnecting: ["connected", "disconnecting", "disconnected", "failed"],
  disconnecting: ["disconnected", "failed"],
  disconnected: [],
  failed: ["disconnecting", "disconnected"]
};

const LOCAL_TRANSITIONS: Record<RealtimeLocalAudioState, RealtimeLocalAudioState[]> = {
  idle: ["requestingPermission", "acquiringSession", "released", "failed"],
  requestingPermission: ["acquiringSession", "released", "failed"],
  acquiringSession: ["creatingTrack", "publishing", "released", "failed"],
  creatingTrack: ["publishing", "released", "failed"],
  publishing: ["published", "recovering", "unpublishing", "released", "failed"],
  published: ["muted", "recovering", "unpublishing", "released", "failed"],
  muted: ["publishing", "published", "recovering", "unpublishing", "released", "failed"],
  recovering: ["publishing", "published", "muted", "unpublishing", "released", "failed"],
  unpublishing: ["released", "failed"],
  released: [],
  failed: ["unpublishing", "released"]
};

const REMOTE_TRANSITIONS: Record<RealtimeRemoteAudioState, RealtimeRemoteAudioState[]> = {
  waiting: ["publicationAvailable", "subscribing", "ended", "failed"],
  publicationAvailable: ["subscribing", "subscribed", "ended", "failed"],
  subscribing: ["subscribed", "recovering", "ended", "failed"],
  subscribed: ["playing", "interrupted", "recovering", "ended", "failed"],
  playing: ["interrupted", "recovering", "ended", "failed"],
  interrupted: ["recovering", "playing", "ended", "failed"],
  recovering: ["subscribed", "playing", "ended", "failed"],
  ended: [],
  failed: ["recovering", "ended"]
};

export class RealtimeAudioTransitionError extends Error {
  constructor(domain: RealtimeAudioStateDomain, from: string, to: string) {
    super(`Invalid realtime audio ${domain} transition: ${from} -> ${to}`);
    this.name = "RealtimeAudioTransitionError";
  }
}

export class RealtimeAudioStateMachine {
  private snapshot: RealtimeAudioStateSnapshot = { room: "idle", local: "idle", remote: "waiting", terminal: false };

  getState(): RealtimeAudioStateSnapshot {
    return { ...this.snapshot };
  }

  transition(domain: RealtimeAudioStateDomain, next: RealtimeRoomState | RealtimeLocalAudioState | RealtimeRemoteAudioState): RealtimeAudioStateSnapshot {
    const current = this.snapshot[domain] as string;
    if (current === next) return this.getState();
    const allowed = domain === "room" ? ROOM_TRANSITIONS : domain === "local" ? LOCAL_TRANSITIONS : REMOTE_TRANSITIONS;
    if (!(allowed as Record<string, string[]>)[current]?.includes(next)) {
      throw new RealtimeAudioTransitionError(domain, current, next);
    }
    this.snapshot = { ...this.snapshot, [domain]: next } as RealtimeAudioStateSnapshot;
    return this.getState();
  }

  tryTransition(domain: RealtimeAudioStateDomain, next: RealtimeRoomState | RealtimeLocalAudioState | RealtimeRemoteAudioState): boolean {
    try {
      this.transition(domain, next);
      return true;
    } catch (error) {
      if (error instanceof RealtimeAudioTransitionError) return false;
      throw error;
    }
  }

  markTerminal(): RealtimeAudioStateSnapshot {
    this.snapshot = { ...this.snapshot, terminal: true };
    return this.getState();
  }

  mayReconnect(): boolean {
    return !this.snapshot.terminal && ["connected", "reconnecting"].includes(this.snapshot.room);
  }
}
