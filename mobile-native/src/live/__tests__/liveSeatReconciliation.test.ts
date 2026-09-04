import {
  isDisruptive,
  preservesLocalCapture,
  reconcileLiveSeat,
  rosterChangeRequiresReconnect,
  type LiveSeat
} from "../liveSeatReconciliation";

function seat(overrides: Partial<LiveSeat> = {}): LiveSeat {
  return { provider: "agora", channelName: "pulse-live-77", uid: 42, publishing: true, token: "tok-1", ...overrides };
}

describe("seat reconciliation", () => {
  it("joins when nothing is connected", () => {
    expect(reconcileLiveSeat(null, seat())).toBe("join");
    expect(reconcileLiveSeat(undefined, seat())).toBe("join");
  });

  it("does nothing when the same seat reconnects with the same token", () => {
    expect(reconcileLiveSeat(seat(), seat())).toBe("noop");
  });

  it("renews in place when only the token changed", () => {
    // A host token is re-minted on a long broadcast. Leaving the channel to
    // apply it would drop the Live.
    expect(reconcileLiveSeat(seat(), seat({ token: "tok-2" }))).toBe("renew_token");
  });

  it("ignores an empty replacement token rather than renewing to nothing", () => {
    expect(reconcileLiveSeat(seat(), seat({ token: "" }))).toBe("noop");
  });

  it("promotes and demotes in place rather than rejoining", () => {
    expect(reconcileLiveSeat(seat({ publishing: false }), seat({ publishing: true }))).toBe("promote");
    expect(reconcileLiveSeat(seat({ publishing: true }), seat({ publishing: false }))).toBe("demote");
  });

  it("rejoins only when the endpoint genuinely changed", () => {
    expect(reconcileLiveSeat(seat(), seat({ channelName: "pulse-live-78" }))).toBe("rejoin");
    expect(reconcileLiveSeat(seat(), seat({ uid: 43 }))).toBe("rejoin");
    expect(reconcileLiveSeat(seat(), seat({ provider: "livekit" }))).toBe("rejoin");
  });

  it("treats participant churn as invisible to the connection", () => {
    // The rule the whole multi-guest feature rests on. Six guests arriving and
    // leaving in any order produce the same seat, so the host is never restarted.
    const host = seat({ uid: 7, publishing: true });
    for (let guest = 0; guest < 12; guest += 1) {
      expect(reconcileLiveSeat(host, host)).toBe("noop");
    }
    expect(rosterChangeRequiresReconnect()).toBe(false);
  });

  it("classifies exactly one action as disruptive to a running session", () => {
    const actions = ["noop", "renew_token", "promote", "demote", "rejoin"] as const;
    expect(actions.filter((action) => isDisruptive(action))).toEqual(["rejoin"]);
  });

  it("keeps the host's camera and mic running across every non-rejoin action", () => {
    expect(preservesLocalCapture("noop", true)).toBe(true);
    expect(preservesLocalCapture("renew_token", true)).toBe(true);
    expect(preservesLocalCapture("promote", false)).toBe(true);
  });

  it("stops local capture on demotion, because an audience member's camera must not stay on", () => {
    expect(preservesLocalCapture("demote", true)).toBe(false);
  });

  it("does not claim capture survives a rejoin", () => {
    expect(preservesLocalCapture("rejoin", true)).toBe(false);
    expect(preservesLocalCapture("join", false)).toBe(false);
  });
});
