export type LiveRuntimeState =
  | "idle" | "preparing" | "authorizing" | "authorized" | "acquiringMedia"
  | "connecting" | "publishing" | "live" | "reconnecting" | "ending" | "ended" | "failed";
export type LiveAudioState = "idle" | "acquiring" | "active" | "creatingTrack" | "publishing" | "published" | "muted" | "recovering" | "stopping" | "released" | "failed";
export type LiveCameraState = "idle" | "acquiring" | "active" | "creatingTrack" | "publishing" | "published" | "switching" | "recovering" | "stopping" | "released" | "failed";
export type LiveRoomState = "idle" | "creating" | "connecting" | "connected" | "reconnecting" | "disconnecting" | "disconnected" | "failed";

export type LiveRuntimeErrorCode =
  | "LIVE_AUTHORIZATION_FAILED" | "LIVE_ROOM_CONNECTION_FAILED" | "LIVE_AUDIO_OWNERSHIP_LOST"
  | "LIVE_AUDIO_PUBLICATION_FAILED" | "LIVE_CAMERA_ACQUISITION_FAILED" | "LIVE_CAMERA_PUBLICATION_FAILED"
  | "LIVE_READINESS_TIMEOUT" | "LIVE_STALE_GENERATION" | "LIVE_INVALID_TRANSITION"
  | "LIVE_GUEST_AUTHORIZATION_FAILED" | "LIVE_RECOVERY_EXHAUSTED";

export class LiveRuntimeError extends Error {
  constructor(public readonly code: LiveRuntimeErrorCode, message: string, public readonly recoverable = false) {
    super(message);
    this.name = "LiveRuntimeError";
  }
}

export type LiveSessionIdentity = Readonly<{
  sessionId: string;
  broadcastId: number;
  roomName: string;
  hostUserId: number;
  generation: number;
  authorizationVersion: string;
  featureFlags: Readonly<Record<string, boolean | string | number>>;
  qualityProfile: "stable" | "balanced" | "elite" | "resilient";
  createdAt: string;
}>;

export type LiveRuntimeSnapshot = Readonly<{
  session: LiveSessionIdentity | null;
  state: LiveRuntimeState;
  audio: LiveAudioState;
  camera: LiveCameraState;
  room: LiveRoomState;
  authorized: boolean;
  audioOwnerActive: boolean;
  microphoneTrackCreated: boolean;
  microphonePublished: boolean;
  cameraOwnerActive: boolean;
  cameraTrackCreated: boolean;
  cameraPublished: boolean;
  competingPathActive: boolean;
  terminal: boolean;
  ready: boolean;
  error: LiveRuntimeErrorCode | null;
}>;

export type LiveRuntimeEvent = {
  event: string;
  timestamp: string;
  correlationId: string;
  sessionId: string;
  broadcastId: number;
  generation: number;
  roomName: string;
  role: "host" | "approved_guest" | "viewer";
  state: LiveRuntimeState;
  audioState: LiveAudioState;
  cameraState: LiveCameraState;
  roomState: LiveRoomState;
  qualityProfile: string;
  featureFlags: string;
  caller: string;
  reason: string;
  errorCategory: string;
};

const TRANSITIONS: Record<LiveRuntimeState, readonly LiveRuntimeState[]> = {
  idle: ["preparing"], preparing: ["authorizing", "ending", "failed"],
  authorizing: ["authorized", "ending", "failed"], authorized: ["acquiringMedia", "ending", "failed"],
  acquiringMedia: ["connecting", "ending", "failed"], connecting: ["publishing", "ending", "failed"],
  publishing: ["live", "ending", "failed"], live: ["reconnecting", "ending", "failed"],
  reconnecting: ["live", "ending", "failed"], ending: ["ended", "failed"], ended: ["preparing"], failed: ["ending", "preparing"]
};

let nextGeneration = 0;

export class LiveRuntime {
  private snapshot: LiveRuntimeSnapshot = this.empty();
  private startPromise: Promise<unknown> | null = null;
  private cleanupPromise: Promise<boolean> | null = null;
  private events: LiveRuntimeEvent[] = [];
  private resources: { room?: unknown; audioLease?: unknown; cameraOwner?: unknown } = {};

  private empty(): LiveRuntimeSnapshot {
    return { session: null, state: "idle", audio: "idle", camera: "idle", room: "idle", authorized: false,
      audioOwnerActive: false, microphoneTrackCreated: false, microphonePublished: false, cameraOwnerActive: false,
      cameraTrackCreated: false, cameraPublished: false, competingPathActive: false, terminal: false, ready: false, error: null };
  }

  getSnapshot(): LiveRuntimeSnapshot { return { ...this.snapshot }; }
  getEvents(): LiveRuntimeEvent[] { return this.events.map((event) => ({ ...event })); }
  getResources() { return { ...this.resources }; }
  attachResources(resources: { room?: unknown; audioLease?: unknown; cameraOwner?: unknown }) { this.resources = { ...this.resources, ...resources }; }

