/**
 * The runtime invariant monitor, and the flag exclusivity it depends on.
 *
 * Two things are being proved here, and they are different in kind.
 *
 * The first is that the monitor observes without interfering. Every state it
 * watches for has already been rejected by the module that detected it, so the
 * monitor's only job is to make that rejection countable. A monitor that throws,
 * repairs, or changes a return value would be a second decision-maker for audio
 * state — the exact failure the boundary exists to prevent — so the tests below
 * pin the non-interference as tightly as they pin the detection.
 *
 * The second is that the stable and experimental livestream audio paths cannot
 * both be live. A kill switch that can resolve to "both" is not a kill switch.
 */
import {
  RealtimeAudioInvariantError,
  checkLeaseFreshness,
  checkMicrophoneOwnership,
  checkPublicationState,
  checkReconnectEligibility,
  checkRouteState,
  checkSessionOwnership,
  getRealtimeAudioInvariantReport,
  reportRealtimeAudioInvariant,
  resetRealtimeAudioInvariants,
  setRealtimeAudioInvariantPolicy
} from "../realtimeAudioInvariants";
import { setRealtimeAudioTelemetrySink } from "../realtimeAudioTelemetry";
import {
  LIVE_AUDIO_V2_FLAG_KEY,
  isLiveAudioV2Enabled,
  normalizeLiveAudioV2Flag,
  resolveLiveAudioPath
} from "../../live/liveAudioFlags";

