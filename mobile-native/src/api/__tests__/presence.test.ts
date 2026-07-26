const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  formatLastSeen,
  isPresenceOnline,
  isPresenceActive,
  normalizePresence,
  offlinePresence,
  presenceActivityText,
  presenceStatusLabel,
  queryPresence
} from "../presence";

describe("presence normalization defaults to offline", () => {
  it("collapses junk/partial payloads to a fully offline record", () => {
    const junk: unknown[] = [
      null,
      undefined,
      42,
      "online",
      [],
      {},
      { status: "banana" },
      // Legacy tokens that used to light an indicator anywhere they appeared.
      // They are not part of the server's vocabulary and must not be honoured.
      { status: "active" },
      { status: "available" },
      { status: "live" },
      { status: "ONLINE" },
      { status: 1 },
      { status: {} }
    ];
    for (const raw of junk) {
      const p = normalizePresence(raw);
      expect(p.online).toBe(false);
      expect(p.status).toBe("offline");
      expect(p.activity).toBe("idle");
      expect(isPresenceOnline(p)).toBe(false);
    }
  });

  it("requires BOTH a recognised status and the online flag before showing anyone online", () => {
    // A payload carrying `online: true` with no status at all is malformed --
    // the server always sends both. Honouring the flag on its own produced a
    // record whose avatar said online while its own status field said offline,
    // and that internal contradiction always resolved in favour of the green
    // dot. An ambiguous payload must resolve to offline instead: showing a
    // reachable user as offline is a cosmetic miss, while showing an
    // unreachable user as online is invisible to whoever is looking at it.
    const noStatus = normalizePresence({ user_id: 1, online: true });
    expect(noStatus.online).toBe(false);
    expect(noStatus.status).toBe("offline");
    expect(isPresenceOnline(noStatus)).toBe(false);

    const junkStatus = normalizePresence({ user_id: 1, online: true, status: "active" });
    expect(junkStatus.online).toBe(false);

    // The well-formed payload is unaffected.
    expect(normalizePresence({ user_id: 1, online: true, status: "online" }).online).toBe(true);
  });

  it("trusts an explicit online boolean from the server", () => {
    const p = normalizePresence({ user_id: 7, online: true, status: "online", activity: "typing" });
    expect(p.user_id).toBe(7);
    expect(p.online).toBe(true);
    expect(p.status).toBe("online");
    expect(p.activity).toBe("typing");
  });

  it("never lets status='online' override online=false", () => {
    const p = normalizePresence({ user_id: 3, online: false, status: "online", activity: "typing" });
    expect(p.online).toBe(false);
    expect(p.status).toBe("offline");
    // Activity is suppressed for an offline user so no stale "Typing…" leaks out.
    expect(p.activity).toBe("idle");
  });

  it("derives online strictly from a confirmed non-offline status when the flag is absent", () => {
    expect(normalizePresence({ user_id: 1, status: "online" }).online).toBe(true);
    expect(normalizePresence({ user_id: 1, status: "away" }).online).toBe(true);
    expect(normalizePresence({ user_id: 1, status: "offline" }).online).toBe(false);
    expect(normalizePresence({ user_id: 1 }).online).toBe(false);
  });

  it("only exposes self-only privacy fields when self is true", () => {
    const other = normalizePresence({ user_id: 2, online: true, status: "online", invisible: true, hide_last_seen: true });
    expect(other.invisible).toBeUndefined();
    expect(other.hide_last_seen).toBeUndefined();
    const self = normalizePresence({ user_id: 2, online: true, status: "online", self: true, invisible: true, hide_last_seen: true });
    expect(self.invisible).toBe(true);
    expect(self.hide_last_seen).toBe(true);
  });
});

describe("presence predicates never assume online", () => {
  it("isPresenceOnline is strictly the normalized server state", () => {
    expect(isPresenceOnline(null)).toBe(false);
    expect(isPresenceOnline(undefined)).toBe(false);
    expect(isPresenceOnline(offlinePresence(9))).toBe(false);
    expect(isPresenceOnline(normalizePresence({ user_id: 9, online: true, status: "online" }))).toBe(true);
  });

  it("isPresenceActive is an alias of isPresenceOnline", () => {
    expect(isPresenceActive(offlinePresence(1))).toBe(false);
    expect(isPresenceActive(normalizePresence({ user_id: 1, online: true, status: "online" }))).toBe(true);
    // Both predicates must agree on every input; two predicates that can
    // disagree is how one screen shows a dot and the next one does not.
    for (const raw of [null, {}, { status: "active" }, { online: true }, { status: "online", online: true }]) {
      const p = normalizePresence(raw);
      expect(isPresenceActive(p)).toBe(isPresenceOnline(p));
    }
  });
});

