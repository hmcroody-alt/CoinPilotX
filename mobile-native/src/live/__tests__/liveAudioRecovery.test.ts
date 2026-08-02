import {
  classifyDisconnect,
  isTerminalDisconnect,
  LIVE_MAX_RECONNECT_ATTEMPTS,
  millisecondsUntilRefresh,
  nextReconnectDelayMs,
  parseTokenExpiry,
  shouldAttemptReconnect,
  shouldReapplyAudioRoute,
  shouldRefreshToken,
  shouldResumeAfterInterruption,
  TOKEN_REFRESH_MARGIN_MS
} from "../liveAudioRecovery";

describe("disconnect classification", () => {
  it("treats host-ended and removal as terminal", () => {
    for (const reason of ["room_closed", "participant_removed", "host_ended", "live_ended", "token_expired"]) {
      expect(classifyDisconnect(reason)).toBe("terminal");
    }
  });

  it("treats network-class drops as recoverable", () => {
    for (const reason of ["signal_close", "network_error", "connection_lost", "state_mismatch"]) {
      expect(classifyDisconnect(reason)).toBe("recoverable");
    }
  });

  it("normalises casing, spacing and hyphenation from different LiveKit versions", () => {
    expect(classifyDisconnect("ParticipantRemoved")).toBe("terminal");
    expect(classifyDisconnect("participant removed")).toBe("terminal");
    expect(classifyDisconnect("participant-removed")).toBe("terminal");
    expect(classifyDisconnect("SIGNAL_CLOSE")).toBe("recoverable");
  });

  it("reports an unrecognised or empty reason as unknown rather than guessing", () => {
    expect(classifyDisconnect("")).toBe("unknown");
    expect(classifyDisconnect(null)).toBe("unknown");
    expect(classifyDisconnect("something_new_from_a_future_sdk")).toBe("unknown");
  });

  it("only reports true terminal reasons as terminal", () => {
    expect(isTerminalDisconnect("room_closed")).toBe(true);
    expect(isTerminalDisconnect("network_error")).toBe(false);
    expect(isTerminalDisconnect("unknown_future_reason")).toBe(false);
  });
});

describe("bounded reconnect backoff", () => {
  it("grows exponentially and caps", () => {
    expect(nextReconnectDelayMs(1)).toBe(500);
    expect(nextReconnectDelayMs(2)).toBe(1000);
    expect(nextReconnectDelayMs(3)).toBe(2000);
    expect(nextReconnectDelayMs(6)).toBe(15000);
  });

  it("stops offering a delay once the attempt budget is spent", () => {
    expect(nextReconnectDelayMs(LIVE_MAX_RECONNECT_ATTEMPTS)).not.toBeNull();
    expect(nextReconnectDelayMs(LIVE_MAX_RECONNECT_ATTEMPTS + 1)).toBeNull();
  });

  it("never reconnects after a terminal disconnect, no matter how few attempts were used", () => {
    expect(shouldAttemptReconnect("participant_removed", 1)).toBe(false);
    expect(shouldAttemptReconnect("room_closed", 1)).toBe(false);
  });

  it("reconnects a recoverable drop until the budget runs out", () => {
    expect(shouldAttemptReconnect("network_error", 1)).toBe(true);
    expect(shouldAttemptReconnect("network_error", LIVE_MAX_RECONNECT_ATTEMPTS)).toBe(true);
    expect(shouldAttemptReconnect("network_error", LIVE_MAX_RECONNECT_ATTEMPTS + 1)).toBe(false);
  });
});

describe("token expiry and refresh", () => {
  const now = Date.UTC(2026, 7, 1, 12, 0, 0);

  it("parses ISO strings, epoch seconds and epoch milliseconds", () => {
    expect(parseTokenExpiry("2026-08-01T12:00:00.000Z")).toBe(now);
    expect(parseTokenExpiry(now / 1000)).toBe(now);
    expect(parseTokenExpiry(now)).toBe(now);
  });

  it("refreshes once inside the margin", () => {
    const insideMargin = now + TOKEN_REFRESH_MARGIN_MS - 1000;
    expect(shouldRefreshToken(new Date(insideMargin).toISOString(), now)).toBe(true);
  });

  it("does not refresh while comfortably valid", () => {
    const wellAhead = now + TOKEN_REFRESH_MARGIN_MS + 60_000;
    expect(shouldRefreshToken(new Date(wellAhead).toISOString(), now)).toBe(false);
  });

  it("refreshes an already-expired token", () => {
    expect(shouldRefreshToken(new Date(now - 1000).toISOString(), now)).toBe(true);
  });

  it("fails safe by refreshing when the expiry is missing or unparseable", () => {
    expect(shouldRefreshToken(undefined, now)).toBe(true);
    expect(shouldRefreshToken("not-a-date", now)).toBe(true);
  });

  it("REGRESSION: a 30-minute guest token in a long broadcast schedules a refresh", () => {
    // Guest TTL is 1800s. Without refresh, a reconnect after minute 30 reuses an
    // expired token and the guest can never rejoin.
    const guestExpiry = now + 30 * 60 * 1000;
    const waitMs = millisecondsUntilRefresh(new Date(guestExpiry).toISOString(), now);

    expect(waitMs).toBeGreaterThan(0);
    expect(waitMs).toBe(30 * 60 * 1000 - TOKEN_REFRESH_MARGIN_MS);
    // And once that moment arrives, a refresh is required.
    expect(shouldRefreshToken(new Date(guestExpiry).toISOString(), now + waitMs)).toBe(true);
  });

  it("asks for an immediate refresh when the expiry cannot be read", () => {
    expect(millisecondsUntilRefresh("garbage", now)).toBe(0);
  });
});

describe("audio route and interruption handling", () => {
  it("reapplies the route when a Bluetooth device disappears", () => {
    expect(shouldReapplyAudioRoute("oldDeviceUnavailable")).toBe(true);
  });

  it("reapplies the route when a device is added or the category changes", () => {
    expect(shouldReapplyAudioRoute("newDeviceAvailable")).toBe(true);
    expect(shouldReapplyAudioRoute("categoryChange")).toBe(true);
    expect(shouldReapplyAudioRoute("noSuitableRouteForCategory")).toBe(true);
  });

  it("ignores route changes that do not affect output", () => {
    expect(shouldReapplyAudioRoute("override")).toBe(false);
    expect(shouldReapplyAudioRoute("unknown")).toBe(false);
    expect(shouldReapplyAudioRoute(undefined)).toBe(false);
  });

  it("only resumes after an interruption when iOS grants shouldResume", () => {
    expect(shouldResumeAfterInterruption({ shouldResume: true })).toBe(true);
    expect(shouldResumeAfterInterruption({ shouldResume: false })).toBe(false);
    expect(shouldResumeAfterInterruption(null)).toBe(false);
    expect(shouldResumeAfterInterruption(undefined)).toBe(false);
  });
});