describe("runtime audio invariants", () => {
  let events: any[];

  beforeEach(() => {
    events = [];
    resetRealtimeAudioInvariants();
    setRealtimeAudioTelemetrySink((event) => events.push(event));
  });

  afterEach(() => {
    setRealtimeAudioTelemetrySink(null);
    resetRealtimeAudioInvariants();
  });

  describe("detection", () => {
    it("flags two simultaneous microphone owners and nothing less", () => {
      expect(checkMicrophoneOwnership({ microphoneOwnerIds: [] })).toEqual([]);
      expect(checkMicrophoneOwnership({ microphoneOwnerIds: ["call-1"] })).toEqual([]);
      expect(checkMicrophoneOwnership({ microphoneOwnerIds: ["call-1", "live-1"] })).toEqual([
        "multiple_microphone_owners"
      ]);
    });

    it("flags duplicate tracks, duplicate publications, and viewer publication independently", () => {
      const clean = {
        localAudioTrackCount: 1,
        inFlightPublications: 1,
        isViewer: false,
        publishRequested: true
      };
      expect(checkPublicationState(clean)).toEqual([]);
      expect(checkPublicationState({ ...clean, localAudioTrackCount: 2 })).toEqual([
        "duplicate_microphone_tracks"
      ]);
      expect(checkPublicationState({ ...clean, inFlightPublications: 2 })).toEqual(["duplicate_publication"]);
      expect(checkPublicationState({ ...clean, isViewer: true })).toEqual(["viewer_publication_attempt"]);
      // A viewer that never asked to publish is the normal case and must stay
      // silent, or the signal drowns in every livestream view in the product.
      expect(checkPublicationState({ ...clean, isViewer: true, publishRequested: false })).toEqual([]);
    });

    it("flags an active session with no owner", () => {
      expect(checkSessionOwnership({ sessionActive: true, ownerId: "call-1" })).toEqual([]);
      expect(checkSessionOwnership({ sessionActive: false, ownerId: null })).toEqual([]);
      expect(checkSessionOwnership({ sessionActive: true, ownerId: null })).toEqual([
        "session_active_without_owner"
      ]);
    });

    it("flags a cleanup whose lease is older than the live one", () => {
      expect(checkLeaseFreshness({ requestedLeaseId: 4, activeLeaseId: 4 })).toEqual([]);
      // A newer lease releasing is not a violation; it is a race the lease check
      // resolves in the caller's favour.
      expect(checkLeaseFreshness({ requestedLeaseId: 5, activeLeaseId: 4 })).toEqual([]);
      expect(checkLeaseFreshness({ requestedLeaseId: 3, activeLeaseId: 4 })).toEqual([
        "stale_cleanup_of_newer_session"
      ]);
    });

    it("flags two different routes in flight at once", () => {
      expect(checkRouteState({ appliedRoute: "speaker", pendingRoute: "speaker" })).toEqual([]);
      expect(checkRouteState({ appliedRoute: "speaker", pendingRoute: null })).toEqual([]);
      expect(checkRouteState({ appliedRoute: "speaker", pendingRoute: "earpiece" })).toEqual([
        "conflicting_route_state"
      ]);
    });

    it("flags a reconnect attempt against a terminal room", () => {
      expect(checkReconnectEligibility({ terminal: false, reconnectRequested: true })).toEqual([]);
      expect(checkReconnectEligibility({ terminal: true, reconnectRequested: false })).toEqual([]);
      expect(checkReconnectEligibility({ terminal: true, reconnectRequested: true })).toEqual([
        "terminal_room_reconnect_attempt"
      ]);
    });

    it("covers all eight invariants named by the hard-lock", () => {
      const covered = new Set([
        ...checkMicrophoneOwnership({ microphoneOwnerIds: ["a", "b"] }),
        ...checkPublicationState({
          localAudioTrackCount: 2,
          inFlightPublications: 2,
          isViewer: true,
          publishRequested: true
        }),
        ...checkSessionOwnership({ sessionActive: true, ownerId: null }),
        ...checkLeaseFreshness({ requestedLeaseId: 1, activeLeaseId: 9 }),
        ...checkRouteState({ appliedRoute: "speaker", pendingRoute: "earpiece" }),
        ...checkReconnectEligibility({ terminal: true, reconnectRequested: true })
      ]);
      expect(covered.size).toBe(8);
    });
  });

  describe("reporting", () => {
    it("emits privacy-safe telemetry carrying the invariant id and the action taken", () => {
      reportRealtimeAudioInvariant({
        id: "viewer_publication_attempt",
        action: "rejected",
        detail: "publish_denied",
        sessionId: "live-session-abcdef123456",
        roomType: "live_viewer"
      });

      expect(events).toHaveLength(1);
      const event = events[0];
      expect(event.name).toBe("invariant_violation");
      expect(event.failureCategory).toBe("viewer_publication_attempt");
      expect(event.outcome).toBe("rejected");
      // The raw session id must never survive into the event.
      expect(JSON.stringify(event)).not.toContain("live-session-abcdef123456");
    });

    it("replaces a detail outside the fixed vocabulary instead of emitting it", () => {
      const violation = reportRealtimeAudioInvariant({
        id: "duplicate_microphone_tracks",
        action: "reconciled",
        // A room name, a URL, or a token must never reach telemetry through the
        // detail field, so anything not matching the vocabulary is discarded.
        detail: "wss://live.example.com/room/user-42?token=eyJhbGciOi"
      });
      expect(violation.detail).toBe("unspecified");
      expect(JSON.stringify(events[0])).not.toContain("example.com");
    });

    it("counts repeated violations and keeps the history bounded", () => {
      for (let index = 0; index < 50; index += 1) {
        reportRealtimeAudioInvariant({ id: "duplicate_publication", action: "rejected" });
      }
      const report = getRealtimeAudioInvariantReport();
      expect(report.counts.duplicate_publication).toBe(50);
      // A device in a reconnect loop can trip an invariant continuously. A
      // diagnostic that grows without bound during the incident it exists to
      // diagnose is worse than no diagnostic.
      expect(report.recent.length).toBeLessThanOrEqual(8);
    });

    it("does not throw for a state that was already rejected", () => {
      const alreadyHandled = [
        "multiple_microphone_owners",
        "duplicate_microphone_tracks",
        "duplicate_publication",
        "conflicting_route_state",
        "stale_cleanup_of_newer_session",
        "viewer_publication_attempt",
        "terminal_room_reconnect_attempt"
      ] as const;
      // A user in a live call must not lose the call because a diagnostic
      // disliked something.
      alreadyHandled.forEach((id) => {
        expect(() => reportRealtimeAudioInvariant({ id, action: "rejected" })).not.toThrow();
      });
    });

    it("escalates only ownership corruption, and only when a build opts in", () => {
      // Default: silent even for the one escalatable case, so production audio
      // can never be dropped by the monitor.
      expect(() =>
        reportRealtimeAudioInvariant({ id: "session_active_without_owner", action: "reported" })
      ).not.toThrow();

      setRealtimeAudioInvariantPolicy({ throwOnOwnershipCorruption: true });
      expect(() =>
        reportRealtimeAudioInvariant({ id: "session_active_without_owner", action: "reported" })
      ).toThrow(RealtimeAudioInvariantError);
      // Still only that one id, even with escalation enabled.
      expect(() =>
        reportRealtimeAudioInvariant({ id: "duplicate_publication", action: "rejected" })
      ).not.toThrow();
    });

    it("keeps recording when the telemetry sink itself fails", () => {
      setRealtimeAudioTelemetrySink(() => {
        throw new Error("sink offline");
      });
      expect(() =>
        reportRealtimeAudioInvariant({ id: "duplicate_publication", action: "rejected" })
      ).not.toThrow();
      expect(getRealtimeAudioInvariantReport().counts.duplicate_publication).toBe(1);
    });

    it("runs unconditionally rather than only under __DEV__", () => {
      const previous = (global as any).__DEV__;
      (global as any).__DEV__ = false;
      try {
        reportRealtimeAudioInvariant({ id: "duplicate_publication", action: "rejected" });
        // The states that matter appear in production builds, on a network CI
        // does not have. A debug-only assertion would never see them.
        expect(events).toHaveLength(1);
      } finally {
        (global as any).__DEV__ = previous;
      }
    });
  });
});