describe("activity is never shown for a user who is not online", () => {
  it("clears a stale activity at the normalizer, not at the label", () => {
    // A "typing" flag riding along with an offline user is the stuck-indicator
    // bug in a different costume. Clearing it here means no downstream renderer
    // has to remember to check.
    for (const activity of ["typing", "recording_voice", "uploading_media", "in_video_call"]) {
      const p = normalizePresence({ user_id: 1, online: false, status: "offline", activity });
      expect(p.activity).toBe("idle");
      expect(presenceActivityText(p.activity)).toBe("");
    }
  });

  it("uses the spec's wording for the Live activities", () => {
    expect(presenceActivityText("live_hosting")).toBe("Hosting Live");
    expect(presenceActivityText("live_guest")).toBe("Guest in Live");
    expect(presenceActivityText("live_watching")).toBe("Watching Live");
  });

  it("returns empty for an unrecognised activity rather than guessing", () => {
    expect(presenceActivityText("juggling")).toBe("");
  });
});

describe("presence display labels", () => {
  it("maps activities to human text and idle to empty", () => {
    expect(presenceActivityText("typing")).toBe("Typing…");
    expect(presenceActivityText("in_video_call")).toBe("In video call");
    expect(presenceActivityText("idle")).toBe("");
    expect(presenceActivityText(undefined)).toBe("");
  });

  it("prioritises live activity over plain online", () => {
    const p = normalizePresence({ user_id: 1, online: true, status: "online", activity: "recording_voice" });
    expect(presenceStatusLabel(p)).toBe("Recording voice…");
  });

  it("shows Away for an away user without activity", () => {
    const p = normalizePresence({ user_id: 1, online: true, status: "away" });
    expect(presenceStatusLabel(p)).toBe("Away");
  });

  it("never fabricates Online for an offline user with no last-seen", () => {
    expect(presenceStatusLabel(offlinePresence(1))).toBe("Offline");
    expect(presenceStatusLabel(null)).toBe("Offline");
  });

  it("falls back to the server last_seen_text for an offline user", () => {
    const p = normalizePresence({ user_id: 1, online: false, status: "offline", last_seen_text: "Last seen recently" });
    expect(presenceStatusLabel(p)).toBe("Last seen recently");
  });
});

describe("formatLastSeen", () => {
  const now = new Date("2026-07-25T12:00:00Z");

  it("returns 'just now' under a minute", () => {
    const p = normalizePresence({ user_id: 1, last_seen_at: "2026-07-25T11:59:30Z" });
    expect(formatLastSeen(p, { now })).toBe("Last seen just now");
  });

  it("buckets minutes and hours", () => {
    const mins = normalizePresence({ user_id: 1, last_seen_at: "2026-07-25T11:45:00Z" });
    expect(formatLastSeen(mins, { now })).toBe("Last seen 15 minutes ago");
    const hrs = normalizePresence({ user_id: 1, last_seen_at: "2026-07-25T09:00:00Z" });
    expect(formatLastSeen(hrs, { now })).toBe("Last seen 3 hours ago");
  });

  it("renders locale-aware wall-clock time for older timestamps (24h locale omits AM/PM)", () => {
    const older = normalizePresence({ user_id: 1, last_seen_at: "2026-07-23T20:00:00Z" });
    const en = formatLastSeen(older, { now, locale: "en-US" });
    expect(en.startsWith("Last seen")).toBe(true);
    expect(/AM|PM/.test(en)).toBe(true);
    const fr = formatLastSeen(older, { now, locale: "fr-FR" });
    expect(fr).not.toMatch(/AM|PM/);
  });

  it("falls back to last_seen_text when the raw timestamp is missing or unparseable", () => {
    expect(formatLastSeen(normalizePresence({ user_id: 1, last_seen_text: "a while ago" }), { now })).toBe("a while ago");
    expect(
      formatLastSeen(normalizePresence({ user_id: 1, last_seen_at: "not-a-date", last_seen_text: "a while ago" }), { now })
    ).toBe("a while ago");
  });
});

describe("queryPresence fills unreturned ids with offline, never online", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("returns an empty map for no positive ids without hitting the network", async () => {
    const out = await queryPresence([0, -1]);
    expect(out.size).toBe(0);
    expect(mockPulseApi).not.toHaveBeenCalled();
  });

  it("maps returned users and backfills the rest as offline", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      presence: [{ user_id: 10, online: true, status: "online" }]
    });
    const out = await queryPresence([10, 11]);
    expect(isPresenceOnline(out.get(10))).toBe(true);
    expect(isPresenceOnline(out.get(11))).toBe(false);
    expect(out.get(11)?.status).toBe("offline");
  });
});