  createSession(input: Omit<LiveSessionIdentity, "generation" | "createdAt">): LiveSessionIdentity {
    nextGeneration += 1;
    const session = Object.freeze({ ...input, featureFlags: Object.freeze({ ...input.featureFlags }), generation: nextGeneration, createdAt: new Date().toISOString() });
    this.snapshot = { ...this.empty(), session, state: "preparing" };
    this.events = [];
    this.emit("session_created", "LiveRuntime.createSession", "start");
    return session;
  }

  transition(next: LiveRuntimeState, caller: string, reason: string): LiveRuntimeSnapshot {
    const current = this.snapshot.state;
    if (current === next) return this.getSnapshot();
    if (!TRANSITIONS[current].includes(next)) {
      this.emit("invalid_transition_rejected", caller, `${current}->${next}`, "LIVE_INVALID_TRANSITION");
      throw new LiveRuntimeError("LIVE_INVALID_TRANSITION", `Invalid Live transition: ${current} -> ${next}`);
    }
    this.snapshot = { ...this.snapshot, state: next, terminal: ["ending", "ended", "failed"].includes(next) };
    this.emit(`state_${next}`, caller, reason);
    return this.getSnapshot();
  }

  update(patch: Partial<LiveRuntimeSnapshot>, event: string, caller: string, reason: string): LiveRuntimeSnapshot {
    if (!this.snapshot.session) throw new LiveRuntimeError("LIVE_STALE_GENERATION", "No current Live session.");
    this.snapshot = { ...this.snapshot, ...patch, session: this.snapshot.session };
    this.snapshot = { ...this.snapshot, ready: this.isReady(this.snapshot) };
    this.emit(event, caller, reason);
    return this.getSnapshot();
  }

  private isReady(s: LiveRuntimeSnapshot): boolean {
    return Boolean(s.authorized && s.room === "connected" && s.audioOwnerActive && s.microphoneTrackCreated &&
      s.microphonePublished && s.cameraOwnerActive && s.cameraTrackCreated && s.cameraPublished &&
      !s.terminal && !s.competingPathActive && s.session);
  }

  assertReady(caller = "LiveRuntime.assertReady"): LiveRuntimeSnapshot {
    if (!this.snapshot.ready) throw new LiveRuntimeError("LIVE_READINESS_TIMEOUT", "Required Live media is not ready.", true);
    if (this.snapshot.state === "publishing") this.transition("live", caller, "readiness_passed");
    this.emit("readiness_passed", caller, "all_required_publications_confirmed");
    this.emit("broadcast_live", caller, "ready");
    return this.getSnapshot();
  }

  runStart<T>(command: () => Promise<T>): Promise<T> {
    if (this.startPromise) return this.startPromise as Promise<T>;
    this.startPromise = command().finally(() => { this.startPromise = null; });
    return this.startPromise as Promise<T>;
  }

  cleanup(generation: number, command: () => Promise<void>, reason: string): Promise<boolean> {
    if (!this.snapshot.session || this.snapshot.session.generation !== generation) {
      this.emit("stale_cleanup_rejected", "LiveRuntime.cleanup", reason, "LIVE_STALE_GENERATION");
      return Promise.resolve(false);
    }
    if (this.cleanupPromise) return this.cleanupPromise;
    this.cleanupPromise = (async () => {
      if (!this.snapshot.terminal) this.transition("ending", "LiveRuntime.cleanup", reason);
      this.emit("cleanup_started", "LiveRuntime.cleanup", reason);
      await command();
      this.resources = {};
      this.snapshot = { ...this.snapshot, audio: "released", camera: "released", room: "disconnected", state: "ended", terminal: true, ready: false };
      this.emit("cleanup_completed", "LiveRuntime.cleanup", reason);
      return true;
    })().finally(() => { this.cleanupPromise = null; });
    return this.cleanupPromise;
  }

  private emit(event: string, caller: string, reason: string, errorCategory = "none") {
    const session = this.snapshot.session;
    this.events.push({ event, timestamp: new Date().toISOString(), correlationId: session?.sessionId || "none",
      sessionId: session?.sessionId || "none", broadcastId: session?.broadcastId || 0, generation: session?.generation || 0,
      roomName: session?.roomName || "none", role: "host", state: this.snapshot.state, audioState: this.snapshot.audio,
      cameraState: this.snapshot.camera, roomState: this.snapshot.room, qualityProfile: session?.qualityProfile || "stable",
      featureFlags: JSON.stringify(session?.featureFlags || {}), caller, reason, errorCategory });
  }
}

const sharedLiveRuntime = new LiveRuntime();
export function getLiveRuntime(): LiveRuntime { return sharedLiveRuntime; }
