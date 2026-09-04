import {
  INITIAL_GUEST_JOIN_STATE,
  guestJoinReducer,
  guestWaitingStateKey,
  isInviteActionable,
  isTerminal,
  mergeLiveInvites,
  normalizeLiveInvite,
  shouldAppearOnStage,
  shouldPublish,
  shouldRunLocalPreview,
  toStagePhase,
  type GuestJoinEvent,
  type GuestJoinState
} from "../liveGuestStage";

function run(events: GuestJoinEvent[], from: GuestJoinState = INITIAL_GUEST_JOIN_STATE): GuestJoinState {
  return events.reduce(guestJoinReducer, from);
}

const TO_LIVE_VIA_INVITE: GuestJoinEvent[] = [
  { type: "invite_received" },
  { type: "invite_accepted" },
  { type: "preview_ready" },
  { type: "guest_confirmed" },
  { type: "rtc_joined" },
  { type: "media_flowing" }
];

describe("guest join state machine", () => {
  it("walks an invited guest all the way to live", () => {
    expect(run(TO_LIVE_VIA_INVITE).phase).toBe("live");
  });

  it("walks a requesting guest to live through the same preview step", () => {
    const state = run([
      { type: "request_sent" },
      { type: "request_approved" },
      { type: "preview_ready" },
      { type: "guest_confirmed" },
      { type: "media_flowing" }
    ]);
    expect(state.phase).toBe("live");
  });

  it("refuses to jump from invited straight to live", () => {
    // The illegal transition that would put someone on air without ever
    // showing them their own camera first.
    const state = run([{ type: "invite_received" }, { type: "media_flowing" }]);
    expect(state.phase).toBe("invited");
  });

  it("refuses to publish before the guest has confirmed their preview", () => {
    const state = run([{ type: "invite_received" }, { type: "invite_accepted" }, { type: "preview_ready" }]);
    expect(state.phase).toBe("preparing");
    expect(shouldRunLocalPreview(state)).toBe(true);
    expect(shouldPublish(state)).toBe(false);
  });

  it("runs the camera locally in preparing without publishing it", () => {
    const state = run([{ type: "invite_received" }, { type: "invite_accepted" }, { type: "preview_ready" }]);
    expect(shouldRunLocalPreview(state)).toBe(true);
    expect(shouldPublish(state)).toBe(false);
    expect(shouldAppearOnStage(state)).toBe(false);
  });

  it("does not render a stage tile while the guest is only connected", () => {
    // This is the empty black tile. Connected is not the same as visible.
    const state = run([
      { type: "invite_received" },
      { type: "invite_accepted" },
      { type: "preview_ready" },
      { type: "guest_confirmed" },
      { type: "rtc_joined" }
    ]);
    expect(state.phase).toBe("joining");
    expect(shouldPublish(state)).toBe(true);
    expect(shouldAppearOnStage(state)).toBe(false);
  });

  it("renders a stage tile only once media is flowing", () => {
    expect(shouldAppearOnStage(run(TO_LIVE_VIA_INVITE))).toBe(true);
  });

  it("drops a guest back to joining rather than leaving a black live tile", () => {
    const state = run([...TO_LIVE_VIA_INVITE, { type: "media_lost" }]);
    expect(state.phase).toBe("joining");
    expect(shouldAppearOnStage(state)).toBe(false);
    expect(shouldRunLocalPreview(state)).toBe(true);
  });

  it("ignores a duplicated realtime event instead of disturbing a live guest", () => {
    const live = run(TO_LIVE_VIA_INVITE);
    const again = guestJoinReducer(live, { type: "invite_accepted" });
    expect(again).toBe(live);
  });

  it("ignores a late-arriving invite push for a guest already on stage", () => {
    const live = run(TO_LIVE_VIA_INVITE);
    expect(guestJoinReducer(live, { type: "invite_received" }).phase).toBe("live");
  });

  it("lets the host remove a guest from any active phase", () => {
    for (const prefix of [
      [{ type: "invite_received" }] as GuestJoinEvent[],
      [{ type: "invite_received" }, { type: "invite_accepted" }] as GuestJoinEvent[],
      TO_LIVE_VIA_INVITE
    ]) {
      const state = run([...prefix, { type: "removed_by_host" }]);
      expect(state.phase).toBe("removed");
      expect(isTerminal(state)).toBe(true);
      expect(shouldPublish(state)).toBe(false);
      expect(shouldRunLocalPreview(state)).toBe(false);
    }
  });

  it("stops the camera the moment a guest leaves the stage", () => {
    const state = run([...TO_LIVE_VIA_INVITE, { type: "left_stage" }]);
    expect(state.phase).toBe("left");
    expect(shouldRunLocalPreview(state)).toBe(false);
    expect(shouldPublish(state)).toBe(false);
  });

  it("does not resurrect a declined invite", () => {
    const declined = run([{ type: "invite_received" }, { type: "invite_declined" }]);
    expect(declined.phase).toBe("declined");
    expect(run([{ type: "invite_accepted" }], declined).phase).toBe("declined");
  });

  it("carries a failure reason and refuses to publish", () => {
    const state = run([
      { type: "invite_received" },
      { type: "invite_accepted" },
      { type: "failed", reason: "camera permission denied" }
    ]);
    expect(state.phase).toBe("failed");
    expect(state.error).toBe("camera permission denied");
    expect(shouldPublish(state)).toBe(false);
  });

  it("resets back to idle", () => {
    expect(guestJoinReducer(run(TO_LIVE_VIA_INVITE), { type: "reset" })).toEqual(INITIAL_GUEST_JOIN_STATE);
  });

  it("speaks the registry's phase vocabulary", () => {
    expect(toStagePhase("preparing")).toBe("preparing");
    expect(toStagePhase("joining")).toBe("joining");
    expect(toStagePhase("live")).toBe("live");
    expect(toStagePhase("requested")).toBe("accepted");
    expect(toStagePhase("removed")).toBe("left");
  });

  it("names a distinct waiting state for every phase", () => {
    const keys = new Set(
      (
        [
          "idle",
          "requested",
          "invited",
          "accepted",
          "preparing",
          "joining",
          "live",
          "declined",
          "removed",
          "left",
          "failed"
        ] as const
      ).map((phase) => guestWaitingStateKey({ phase, inviteId: "", error: "" }))
    );
    // A guest waiting must always be told which of the states they are in.
    expect(keys.size).toBe(11);
  });
});

