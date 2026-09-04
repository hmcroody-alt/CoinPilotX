/**
 * Stages 27, 28 and 30.
 *
 * The assertions worth reading are the negative ones. Most of what this module
 * does is arithmetic on lists; what it is *for* is making sure a comment stream
 * never gets shorter, a reaction never bursts twice, and an invite never
 * prompts twice. Each of those is a property over arbitrary input, so each is
 * tested by driving a sequence rather than by checking one call.
 */

import {
  acceptInviteEvent,
  canSendLiveReaction,
  dedupeInviteEvents,
  EMPTY_INVITE_INBOX,
  mergeLiveChat,
  mergeLiveReactions,
  reactionIsAudienceWide,
  stageChangeInvalidatesChat,
  type LiveInviteEvent,
  type LiveReactionEvent,
  type PendingLiveChatMessage
} from "../liveEventContinuity";
import type { PulseLiveChatMessage } from "../../api/live";

function msg(id: number, body = `m${id}`, userId = 7): PulseLiveChatMessage {
  return { id, body, user_id: userId, created_at: `2026-09-03T10:00:${String(id).padStart(2, "0")}Z` };
}

describe("Stage 27 — comments survive whatever happens to the stage", () => {
  it("keeps messages the server window has scrolled past", () => {
    const onScreen = [msg(1), msg(2), msg(3)];
    const window = [msg(2), msg(3), msg(4)];
    expect(mergeLiveChat(onScreen, window).map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("never shortens the stream, however the server answers", () => {
    let stream = [msg(1), msg(2), msg(3), msg(4)];
    for (const response of [[], [msg(4)], [msg(3), msg(4)], []]) {
      const next = mergeLiveChat(stream, response);
      expect(next.length).toBeGreaterThanOrEqual(stream.length);
      stream = next;
    }
    expect(stream.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("shows a message once when it arrives from two sources", () => {
    const merged = mergeLiveChat([msg(9)], [msg(9), msg(9)]);
    expect(merged.map((m) => m.id)).toEqual([9]);
  });

  it("prefers the newer copy of a message that has been edited or moderated", () => {
    const merged = mergeLiveChat([msg(5)], [{ ...msg(5), moderation_status: "hidden" }]);
    expect(merged).toHaveLength(1);
    expect(merged[0].moderation_status).toBe("hidden");
  });

  it("replaces an optimistic message with its server row rather than showing both", () => {
    const pending: PendingLiveChatMessage = {
      id: 0,
      body: "hello everyone",
      user_id: 42,
      pendingKey: "local-1"
    };
    const merged = mergeLiveChat([msg(1), pending], [msg(1), { id: 88, body: "hello everyone", user_id: 42 }]);
    expect(merged).toHaveLength(2);
    expect(merged.map((m) => m.id)).toEqual([1, 88]);
  });

  it("keeps an unconfirmed optimistic message at the end where its author expects it", () => {
    const pending: PendingLiveChatMessage = { id: 0, body: "typing", user_id: 42, pendingKey: "local-2" };
    const merged = mergeLiveChat([msg(1), pending], [msg(1), msg(2)]);
    expect(merged[merged.length - 1]).toMatchObject({ pendingKey: "local-2" });
  });

  it("does not confuse two different people saying the same thing", () => {
    const pending: PendingLiveChatMessage = { id: 0, body: "same", user_id: 42, pendingKey: "local-3" };
    const merged = mergeLiveChat([pending], [{ id: 90, body: "same", user_id: 43 }]);
    expect(merged).toHaveLength(2);
  });

  it("sorts by id so a poll landing out of order cannot scramble the column", () => {
    expect(mergeLiveChat([], [msg(4), msg(1), msg(3)]).map((m) => m.id)).toEqual([1, 3, 4]);
  });

  it("caps retention without dropping the newest messages", () => {
    const many = Array.from({ length: 20 }, (_, index) => msg(index + 1));
    const merged = mergeLiveChat([], many, { retention: 5 });
    expect(merged.map((m) => m.id)).toEqual([16, 17, 18, 19, 20]);
  });

  it("tolerates null holes in either list", () => {
    const merged = mergeLiveChat(
      [null as unknown as PulseLiveChatMessage, msg(1)],
      [msg(2), undefined as unknown as PulseLiveChatMessage]
    );
    expect(merged.map((m) => m.id)).toEqual([1, 2]);
  });

  it("states, as a rule, that a stage change does not invalidate the chat", () => {
    expect(stageChangeInvalidatesChat()).toBe(false);
  });
});

describe("Stage 28 — reactions belong to the room", () => {
  const heart = (id: number): LiveReactionEvent => ({ id, reactionType: "heart", userId: 5 });

  it("lets everyone react, on stage or not", () => {
    for (const role of ["host", "cohost", "guest", "audience"]) {
      for (const onStage of [true, false]) {
        expect(canSendLiveReaction({ role, onStage })).toBe(true);
      }
    }
    expect(reactionIsAudienceWide()).toBe(true);
  });

  it("only reports reactions it has not seen before as fresh", () => {
    const first = mergeLiveReactions([], [heart(1), heart(2)]);
    expect(first.fresh.map((r) => r.id)).toEqual([1, 2]);
    const second = mergeLiveReactions(first.window, [heart(1), heart(2), heart(3)]);
    expect(second.fresh.map((r) => r.id)).toEqual([3]);
  });

  it("bursts nothing at all when a poll returns the same reactions", () => {
    const state = mergeLiveReactions([], [heart(1), heart(2), heart(3)]);
    for (let poll = 0; poll < 5; poll += 1) {
      const next = mergeLiveReactions(state.window, [heart(1), heart(2), heart(3)]);
      expect(next.fresh).toHaveLength(0);
    }
  });

  it("deduplicates within a single batch", () => {
    expect(mergeLiveReactions([], [heart(4), heart(4)]).fresh).toHaveLength(1);
  });

  it("distinguishes a local reaction from the server row it becomes", () => {
    // Deliberate: a local burst has already been animated, so the server copy
    // arriving later is a new key and is not re-animated only because the
    // window is keyed on the server id once it exists. The window holding both
    // is acceptable; bursting twice would not be.
    const local: LiveReactionEvent = { localKey: "l1", reactionType: "fire" };
    const merged = mergeLiveReactions([local], [{ id: 77, reactionType: "fire" }]);
    expect(merged.fresh).toHaveLength(1);
  });

  it("bounds the window it keeps", () => {
    const many = Array.from({ length: 30 }, (_, index) => heart(index + 1));
    const merged = mergeLiveReactions([], many, { window: 10 });
    expect(merged.window).toHaveLength(10);
    expect(merged.window[merged.window.length - 1].id).toBe(30);
  });

  it("tolerates empty and malformed input", () => {
    expect(mergeLiveReactions([], []).fresh).toEqual([]);
    expect(mergeLiveReactions([], [null as unknown as LiveReactionEvent]).fresh).toEqual([]);
  });
});

describe("Stage 30 — one invite, however many times it is delivered", () => {
  const invite = (source: LiveInviteEvent["source"]): LiveInviteEvent => ({
    inviteId: "live-42-req-9",
    liveId: 42,
    requestId: 9,
    source
  });

  it("shows an invite the first time and never again", () => {
    const first = acceptInviteEvent(EMPTY_INVITE_INBOX, invite("push"));
    expect(first.show).toBe(true);
    expect(first.reason).toBe("new_invite");

    for (const source of ["realtime", "poll", "deeplink"] as const) {
      const next = acceptInviteEvent(first.inbox, invite(source));
      expect(next.show).toBe(false);
      expect(next.reason).toBe("already_handled");
    }
  });

  it("collapses a backlog delivered all at once", () => {
    const { show } = dedupeInviteEvents(EMPTY_INVITE_INBOX, [
      invite("push"),
      invite("realtime"),
      invite("poll")
    ]);
    expect(show).toHaveLength(1);
  });

  it("still shows a genuinely different invite", () => {
    const first = acceptInviteEvent(EMPTY_INVITE_INBOX, invite("push"));
    const other = acceptInviteEvent(first.inbox, { inviteId: "live-42-req-10", liveId: 42 });
    expect(other.show).toBe(true);
  });

  it("refuses an invite with no stable id rather than prompting forever", () => {
    expect(acceptInviteEvent(EMPTY_INVITE_INBOX, { inviteId: "", liveId: 42 }).reason).toBe("missing_invite_id");
    expect(acceptInviteEvent(EMPTY_INVITE_INBOX, null).show).toBe(false);
    expect(acceptInviteEvent(EMPTY_INVITE_INBOX, { inviteId: "x", liveId: 0 }).reason).toBe("missing_live_id");
  });

  it("marks an expired invite handled so a stale push stops being re-evaluated", () => {
    const now = Date.parse("2026-09-03T12:00:00Z");
    const stale: LiveInviteEvent = { inviteId: "live-1-req-1", liveId: 1, expiresAt: "2026-09-03T11:00:00Z" };
    const first = acceptInviteEvent(EMPTY_INVITE_INBOX, stale, { nowMs: now });
    expect(first.show).toBe(false);
    expect(first.reason).toBe("expired");
    expect(acceptInviteEvent(first.inbox, stale, { nowMs: now }).reason).toBe("already_handled");
  });

  it("shows an invite that has not expired yet", () => {
    const now = Date.parse("2026-09-03T12:00:00Z");
    const fresh: LiveInviteEvent = { inviteId: "live-1-req-2", liveId: 1, expiresAt: "2026-09-03T12:05:00Z" };
    expect(acceptInviteEvent(EMPTY_INVITE_INBOX, fresh, { nowMs: now }).show).toBe(true);
  });

  it("ignores an unparseable expiry rather than swallowing the invite", () => {
    const odd: LiveInviteEvent = { inviteId: "live-1-req-3", liveId: 1, expiresAt: "soon" };
    expect(acceptInviteEvent(EMPTY_INVITE_INBOX, odd).show).toBe(true);
  });

  it("does not mutate the inbox it was given", () => {
    const inbox = EMPTY_INVITE_INBOX;
    acceptInviteEvent(inbox, invite("push"));
    expect(inbox.handled).toHaveLength(0);
  });

  it("bounds how many invite ids it remembers", () => {
    let inbox = EMPTY_INVITE_INBOX;
    for (let index = 0; index < 200; index += 1) {
      inbox = acceptInviteEvent(inbox, { inviteId: `live-1-req-${index}`, liveId: 1 }).inbox;
    }
    expect(inbox.handled.length).toBeLessThanOrEqual(64);
    // The most recent invite is the one a duplicate is most likely to follow.
    expect(inbox.handled).toContain("live-1-req-199");
  });
});
