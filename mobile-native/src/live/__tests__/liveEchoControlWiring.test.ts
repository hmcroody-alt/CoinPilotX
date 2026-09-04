/**
 * Stage 17 — the transition rule the hook applies, tested as a transition.
 *
 * `resolveLiveEchoControl` answers "what should the settings be". This suite
 * covers the narrower question `useAgoraLiveBroadcastRoom` actually asks:
 * whether to call the SDK at all. That distinction is the whole point — on a
 * busy Live the roster changes constantly, and re-applying an audio scenario
 * mid-broadcast is audible, so the common case must resolve to "do nothing".
 *
 * Why this is tested here rather than through the hook: the hook resolves the
 * Agora SDK with `await import(...)`, which Jest cannot evaluate under this
 * project's configuration (dynamic import is not transpiled, and the runner is
 * not started with --experimental-vm-modules). The hook's own connect path is
 * therefore covered by the device gates, not by unit tests, and the decision it
 * makes is extracted to a pure function so the decision at least is provable.
 */
import { nextEchoScenario, resolveLiveAudioPlan } from "../liveAudioMatrix";

const hostPlan = resolveLiveAudioPlan("host", true);
const guestPlan = resolveLiveAudioPlan("guest", true);
const listenerPlan = resolveLiveAudioPlan("audience", true);
const unauthorizedPlan = resolveLiveAudioPlan("guest", false);

describe("when the scenario should change", () => {
  it("does nothing for a solo host, leaving single-host Live configured as it always was", () => {
    // The Stage 40/41 backward-compatibility promise, as a test.
    expect(nextEchoScenario("default", hostPlan, 1)).toBeNull();
  });

  it("moves to chatroom at the first guest, not the second", () => {
    expect(nextEchoScenario("default", hostPlan, 2)).toBe("chatroom");
  });

  it("does not reapply chatroom however much the roster churns", () => {
    for (const count of [2, 3, 4, 5, 6, 12]) {
      expect(nextEchoScenario("chatroom", hostPlan, count)).toBeNull();
    }
  });

  it("returns to the default scenario when the stage empties back to a solo host", () => {
    expect(nextEchoScenario("chatroom", hostPlan, 1)).toBe("default");
    expect(nextEchoScenario("default", hostPlan, 1)).toBeNull();
  });

  it("asks for exactly one SDK call across a full join-and-leave cycle", () => {
    // A stage filling to six and emptying again should touch the scenario twice:
    // once in, once out. Any more than that is an audible glitch per guest.
    let applied: "default" | "chatroom" = "default";
    const calls: string[] = [];
    for (const count of [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]) {
      const next = nextEchoScenario(applied, hostPlan, count);
      if (next) {
        calls.push(next);
        applied = next;
      }
    }
    expect(calls).toEqual(["chatroom", "default"]);
    expect(applied).toBe("default");
  });
});

describe("who the rule applies to", () => {
  it("treats a guest publisher exactly like the host, because echo is symmetric", () => {
    for (const count of [1, 2, 6]) {
      expect(nextEchoScenario("default", guestPlan, count)).toEqual(nextEchoScenario("default", hostPlan, count));
    }
  });

  it("never moves a listener's scenario, because there is nothing to cancel", () => {
    for (const count of [0, 1, 2, 6, 100]) {
      expect(nextEchoScenario("default", listenerPlan, count)).toBeNull();
      expect(nextEchoScenario("chatroom", listenerPlan, count)).toBeNull();
    }
  });

  it("never moves the scenario for a guest the server has not authorized", () => {
    // An unauthorized guest is an audience member. If this returned a scenario,
    // a client that had not been promoted would be configuring capture-side
    // audio processing for a capture it is not allowed to have.
    expect(nextEchoScenario("default", unauthorizedPlan, 6)).toBeNull();
  });
});

describe("counts that are not counts", () => {
  it("survives a roster count that arrives as nonsense", () => {
    for (const count of [0, -4, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(() => nextEchoScenario("default", hostPlan, count as number)).not.toThrow();
    }
    // Zero and negative both mean "not a crowd", so they must not engage chatroom.
    expect(nextEchoScenario("default", hostPlan, 0)).toBeNull();
    expect(nextEchoScenario("default", hostPlan, -4)).toBeNull();
  });

  it("treats a fractional count as the number of whole people it represents", () => {
    expect(nextEchoScenario("default", hostPlan, 1.9)).toBeNull();
    expect(nextEchoScenario("default", hostPlan, 2.4)).toBe("chatroom");
  });
});
