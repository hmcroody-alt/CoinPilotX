/**
 * Tests for the shared UnreadCountStore — the ONE source of the header bell's
 * unread number. Pinned:
 *
 * 1. The bell counts NOTIFICATION unreads and EXCLUDES messages (messages are
 *    badged separately); messageCount and totalCount are exposed alongside.
 * 2. set/get publishes an authoritative count and only notifies subscribers when
 *    the derived numbers actually change.
 * 3. applyOptimisticRead zeroes the bell instantly, scoped so a message-only
 *    read leaves the bell alone and vice-versa.
 */

import {
  __resetUnreadCounts,
  applyOptimisticRead,
  getUnreadSnapshot,
  setUnreadCounts,
  subscribeUnread
} from "../unreadCounts";

beforeEach(() => {
  __resetUnreadCounts();
});

describe("bell scope", () => {
  it("counts notification unreads and excludes messages from the bell", () => {
    const snap = setUnreadCounts({
      alert_unread_count: 4,
      chat_unread_count: 7,
      total_unread_count: 11
    });
    expect(snap.bellCount).toBe(4); // messages excluded
    expect(snap.messageCount).toBe(7);
    expect(snap.totalCount).toBe(11);
  });
});

describe("publish + notify", () => {
  it("notifies subscribers when the numbers change, and skips when they don't", () => {
    let hits = 0;
    const off = subscribeUnread(() => {
      hits += 1;
    });
    setUnreadCounts({ alert_unread_count: 2 });
    expect(getUnreadSnapshot().bellCount).toBe(2);
    expect(hits).toBe(1);

    // same derived numbers → no extra notify
    setUnreadCounts({ alert_unread_count: 2 });
    expect(hits).toBe(1);

    setUnreadCounts({ alert_unread_count: 5 });
    expect(hits).toBe(2);
    off();
  });
});

describe("optimistic read", () => {
  it("zeroes the bell but leaves messages when scoped to notifications", () => {
    setUnreadCounts({ alert_unread_count: 4, chat_unread_count: 7 });
    const snap = applyOptimisticRead("notifications");
    expect(snap.bellCount).toBe(0);
    expect(snap.messageCount).toBe(7);
  });

  it("zeroes messages but leaves the bell when scoped to messages", () => {
    setUnreadCounts({ alert_unread_count: 4, chat_unread_count: 7 });
    const snap = applyOptimisticRead("messages");
    expect(snap.bellCount).toBe(4);
    expect(snap.messageCount).toBe(0);
  });
});