describe("livestream audio flag exclusivity", () => {
  const INPUTS = [
    undefined,
    null,
    {},
    { [LIVE_AUDIO_V2_FLAG_KEY]: true },
    { [LIVE_AUDIO_V2_FLAG_KEY]: false },
    { [LIVE_AUDIO_V2_FLAG_KEY]: "true" },
    { [LIVE_AUDIO_V2_FLAG_KEY]: 1 },
    { [LIVE_AUDIO_V2_FLAG_KEY]: "1" },
    { [LIVE_AUDIO_V2_FLAG_KEY]: "yes" },
    { [LIVE_AUDIO_V2_FLAG_KEY]: {} }
  ];

  it("resolves to exactly one path for every input shape", () => {
    INPUTS.forEach((source) => {
      const path = resolveLiveAudioPath(source as any);
      // The stable and experimental paths are mutually exclusive by
      // construction: a single resolver returns one name. There is no input
      // that yields both, and none that yields neither.
      expect(["legacy_fallback", "shared_governed"]).toContain(path);
      expect(path === "shared_governed").toBe(isLiveAudioV2Enabled(source as any));
    });
  });

  it("requires a strict boolean true, so a truthy string cannot enable the experimental path", () => {
    expect(normalizeLiveAudioV2Flag(true)).toBe(true);
    // "true", 1, and "1" are what an environment variable or a JSON round-trip
    // produces by accident. Accepting them would mean the kill switch could be
    // turned on by a serialization change rather than by a decision.
    ["true", "1", 1, "yes", {}, [], "false", 0, null, undefined].forEach((raw) => {
      expect(normalizeLiveAudioV2Flag(raw)).toBe(false);
    });
  });

  it("defaults to the stable path when the server says nothing", () => {
    // Absent flag means legacy. A rollout that fails to deliver the flag must
    // fall back to the path that was physically verified, not the new one.
    expect(resolveLiveAudioPath(undefined)).toBe("v1_legacy");
    expect(resolveLiveAudioPath(null)).toBe("v1_legacy");
    expect(resolveLiveAudioPath({} as any)).toBe("v1_legacy");
  });

  it("is server-driven with no local override reachable from the module", () => {
    const source = { [LIVE_AUDIO_V2_FLAG_KEY]: false } as any;
    expect(resolveLiveAudioPath(source)).toBe("v1_legacy");
    // The resolver is pure: the only input is the server payload. There is no
    // setter, no cached local preference, and no environment read, which is
    // what makes this a real remote kill switch.
    expect(resolveLiveAudioPath(source)).toBe("v1_legacy");
    expect(Object.keys(require("../../live/liveAudioFlags"))).not.toContain("setLiveAudioV2Enabled");
  });
});
