import { MAX_RADIO_RECOVERY_ATTEMPTS, radioRecoveryPlan, shouldResumeRadio } from "../radioRecovery";

function plan(overrides: Partial<Parameters<typeof radioRecoveryPlan>[0]> = {}) {
  return radioRecoveryPlan({
    reason: "load_failed",
    connectivity: "online",
    attempt: 0,
    positionMillis: 92_000,
    fromCache: false,
    ...overrides
  });
}

describe("radioRecoveryPlan", () => {
  it("never loses the listener's place, whatever it decides", () => {
    // The §84 failure was not that the radio stopped; it was that coming back
    // restarted the song. Every branch has to carry the position.
    const outcomes = [
      plan({ connectivity: "offline" }),
      plan({ connectivity: "online" }),
      plan({ attempt: MAX_RADIO_RECOVERY_ATTEMPTS }),
      plan({ fromCache: true })
    ];
    for (const outcome of outcomes) expect(outcome.resumeAtMillis).toBe(92_000);
  });

  it("waits for the network instead of retrying into a dead radio", () => {
    const result = plan({ connectivity: "offline" });
    expect(result.action).toBe("await_network");
    expect(result.status).toBe("offline");
    // Retrying here would burn the retry budget and the battery for nothing,
    // and would then report a real failure caused only by the retries.
    expect(result.delayMs).toBe(0);
  });

  it("retries a live network with growing backoff", () => {
    const first = plan({ attempt: 0 });
    const second = plan({ attempt: 1 });
    expect(first.action).toBe("retry");
    expect(second.delayMs).toBeGreaterThan(first.delayMs);
    // The track has not ended and nobody pressed stop. From the listener's side
    // this is a gap in the audio, which is what buffering is.
    expect(first.status).toBe("buffering");
  });

  it("treats a recovering network as a retry, not as an immediate resume", () => {
    // RECOVERING means the authority has seen the network return but has not
    // confirmed it. Firing instantly into a half-open connection spends an
    // attempt on a request that was never going to complete.
    const result = plan({ connectivity: "recovering" });
    expect(result.action).toBe("retry");
    expect(result.delayMs).toBeGreaterThan(0);
  });

  it("gives up once the attempts are spent", () => {
    const result = plan({ attempt: MAX_RADIO_RECOVERY_ATTEMPTS });
    expect(result.action).toBe("give_up");
    expect(result.status).toBe("error");
  });

  it("does not blame the network for a cached file that failed to open", () => {
    // A file on disk does not stop being readable because a tunnel arrived.
    // Waiting for connectivity would be a lie dressed as patience, and the same
    // broken bytes would be replayed forever.
    const result = plan({ fromCache: true, connectivity: "offline" });
    expect(result.action).toBe("give_up");
    expect(result.discardCachedCopy).toBe(true);
  });

  it("does not throw away the cached copy when the network was at fault", () => {
    expect(plan({ connectivity: "offline" }).discardCachedCopy).toBe(false);
    expect(plan({ attempt: MAX_RADIO_RECOVERY_ATTEMPTS }).discardCachedCopy).toBe(false);
  });
});

describe("shouldResumeRadio", () => {
  it("resumes only what it was actually asked to resume", () => {
    expect(shouldResumeRadio({ userWantsPlayback: true, connectivity: "online", awaitingNetwork: true })).toBe(true);
    // Nothing is owed: the radio was not waiting on the network.
    expect(shouldResumeRadio({ userWantsPlayback: true, connectivity: "online", awaitingNetwork: false })).toBe(false);
  });

  it("never overrides an explicit pause", () => {
    // Otherwise reconnecting starts music in someone's pocket.
    expect(shouldResumeRadio({ userWantsPlayback: false, connectivity: "online", awaitingNetwork: true })).toBe(false);
  });

  it("waits for the network to actually be back", () => {
    expect(shouldResumeRadio({ userWantsPlayback: true, connectivity: "offline", awaitingNetwork: true })).toBe(false);
    // DEGRADED is slow, not absent. A radio that refuses to play on a weak
    // connection is worse than one that buffers.
    expect(shouldResumeRadio({ userWantsPlayback: true, connectivity: "degraded", awaitingNetwork: true })).toBe(true);
  });
});
