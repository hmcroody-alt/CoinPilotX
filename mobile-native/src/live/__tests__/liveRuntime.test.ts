import { LiveRuntime, LiveRuntimeError } from "../liveRuntime";

function session(runtime: LiveRuntime) {
  return runtime.createSession({
    sessionId: "session-a", broadcastId: 42, roomName: "room-42", hostUserId: 7,
    authorizationVersion: "auth-v1", featureFlags: { audioV2: false, qualityV2: false }, qualityProfile: "stable"
  });
}

function advanceToPublishing(runtime: LiveRuntime) {
  session(runtime);
  runtime.transition("authorizing", "test", "authorize");
  runtime.update({ authorized: true }, "authorization_succeeded", "test", "authorized");
  runtime.transition("authorized", "test", "authorized");
  runtime.transition("acquiringMedia", "test", "media");
  runtime.update({ audio: "active", audioOwnerActive: true, camera: "active", cameraOwnerActive: true }, "ownership_acquired", "test", "owners");
  runtime.transition("connecting", "test", "connect");
  runtime.update({ room: "connected" }, "room_connected", "test", "connected");
  runtime.transition("publishing", "test", "publish");
}

describe("authoritative Live runtime", () => {
  it("snapshots canonical identity and flags for a generation", () => {
    const runtime = new LiveRuntime();
    const created = session(runtime);
    expect(created.roomName).toBe("room-42");
    expect(created.generation).toBeGreaterThan(0);
    expect(Object.isFrozen(created.featureFlags)).toBe(true);
  });

  it("rejects every invalid transition and records it", () => {
    const runtime = new LiveRuntime();
    session(runtime);
    expect(() => runtime.transition("live", "test", "skip")).toThrow(LiveRuntimeError);
    expect(runtime.getEvents().at(-1)?.event).toBe("invalid_transition_rejected");
  });

  it("enters live only from event-derived readiness", () => {
    const runtime = new LiveRuntime();
    advanceToPublishing(runtime);
    expect(() => runtime.assertReady()).toThrow("not ready");
    runtime.update({ microphoneTrackCreated: true, microphonePublished: true, cameraTrackCreated: true, cameraPublished: true }, "publications_confirmed", "test", "confirmed");
    expect(runtime.assertReady().state).toBe("live");
  });

  it("deduplicates concurrent start commands", async () => {
    const runtime = new LiveRuntime();
    let calls = 0;
    const command = () => runtime.runStart(async () => { calls += 1; session(runtime); return runtime.getSnapshot(); });
    await Promise.all([command(), command(), command()]);
    expect(calls).toBe(1);
  });

  it("rejects stale cleanup and preserves current resources", async () => {
    const runtime = new LiveRuntime();
    const first = session(runtime);
    const current = session(runtime);
    const room = {};
    runtime.attachResources({ room });
    expect(await runtime.cleanup(first.generation, async () => undefined, "old_unmount")).toBe(false);
    expect(runtime.getSnapshot().session?.generation).toBe(current.generation);
    expect(runtime.getResources().room).toBe(room);
    expect(runtime.getEvents().at(-1)?.event).toBe("stale_cleanup_rejected");
  });

  it("makes current cleanup idempotent", async () => {
    const runtime = new LiveRuntime();
    const current = session(runtime);
    let cleanups = 0;
    const cleanup = () => runtime.cleanup(current.generation, async () => { cleanups += 1; }, "host_end");
    await Promise.all([cleanup(), cleanup()]);
    expect(cleanups).toBe(1);
    expect(runtime.getSnapshot().state).toBe("ended");
  });

  it("keeps the session alive across a UI remount", () => {
    const runtime = new LiveRuntime();
    advanceToPublishing(runtime);
    runtime.update({ microphoneTrackCreated: true, microphonePublished: true, cameraTrackCreated: true, cameraPublished: true }, "publications_confirmed", "test", "confirmed");
    runtime.assertReady();
    const room = {};
    runtime.attachResources({ room });
    expect(runtime.getSnapshot().state).toBe("live");
    expect(runtime.getResources().room).toBe(room);
  });
});