describe("invite normalisation and deduplication", () => {
  const base = {
    invite_id: "inv-77-901",
    live_id: 77,
    id: 901,
    user_id: 42,
    invited_by: 7,
    inviter_name: "Ada",
    display_name: "Grace",
    status: "invited",
    expires_at: "2099-01-01T00:00:00"
  };

  it("rejects an invite with no stable id", () => {
    // Without an id the same invite would be shown twice.
    expect(normalizeLiveInvite({ ...base, invite_id: "" })).toBeNull();
  });

  it("rejects an invite with no target", () => {
    expect(normalizeLiveInvite({ ...base, user_id: 0 })).toBeNull();
  });

  it("shows one prompt when the same invite arrives from push, realtime and polling", () => {
    const merged = mergeLiveInvites([base], [{ ...base }], [{ ...base }]);
    expect(merged).toHaveLength(1);
    expect(merged[0].inviteId).toBe("inv-77-901");
  });

  it("keeps two genuinely different invites apart", () => {
    expect(mergeLiveInvites([base, { ...base, invite_id: "inv-77-902", id: 902 }])).toHaveLength(2);
  });

  it("drops invites the server already marked expired", () => {
    expect(mergeLiveInvites([{ ...base, expired: true }])).toHaveLength(0);
  });

  it("drops invites that were already answered", () => {
    expect(mergeLiveInvites([{ ...base, status: "declined" }])).toHaveLength(0);
  });

  it("treats a passed expiry as not actionable", () => {
    const invite = normalizeLiveInvite({ ...base, expires_at: "2020-01-01T00:00:00" });
    expect(isInviteActionable(invite, new Date("2026-01-01T00:00:00Z"))).toBe(false);
  });

  it("treats a future expiry as actionable", () => {
    const invite = normalizeLiveInvite(base);
    expect(isInviteActionable(invite, new Date("2026-01-01T00:00:00Z"))).toBe(true);
  });

  it("treats an unparseable expiry as not actionable rather than eternal", () => {
    const invite = normalizeLiveInvite({ ...base, expires_at: "whenever" });
    expect(isInviteActionable(invite)).toBe(false);
  });
});
