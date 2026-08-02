import { RealtimeAudioStateMachine, RealtimeAudioTransitionError } from "../realtimeAudioStateMachine";

describe("RealtimeAudioStateMachine", () => {
  it("tracks the reviewed room, local publication, and remote playback lifecycle", () => {
    const machine = new RealtimeAudioStateMachine();
    machine.transition("room", "authorizing");
    machine.transition("room", "connecting");
    machine.transition("local", "acquiringSession");
    machine.transition("local", "publishing");
    machine.transition("local", "published");
    machine.transition("room", "connected");
    machine.transition("remote", "publicationAvailable");
    machine.transition("remote", "subscribing");
    machine.transition("remote", "subscribed");
    machine.transition("remote", "playing");

    expect(machine.getState()).toEqual({
      room: "connected",
      local: "published",
      remote: "playing",
      terminal: false
    });
  });

  it("rejects impossible transitions instead of silently reporting a false media state", () => {
    const machine = new RealtimeAudioStateMachine();
    expect(() => machine.transition("room", "connected")).toThrow(RealtimeAudioTransitionError);
    expect(() => machine.transition("local", "published")).toThrow(RealtimeAudioTransitionError);
    expect(() => machine.transition("remote", "playing")).toThrow(RealtimeAudioTransitionError);
  });

  it("prevents a terminal session from becoming reconnectable", () => {
    const machine = new RealtimeAudioStateMachine();
    machine.transition("room", "connecting");
    machine.transition("room", "connected");
    expect(machine.mayReconnect()).toBe(true);
    machine.markTerminal();
    expect(machine.mayReconnect()).toBe(false);
  });

  it("makes same-state lifecycle notifications idempotent", () => {
    const machine = new RealtimeAudioStateMachine();
    machine.transition("room", "connecting");
    expect(machine.transition("room", "connecting").room).toBe("connecting");
  });
});
